#!/usr/bin/env python3
"""POST /send body parsing — the human->agent input channel the Obsidian track-changes
plugin posts to. The kernel then injects the text via _tmux_send (the same delivery the
chat composer's WS sendMessage uses), so the plugin never touches tmux itself.
"""
import os
import unittest
from unittest import mock
from importlib.machinery import SourceFileLoader
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = SourceFileLoader("romp_kernel_send", os.path.join(BIN, "romp-kernel")).load_module()


class ParseSendBody(unittest.TestCase):
    def test_id_and_text(self):
        self.assertEqual(km._parse_send_body(b'{"id":"alpha","text":"hi"}'), {"who": "alpha", "text": "hi"})

    def test_name_is_accepted_as_who(self):
        self.assertEqual(km._parse_send_body(b'{"name":"beta","text":"yo"}'), {"who": "beta", "text": "yo"})

    def test_rejects_missing_or_empty(self):
        self.assertIsNone(km._parse_send_body(b'{"id":"alpha"}'))           # no text
        self.assertIsNone(km._parse_send_body(b'{"text":"hi"}'))            # no id/name
        self.assertIsNone(km._parse_send_body(b'{"id":"alpha","text":""}'))  # empty text
        self.assertIsNone(km._parse_send_body(b'{"id":"","text":"hi"}'))    # empty id

    def test_tag_appends_the_render_hint_marker(self):
        # scheduled/scripted senders (the user 2026-08-18, the nightly optimizer briefing):
        # the "tag" field makes the kernel append the SAME marker `romp send --tag` writes,
        # so the chat dresses the message machine-sent under that label
        self.assertEqual(km._parse_send_body(b'{"id":"alpha","text":"hi","tag":"nightly-optimizer"}'),
                         {"who": "alpha", "text": "hi\n\n<!-- romp-tag: nightly-optimizer -->"})

    def test_bad_tag_fails_the_whole_parse(self):
        # a malformed tag is a 400, never a silent plain delivery — delivering anyway would
        # misattribute the text (fail loudly, 2026-07-03)
        self.assertIsNone(km._parse_send_body(b'{"id":"a","text":"hi","tag":"two words"}'))
        self.assertIsNone(km._parse_send_body(b'{"id":"a","text":"hi","tag":""}'))
        self.assertIsNone(km._parse_send_body(b'{"id":"a","text":"hi","tag":123}'))
        self.assertIsNone(km._parse_send_body(b'{"id":"a","text":"hi","tag":"-leading-dash"}'))
        self.assertIsNone(km._parse_send_body(b'{"id":"a","text":"hi","tag":"' + b"x" * 25 + b'"}'))

    def test_rejects_bad_json_non_object_and_non_string_text(self):
        self.assertIsNone(km._parse_send_body(b'not json'))
        self.assertIsNone(km._parse_send_body(b'[1,2,3]'))
        self.assertIsNone(km._parse_send_body(b''))
        self.assertIsNone(km._parse_send_body(b'{"id":"a","text":123}'))


class SessionList(unittest.TestCase):
    """GET /sessions — the UNIFIED (tmux + SDK) romp session list external tools read (the Obsidian Cmd+M
    picker + diff chips, the postal bus) instead of shelling tmux. _session_rows assembles each LIVE session
    from Sessions.live() (the backend query) + the names registry + working-notes."""

    def _stub(self, live, notes, names):
        saved = (km.Sessions.live, km._working_notes, km._name_of, km._cwd_of, km._identity_of)
        km.Sessions.live = staticmethod(lambda: live)
        km._working_notes = lambda: notes
        km._name_of = lambda sid: names.get(sid, (sid[:8],))[0]
        km._cwd_of = lambda sid: names[sid][1]
        km._identity_of = lambda sid: names[sid][2:4]
        self.addCleanup(lambda: setattr(km.Sessions, "live", saved[0]))
        self.addCleanup(lambda: (setattr(km, "_working_notes", saved[1]), setattr(km, "_name_of", saved[2]),
                                 setattr(km, "_cwd_of", saved[3]), setattr(km, "_identity_of", saved[4])))

    def test_session_rows_assembles_both_backends(self):
        self._stub(
            live={"sid-t": {"state": "working", "backend": "tmux"},
                  "sid-s": {"state": "waiting", "backend": "sdk"}},
            notes={"sid-t": "owns feed.ts"},           # SDK has no working-note yet (P3) → ''
            names={"sid-t": ("alpha", "/work/a", "#112233", "#ffffff"),
                   "sid-s": ("beta", "/work/b", "blue", "white")})
        rows = {r["id"]: r for r in km._session_rows()}
        self.assertEqual(set(rows), {"sid-t", "sid-s"})
        # lastSid = the session's CURRENT transcript fsid (self-identity join, the user 2026-07-27);
        # with no diverged SDK registry it is the sid itself.
        self.assertEqual(rows["sid-t"], {"id": "sid-t", "name": "alpha", "state": "working", "dir": "/work/a",
                                         "bg": "#112233", "fg": "#ffffff", "lastSid": "sid-t",
                                         "working": "owns feed.ts", "backend": "tmux"})
        self.assertEqual(rows["sid-s"], {"id": "sid-s", "name": "beta", "state": "waiting", "dir": "/work/b",
                                         "bg": "blue", "fg": "white", "lastSid": "sid-s",
                                         "working": "", "backend": "sdk"})

    def test_empty_when_no_live_sessions(self):
        self._stub(live={}, notes={}, names={})
        self.assertEqual(km._session_rows(), [])


class WorkingNoteStore(unittest.TestCase):
    """The backend-agnostic working-note store (working/<sid> files): the postal bus's set_working goes
    through the kernel (Sessions.set_working_note, served at POST /working), works for ANY sid incl. an SDK
    session, and the note surfaces in _working_notes (→ GET /sessions). Replaces the tmux @romp-working var."""

    def setUp(self):
        import tempfile
        from pathlib import Path
        self._saved = km.WORKING_DIR
        km.WORKING_DIR = Path(tempfile.mkdtemp()) / "working"

    def tearDown(self):
        km.WORKING_DIR = self._saved

    def test_set_read_and_clear_round_trip(self):
        km.Sessions.set_working_note("sid-x", "owns feed.ts")
        self.assertEqual(km.Sessions.working_note("sid-x"), "owns feed.ts")
        self.assertEqual(km._working_notes(), {"sid-x": "owns feed.ts"})
        km.Sessions.set_working_note("sid-x", "")          # clear → the claim is lifted
        self.assertEqual(km.Sessions.working_note("sid-x"), "")
        self.assertEqual(km._working_notes(), {})

    def test_any_backend_sid_can_publish(self):
        # no backend gate: an SDK session's sid stores + reads the same way a tmux one does
        km.Sessions.set_working_note("sdk-sid", "drafting api")
        self.assertEqual(km._working_notes().get("sdk-sid"), "drafting api")

    def test_rejects_path_traversal_sid(self):
        km.Sessions.set_working_note("../evil", "x")        # sid is a path component → must not escape the store
        self.assertEqual(km._working_notes(), {})
        self.assertEqual(km.Sessions.working_note("../evil"), "")

    def test_post_working_endpoint_is_wired(self):
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertIn('u.path == "/working"', src, "POST /working routes set_working through the kernel")
        self.assertIn("Sessions.set_working_note(sid, str(body.get(\"text\") or \"\"))", src)


if __name__ == "__main__":
    unittest.main()
