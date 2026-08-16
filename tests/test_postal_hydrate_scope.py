#!/usr/bin/env python3
"""_hydrate_postal must only hydrate mail this session actually received.

It scanned the output of EVERY tool for `romp-msg-id` markers and looked each id up in a BOX-WIDE
index, with no check of who the message was addressed to — then REPLACED the event when every id
resolved. So text the agent merely READ (a file, a fetched page, a grep hit, its own echo of mail)
could make a Bash/Read/WebFetch row vanish from the chat and come back as a postal card wearing
another session's name and colour; and an id belonging to a message between two OTHER sessions
rendered that message's body in a chat that was never a recipient.

Two scopes now: only the MAIL READERS' output is scanned (check_inbox, `romp mail inbox|recv|peek`,
the drain) alongside user text — those are the only places format_inbox / format_push output can
legitimately land — and a record whose to_id names someone else falls through to the existing loud
unresolved path instead of rendering.

A genuine multi-message drain must still hydrate EVERY id, so that stays covered here too.

SYNTHETIC fixtures only: invented sessions (notes-api's web/api/tests), placeholder UUIDs, TESTHOST.
"""
import inspect
import io
import json
import os
import tempfile
import unittest
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = SourceFileLoader("romp_kernel_hscope", os.path.join(BIN, "romp-kernel")).load_module()

ME = "11111111-2222-3333-4444-555555555555"       # the session whose chat is being built ('web')
PEER = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"     # the sender ('api')
OTHER = "99999999-8888-7777-6666-555555555555"    # a third session ('tests') — never this chat

M1 = "1700000000.11111_22222.TESTHOST"
M2 = "1700000001.33333_44444.TESTHOST"
M3 = "1700000002.55555_66666.TESTHOST"

MARKER = "<!-- romp-msg-id: %s -->"


def row(mid, to, body="the changelog is the last thing left on the deploy"):
    return {"id": mid, "from": "api", "fromId": PEER, "fromHost": "", "toId": to,
            "body": body, "kind": "coordinate", "t": 1700000000, "park": False}


def tool(name, output, **kw):
    ev = {"kind": "tool", "name": name, "input": "", "output": output,
          "isError": False, "uuid": "cccccccc-dddd-eeee-ffff-000000000000", "ts": "x"}
    ev.update(kw)
    return ev


def bash(command, output):
    return tool("Bash", output, input=json.dumps({"command": command, "description": "d"}))


def user(text):
    return {"kind": "user", "md": text, "uuid": "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
            "ts": "x", "human": False}


INBOX_TOOL = "mcp__romp-postal-service__check_inbox"


class HydrateScope(unittest.TestCase):
    """Only a mail reader's output (and user text) is scanned for message ids."""

    def _run(self, events, index, sid=ME):
        err = io.StringIO()
        saved_sum, saved_err = km._msg_summaries, km.sys.stderr
        km._msg_summaries, km.sys.stderr = (lambda: {}), err
        try:
            return km._hydrate_postal(events, index, sid), err.getvalue()
        finally:
            km._msg_summaries, km.sys.stderr = saved_sum, saved_err

    def test_a_file_read_that_mentions_a_message_id_is_not_hydrated(self):
        # The core bug: a Read of a file that happens to carry the marker replaced the tool row with a
        # postal card. The row must survive intact — and this is not a postal event, so nothing is warned.
        ev = tool("Read", "notes.md line 3: %s\nsee the handoff above" % (MARKER % M1))
        out, warned = self._run([ev], {M1: row(M1, ME)})
        self.assertEqual(out, [ev], "a Read row is untouched — no card, no mid stamped on it")
        self.assertEqual(warned, "", "and nothing is reported: it was never postal traffic")

    def test_a_bash_row_echoing_a_received_id_keeps_its_output(self):
        # An agent's own echo of mail it received (grep, cat, a script that prints the marker back)
        # used to swap the Bash row out for a duplicate card of a message already shown once.
        ev = bash("grep -n romp-msg-id handoff.md", "handoff.md:7:%s" % (MARKER % M1))
        out, _ = self._run([ev], {M1: row(M1, ME)})
        self.assertEqual(out, [ev], "the command and its output stay in the chat")

    def test_a_web_fetch_carrying_a_marker_is_not_hydrated(self):
        ev = tool("WebFetch", "the page said: %s" % (MARKER % M1))
        out, _ = self._run([ev], {M1: row(M1, ME)})
        self.assertEqual(out, [ev])

    def test_a_command_that_merely_mentions_the_subcommand_is_not_a_mail_read(self):
        # The matcher is anchored at a command position, so quoting the subcommand as an ARGUMENT
        # doesn't turn an ordinary grep into a mailbox reader.
        ev = bash('grep -rn "romp mail inbox" docs/', "docs/reference.md:86:%s" % (MARKER % M1))
        out, _ = self._run([ev], {M1: row(M1, ME)})
        self.assertEqual(out, [ev], "'romp mail inbox' inside a quoted argument is not a mail read")

    def test_the_check_inbox_tool_output_still_hydrates(self):
        out, warned = self._run([tool(INBOX_TOOL, "\U0001F4EC New message(s):\nhi\n" + MARKER % M1)],
                                {M1: row(M1, ME)})
        self.assertEqual([e["kind"] for e in out], ["postal-service"])
        self.assertEqual((out[0]["direction"], out[0]["peer"], out[0]["mid"]), ("in", "api", M1))
        self.assertEqual(warned, "")

    def test_the_mail_reading_cli_subcommands_still_hydrate(self):
        for cmd in ("romp mail inbox", "romp mail recv", "romp mail peek", "romp --mail inbox",
                    "romp-postal-service inbox", "romp mail drain --id " + ME,
                    "cd /tmp/notes-api && romp mail inbox"):
            with self.subTest(cmd=cmd):
                out, _ = self._run([bash(cmd, "from api:\n" + MARKER % M1)], {M1: row(M1, ME)})
                self.assertEqual([e["kind"] for e in out], ["postal-service"], cmd)

    def test_delivered_mail_as_user_text_still_hydrates(self):
        # How mail actually reaches a session: the Stop-hook drain's block reason and the kernel's
        # pane/socket push both land as user records. That path is unchanged.
        out, _ = self._run([user("####\n\U0001F4EC from api\n####\nbody\n" + MARKER % M1)],
                           {M1: row(M1, ME)})
        self.assertEqual([e["kind"] for e in out], ["postal-service"])

    def test_the_outgoing_card_paths_are_untouched(self):
        # send_message / `romp mail send` are matched before the scan and never depended on it.
        mcp = tool("mcp__romp-postal-service__send_message", "Delivered to 'api'.",
                   input=json.dumps({"to": "api", "body": "taking the deploy"}))
        cli = tool("Bash", "[romp mail] delivered to api",
                   input='romp mail send api "taking the deploy"')
        out, _ = self._run([mcp, cli], {})
        self.assertEqual([(e["kind"], e["direction"]) for e in out],
                         [("postal-service", "out")] * 2)


class HydrateRecipient(unittest.TestCase):
    """Only mail addressed to THIS session is rendered in its chat."""

    def _run(self, events, index, sid=ME):
        err = io.StringIO()
        saved_sum, saved_err = km._msg_summaries, km.sys.stderr
        km._msg_summaries, km.sys.stderr = (lambda: {}), err
        try:
            return km._hydrate_postal(events, index, sid), err.getvalue()
        finally:
            km._msg_summaries, km.sys.stderr = saved_sum, saved_err

    def test_a_message_addressed_to_another_session_is_not_rendered(self):
        # A peer's message body (delivered as user text) quoting an id from a conversation between two
        # OTHER sessions used to render THAT conversation's body here, wearing its sender's identity.
        secret = row(M2, OTHER, body="the staging credentials rotate on friday")
        out, warned = self._run([user("look at this: " + MARKER % M2)], {M2: secret})
        self.assertEqual([e["kind"] for e in out], ["user"], "no card for someone else's mail")
        self.assertNotIn("the staging credentials rotate on friday",
                         json.dumps(out[0]), "and its body never reaches this chat")
        self.assertEqual(out[0]["mids"], [M2], "the turn still carries the id it mentioned (deep-link)")
        self.assertIn("unresolved", warned, "it goes down the existing loud path, not silence")
        self.assertIn(M2, warned)

    def test_the_same_id_addressed_to_us_does_render(self):
        out, warned = self._run([user("mail: " + MARKER % M1)], {M1: row(M1, ME)})
        self.assertEqual([e["kind"] for e in out], ["postal-service"])
        self.assertEqual(warned, "")

    def test_a_check_inbox_output_naming_someone_elses_id_is_not_rendered(self):
        out, warned = self._run([tool(INBOX_TOOL, "spoofed: " + MARKER % M2)], {M2: row(M2, OTHER)})
        self.assertEqual([e["kind"] for e in out], ["tool"], "even a real mail reader is scoped")
        self.assertIn("unresolved", warned)

    def test_a_genuine_multi_message_drain_hydrates_every_id(self):
        # One drain legitimately carries several messages — all of them must still become cards.
        ev = user("\n".join(["\U0001F4EC New message(s):"]
                            + [MARKER % m for m in (M1, M2, M3)]))
        idx = {M1: row(M1, ME, "one"), M2: row(M2, ME, "two"), M3: row(M3, ME, "three")}
        out, warned = self._run([ev], idx)
        self.assertEqual([e["kind"] for e in out], ["postal-service"] * 3)
        self.assertEqual([e["mid"] for e in out], [M1, M2, M3])
        self.assertEqual([e["body"] for e in out], ["one", "two", "three"])
        self.assertEqual(warned, "")

    def test_a_drain_with_one_foreign_id_stays_all_or_nothing(self):
        ev = user("\n".join(MARKER % m for m in (M1, M2)))
        out, warned = self._run([ev], {M1: row(M1, ME), M2: row(M2, OTHER)})
        self.assertEqual([e["kind"] for e in out], ["user"], "a partial run never half-renders")
        self.assertEqual(out[0]["mids"], [M1, M2])
        self.assertIn("1 of 2", warned)

    def test_a_log_row_with_no_recipient_still_renders(self):
        # Safety net: pre-schema rows (and any writer that omits to_id) must not lose their cards —
        # the check scopes what we render, it is not a reason to drop real mail.
        out, warned = self._run([user(MARKER % M1)], {M1: row(M1, "")})
        self.assertEqual([e["kind"] for e in out], ["postal-service"])
        self.assertEqual(warned, "")

    def test_a_caller_that_passes_no_sid_keeps_the_old_behavior(self):
        out, _ = self._run([user(MARKER % M2)], {M2: row(M2, OTHER)}, sid=None)
        self.assertEqual([e["kind"] for e in out], ["postal-service"],
                         "no sid to compare against → no recipient check (direct callers)")

    def test_build_session_passes_its_sid_in(self):
        # Without the wiring the recipient check is dead code in the only caller that matters.
        self.assertIn("_hydrate_postal(events, _postal_index(), sid)",
                      inspect.getsource(km.build_session))


if __name__ == "__main__":
    unittest.main()
