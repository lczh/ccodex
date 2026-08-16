#!/usr/bin/env python3
"""The /file preview endpoint (the user 2026-07-08): chat path-thumbnails
load real bytes from `GET /file?path=…`, existence- and extension-gated.

Drives the REAL Handler over HTTP (the test_kernel_ws_auth.py pattern). Synthetic only — temp files,
no session state touched.
"""
import os
import tempfile
import threading
import unittest
from unittest import mock
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
SourceFileLoader("romp_event_model", os.path.join(BIN, "romp-event-model")).load_module()
SourceFileLoader("romp_judge", os.path.join(BIN, "romp-judge")).load_module()
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
km = SourceFileLoader("romp_kernel", os.path.join(BIN, "romp-kernel")).load_module()

TOKEN = os.environ["ROMP_SERVE_TOKEN"]

# a 1x1 transparent PNG — real image bytes so the mime/type path is exercised end-to-end
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c626001000000ffff03000006000557bfabd40000000049454e44ae426082")


class FilePreviewEndpoint(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        cls.t = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.t.start()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.png = os.path.join(cls.tmp.name, "plot.png")
        with open(cls.png, "wb") as f:
            f.write(PNG)
        cls.txt = os.path.join(cls.tmp.name, "notes.txt")
        with open(cls.txt, "w") as f:
            f.write("not renderable")
        # …and something served NEITHER as media nor as text, which is what "off the allowlist" means
        # now that source/text is ON it (2026-08-08 — see tests/test_file_view.py)
        cls.bin = os.path.join(cls.tmp.name, "archive.zip")
        with open(cls.bin, "wb") as f:
            f.write(b"PK\x03\x04not renderable, not text")

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.tmp.cleanup()

    def _req(self, path, method="GET"):
        url = "http://127.0.0.1:%d%s%stoken=%s" % (self.port, path, "&" if "?" in path else "?", TOKEN)
        req = urllib.request.Request(url, method=method)
        try:
            with urllib.request.urlopen(req, timeout=3) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def test_serves_an_existing_image_with_its_mime(self):
        code, hdrs, body = self._req("/file?path=" + urllib.parse.quote(self.png))
        self.assertEqual(code, 200)
        self.assertEqual(hdrs.get("Content-Type"), "image/png")
        self.assertEqual(body, PNG)

    def _req_range(self, path, rng):
        url = "http://127.0.0.1:%d%s&token=%s" % (self.port, path, TOKEN)
        req = urllib.request.Request(url, headers={"Range": rng})
        try:
            with urllib.request.urlopen(req, timeout=3) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def test_a_suffix_range_resumes_mid_file(self):
        # the resumable preview retry (the user 2026-08-16, flaky wifi): bytes already received are
        # never re-sent — the client asks for the rest and stitches the picture across attempts
        code, hdrs, body = self._req_range("/file?path=" + urllib.parse.quote(self.png), "bytes=10-")
        self.assertEqual(code, 206)
        self.assertEqual(body, PNG[10:])
        self.assertEqual(hdrs.get("Content-Range"), "bytes 10-%d/%d" % (len(PNG) - 1, len(PNG)))
        self.assertEqual(hdrs.get("Content-Type"), "image/png")

    def test_a_range_past_the_end_416s_so_the_client_restarts(self):
        code, _, _ = self._req_range("/file?path=" + urllib.parse.quote(self.png), "bytes=%d-" % (len(PNG) + 5))
        self.assertEqual(code, 416)

    def test_a_non_suffix_range_is_served_whole(self):
        # only the one suffix form is honored; anything else means a plain 200 the client treats as a restart
        code, _, body = self._req_range("/file?path=" + urllib.parse.quote(self.png), "bytes=0-5")
        self.assertEqual(code, 200)
        self.assertEqual(body, PNG)

    def test_text_ignores_range(self):
        # the viewer slurps text; a range on it is served whole
        code, _, body = self._req_range("/file?path=" + urllib.parse.quote(self.txt), "bytes=3-")
        self.assertEqual(code, 200)
        self.assertEqual(body, b"not renderable")

    def test_missing_file_404s(self):
        code, _, _ = self._req("/file?path=" + urllib.parse.quote(os.path.join(self.tmp.name, "gone.png")))
        self.assertEqual(code, 404)

    def test_an_extension_on_neither_allowlist_415s_as_exists_but_unviewable(self):
        # the VIEW allowlist is renderable media PLUS source/text; a .zip is neither — but it EXISTS,
        # which is a different truth from 404's "no such file", and the client acts on the difference
        # (415 → offer the ?download=1 the route serves; 404 → nothing to offer). The user 2026-08-09.
        code, _, _ = self._req("/file?path=" + urllib.parse.quote(self.bin))
        self.assertEqual(code, 415)

    def test_text_is_served_so_a_remote_dashboard_can_actually_show_it(self):
        # widened 2026-08-08: a file link is only followable if the bytes reach the browser, since the
        # kernel-side opener draws on the kernel's screen — the wrong machine when you are remote
        code, hdrs, body = self._req("/file?path=" + urllib.parse.quote(self.txt))
        self.assertEqual(code, 200)
        self.assertTrue(hdrs.get("Content-Type", "").startswith("text/plain"), hdrs.get("Content-Type"))
        self.assertEqual(body, b"not renderable")

    def test_relative_path_without_sid_404s(self):
        # unresolvable relative path (no session cwd) must not fall back to the kernel's own cwd
        code, _, _ = self._req("/file?path=plot.png")
        self.assertEqual(code, 404)

    def test_oversize_413s_rather_than_truncating(self):
        old = km._PREVIEW_MAX_BYTES
        km._PREVIEW_MAX_BYTES = len(PNG) - 1
        try:
            code, _, _ = self._req("/file?path=" + urllib.parse.quote(self.png))
            self.assertEqual(code, 413)
        finally:
            km._PREVIEW_MAX_BYTES = old

    def test_head_probe_reports_existence_without_the_bytes(self):
        # the client's PDF-chip probe: headers only (real Content-Length), no body download
        code, hdrs, body = self._req("/file?path=" + urllib.parse.quote(self.png), method="HEAD")
        self.assertEqual(code, 200)
        self.assertEqual(hdrs.get("Content-Length"), str(len(PNG)))
        self.assertEqual(body, b"")
        code, _, _ = self._req("/file?path=" + urllib.parse.quote(self.bin), method="HEAD")
        self.assertEqual(code, 415, "HEAD carries the same exists-but-unviewable verdict as GET")


class FileDownloadEndpoint(unittest.TestCase):
    """/file?download=1 (the user 2026-08-09): anything on disk is downloadable — the view allowlists
    are a rendering choice, not a security boundary (the OS still guards execution, and the dashboard's
    owner can already read any file through an agent). Served as an attachment the browser saves, never
    interprets, and STREAMED so a multi-GB file cannot OOM the kernel that self-hosts the sessions."""

    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.tmp = tempfile.TemporaryDirectory()
        # NUL bytes up front: off both view allowlists AND would fail the text sniff — the exact
        # kind of file the download path exists for
        cls.blob = os.path.join(cls.tmp.name, "model.bin")
        cls.blob_bytes = b"\x00\x7fELF binary-ish payload\x00" * 40
        with open(cls.blob, "wb") as f:
            f.write(cls.blob_bytes)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.tmp.cleanup()

    def _req(self, path, method="GET"):
        url = "http://127.0.0.1:%d%s%stoken=%s" % (self.port, path, "&" if "?" in path else "?", TOKEN)
        req = urllib.request.Request(url, method=method)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def test_an_off_allowlist_binary_downloads_as_an_attachment(self):
        code, hdrs, body = self._req("/file?path=" + urllib.parse.quote(self.blob) + "&download=1")
        self.assertEqual(code, 200)
        self.assertEqual(body, self.blob_bytes)
        self.assertEqual(hdrs.get("Content-Type"), "application/octet-stream")
        self.assertEqual(hdrs.get("Content-Disposition"), 'attachment; filename="model.bin"')
        self.assertEqual(hdrs.get("Content-Length"), str(len(self.blob_bytes)))
        self.assertEqual(hdrs.get("X-Content-Type-Options"), "nosniff")

    def test_the_view_path_is_unchanged_without_the_param(self):
        # same file, no download=1 → the exists-but-unviewable 415, exactly as before this route
        code, _, _ = self._req("/file?path=" + urllib.parse.quote(self.blob))
        self.assertEqual(code, 415)

    def test_a_missing_file_still_404s_naming_the_path(self):
        gone = os.path.join(self.tmp.name, "gone.bin")
        code, _, body = self._req("/file?path=" + urllib.parse.quote(gone) + "&download=1")
        self.assertEqual(code, 404)
        self.assertIn(km._tilde(gone).encode(), body, "every /file error names the resolved path")

    def test_the_text_cap_gates_the_view_but_not_the_download(self):
        big = os.path.join(self.tmp.name, "huge.log")
        data = b"x" * (km._TEXT_MAX_BYTES + 1)
        with open(big, "wb") as f:
            f.write(data)
        code, _, _ = self._req("/file?path=" + urllib.parse.quote(big))
        self.assertEqual(code, 413, "the VIEW keeps its cap")
        code, hdrs, body = self._req("/file?path=" + urllib.parse.quote(big) + "&download=1")
        self.assertEqual(code, 200, "the download has none")
        self.assertEqual(len(body), len(data))
        self.assertEqual(hdrs.get("Content-Length"), str(len(data)))

    def test_head_reports_the_attachment_without_the_bytes(self):
        code, hdrs, body = self._req("/file?path=" + urllib.parse.quote(self.blob) + "&download=1",
                                     method="HEAD")
        self.assertEqual(code, 200)
        self.assertEqual(body, b"")
        self.assertEqual(hdrs.get("Content-Disposition"), 'attachment; filename="model.bin"')
        self.assertEqual(hdrs.get("Content-Length"), str(len(self.blob_bytes)))


class _StreamSink:
    def __init__(self, sink):
        self.sink = sink

    def write(self, b):
        self.sink.append(bytes(b))


class _DownloadRecorder:
    """Just enough Handler surface for a direct _file_download call: records every wfile.write, so the
    test can see the CHUNKING itself — the over-HTTP tests above only see the reassembled body."""

    def __init__(self):
        self.writes, self.headers, self.status = [], {}, None
        self.close_connection = False
        self.wfile = _StreamSink(self.writes)

    def send_response(self, code):
        self.status = code

    def send_header(self, k, v):
        self.headers[k] = v

    def end_headers(self):
        pass

    _send = km.Handler._send      # the 404 path routes through the real _send, captured by the fakes


class DownloadStreams(unittest.TestCase):
    """The body is streamed in fixed chunks, never slurped. _send reads whole files into memory, and a
    multi-GB download through it would OOM the kernel — which self-hosts the very sessions using it.
    This pins the chunked WRITES, not just the reassembled bytes."""

    def test_the_body_arrives_as_bounded_chunks_never_one_slurp(self):
        with tempfile.TemporaryDirectory() as tmp:
            fp = os.path.join(tmp, "big.dat")
            data = bytes(range(256)) * ((3 * km._DOWNLOAD_CHUNK) // 256) + b"tail"
            with open(fp, "wb") as f:
                f.write(data)
            rec = _DownloadRecorder()
            km.Handler._file_download(rec, fp)
        self.assertEqual(rec.status, 200)
        self.assertGreater(len(rec.writes), 1, "one write means the file was slurped")
        self.assertTrue(all(len(w) <= km._DOWNLOAD_CHUNK for w in rec.writes),
                        "every write is bounded by the chunk size, whatever the file size")
        self.assertEqual(b"".join(rec.writes), data, "…and the chunks reassemble to the exact file")
        self.assertEqual(rec.headers.get("Content-Length"), str(len(data)))

    def test_a_file_truncated_mid_stream_closes_rather_than_hanging(self):
        # the promised Content-Length can no longer be honored → close, a visibly failed download
        rec = _DownloadRecorder()
        with tempfile.TemporaryDirectory() as tmp:
            fp = os.path.join(tmp, "shrinks.dat")
            with open(fp, "wb") as f:
                f.write(b"x" * 64)
            real_getsize = os.path.getsize
            with mock.patch.object(km.os.path, "getsize", lambda p: real_getsize(p) + 1000):
                km.Handler._file_download(rec, fp)
        self.assertTrue(rec.close_connection, "short bytes must close the connection, not hang it")


class AttachmentDisposition(unittest.TestCase):
    """The basename lands inside a header's quoted-string, so header-hostile characters are replaced;
    a mangled name also rides the RFC 5987 filename* form so capable browsers save the real one."""

    def test_a_plain_name_passes_through(self):
        self.assertEqual(km._attachment_disposition("model.bin"), 'attachment; filename="model.bin"')

    def test_header_hostile_characters_cannot_escape_the_quoted_string(self):
        d = km._attachment_disposition('a"b\\c\r\nSet-Cookie: x=y.bin')
        header_value = d.split("filename=")[1]
        self.assertNotIn("\r", d)
        self.assertNotIn("\n", d)
        self.assertNotIn('"', header_value[1:].split('"')[0], "no quote survives inside the quotes")
        self.assertIn('filename="a_b_c__Set-Cookie: x=y.bin"', d)

    def test_a_non_ascii_name_keeps_its_real_form_in_filename_star(self):
        d = km._attachment_disposition("données.csv")
        self.assertIn('filename="donn_es.csv"', d, "the ASCII fallback is mangled but present")
        self.assertIn("filename*=UTF-8''donn%C3%A9es.csv", d, "the real name rides RFC 5987")

    def test_an_empty_name_still_yields_a_usable_filename(self):
        self.assertIn('filename="download"', km._attachment_disposition(""))


if __name__ == "__main__":
    unittest.main()
