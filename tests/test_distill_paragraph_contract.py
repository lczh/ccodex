"""The card-summary PARAGRAPH CONTRACT (the user 2026-07-29).

Three judge prompts write the three card surfaces: the distiller's takeaway for a completed goal, the
briefer's decision brief for a blocked one, and the staller's note for one romp has stopped acting on. An
audit of the live corpus against the jld writing method found the same defect in all three: the part that is
NOT finished rode the tail of one long paragraph. Among the 300 most recent takeaways, 95% were a single
paragraph (median 79 words, the longest 226), and of the 38 that mentioned something still open, 30 hung it
off the end of a multi-sentence paragraph while only 2 gave it its own.

So the contract is: one message per paragraph, and the unfinished part is the LAST paragraph, alone, in one
short sentence. Prompt text IS the artifact for a judge, so asserting on it asserts behavior. Every check
below corresponds to a change that was MEASURED on real cards (24 completed, 7 blocked, replayed under nine
wordings; harness shape in the judge-prompt-dry-run-replay note), and the wording that shipped is a minimal
diff on the prompts that were already working:

  shape (leftover alone in a final short paragraph)   0/24 before   5-8/24 after
  welded leftover (buried mid-paragraph)              4-5/24        2/24
  "the user" / "the assistant" on a card FOR them     13-17/24      0-1/24
  em dashes, which the prompt banned while using them 0-3/24        0-1/24
  takeaway length (median words)                      68-73         79-81

A full rewrite was tried first and scored WORSE than this minimal diff on every one of those columns while
running 20 words longer, so the shipped wording keeps the original brevity anchor and section specs and
changes only what the audit named.
"""
import pathlib
import unittest
from importlib.machinery import SourceFileLoader
import os
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = SourceFileLoader("romp_judge_paras", str(ROOT / "kernel" / "judge.py")).load_module()

PROMPTS = {
    "distiller": jd.DISTILL_SYS,
    "briefer": jd.BLOCK_BRIEF_SYS,
    "staller": jd.STALL_BRIEF_SYS,
}


class ParagraphContract(unittest.TestCase):
    """All three surfaces: one message per paragraph, unfinished part alone at the end, second person."""

    def test_all_three_ask_for_one_message_per_paragraph(self):
        for name, p in PROMPTS.items():
            self.assertIn("one message per paragraph", p.lower(), "%s: the contract itself" % name)
            self.assertIn("no paragraph longer than three sentences", p,
                          "%s: the cap that stops a 'paragraph' from becoming the old blob" % name)

    def test_the_unfinished_part_gets_the_last_paragraph_alone(self):
        # the distiller's leftover, the briefer's non-decision leftover, the staller's holding reason: one
        # shape across all three, so the short paragraph at the end always means "this part is not done".
        for name, p in PROMPTS.items():
            self.assertIn("last paragraph, alone, in one short sentence", p,
                          "%s: alone at the end, one sentence, never a trailing clause" % name)
        self.assertIn("Never attach it to a sentence or a paragraph about what got done", jd.DISTILL_SYS)
        self.assertIn("Never attach it to a paragraph about the decision", jd.BLOCK_BRIEF_SYS)

    def test_that_paragraph_carries_no_label_of_its_own(self):
        """An earlier wording shouted the phrase and a replayed card came back reading
        'STILL OPEN: run install.sh'. Only BACKGROUND and TAKEAWAY are labels."""
        self.assertIn("give it no label, just the sentence", jd.DISTILL_SYS)
        self.assertIn("with no label", jd.BLOCK_BRIEF_SYS)
        self.assertIn("with no label", jd.STALL_BRIEF_SYS)
        self.assertNotIn("STILL OPEN", jd.DISTILL_SYS,
                         "the phrase in caps reads as a label to copy, which is how it reached a card")

    def test_no_filler_about_nothing_being_left(self):
        """Takeaways closed with 'nothing is left to act on' and 'no further action needed' — a recap ending
        that says what the card's own column already says."""
        self.assertIn("never say that nothing is left to do", jd.DISTILL_SYS)

    def test_the_distiller_keeps_its_original_brevity_anchor(self):
        """Replacing this sentence with a word count grew replayed takeaways ~40% (75 to 106 words median),
        so the 2026-06-19 wording stands and the paragraph rules were layered on top of it."""
        self.assertIn("usually one sentence and at most two or three", jd.DISTILL_SYS)
        self.assertNotIn("sixty words", jd.DISTILL_SYS)

    def test_the_brief_still_leads_with_the_decision(self):
        """Rewording this one sentence cost the decision-first lead on 4 of 7 replayed cards (the takeaway
        opened 'You asked for...' instead), so it is the 2026-06-18 original with only its person changed."""
        self.assertIn("Lead with exactly what you must decide or provide", jd.BLOCK_BRIEF_SYS)
        self.assertNotIn("Lead with exactly what they must decide", jd.BLOCK_BRIEF_SYS)

    def test_repeated_owed_items_collapse_to_one_paragraph(self):
        """A brief handed three <owed> rows that were one decision restated it three times, and twice said so
        out loud ('This is the same rebuild decision restated...')."""
        self.assertIn("come down to the SAME decision, write ONE paragraph", jd.BLOCK_BRIEF_SYS)
        self.assertIn("never remark that the items repeat", jd.BLOCK_BRIEF_SYS)

    def test_the_reader_is_addressed_as_you(self):
        """The corpus said 'the user' in 17% of takeaways and once 'the assistant acknowledged...', which
        reads oddly on a card written for the person reading it."""
        for name, p in PROMPTS.items():
            self.assertIn("addressed to the user as **you**", p, name)
            self.assertIn("never call them 'the user'", p, name)
            self.assertIn("never call the session 'the assistant'", p, name)
            self.assertNotIn("from the user's vantage", p, "%s: the ambiguous phrasing is gone" % name)

    def test_the_distiller_bans_the_colon_spliced_list_and_process_mechanics(self):
        """29% of takeaways opened 'All four fixes landed:' and comma-spliced the real content; 9% carried
        test counts ('3543 Python / 1432 node tests green'), which is process, not outcome."""
        self.assertIn("behind one colon", jd.DISTILL_SYS)
        self.assertIn("test counts, and whether the suites passed", jd.DISTILL_SYS)

    def test_the_prompts_do_not_use_the_em_dashes_they_ban(self):
        """11% of takeaways carried an em dash while the prompt banned them, and the prompt used eight
        itself. Removing them from the prompt text took the replayed rate to 0-1 of 24."""
        for name, p in PROMPTS.items():
            self.assertIn("no em dashes", p, "%s: still banned" % name)
            self.assertEqual(p.count("—"), 0, "%s: the prompt must not model what it forbids" % name)


class ParserKeepsTheParagraphs(unittest.TestCase):
    """The reply parser is what has to survive the new shape: a takeaway is now MULTI-paragraph, the
    trailing SOURCE line still peels off around it, and a decorated label still parses."""

    REPLY = ("BACKGROUND: You asked for the retry detail to reach the UI.\n"
             "TAKEAWAY: The rebuilt bundle carries the real request id and backoff.\n\n"
             "Running install.sh is the one step left to install it.\n"
             "SOURCE: m4")

    def _parse(self, reply):
        body, src = jd._split_source(reply)
        bg, take = jd._split_sections(body)
        return bg, take, src

    def test_source_peels_off_a_multi_paragraph_takeaway(self):
        bg, take, src = self._parse(self.REPLY)
        self.assertEqual(src, "m4")
        self.assertEqual(bg, "You asked for the retry detail to reach the UI.")
        self.assertEqual(take.split("\n\n"),
                         ["The rebuilt bundle carries the real request id and backoff.",
                          "Running install.sh is the one step left to install it."],
                         "the blank line between outcome and leftover is the card's paragraph break")

    def test_the_blank_line_survives_storage_untouched(self):
        """node['summary'] is stored verbatim and the card renders it with white-space: pre-wrap, so the
        paragraph break IS the blank line. Anything that collapsed it would silently re-weld the leftover."""
        _, take, _ = self._parse(self.REPLY)
        self.assertIn("\n\n", take)
        self.assertEqual(take, take.strip(), "trimmed at the ends only")

    def test_a_decorated_label_still_parses(self):
        """One replayed reply came back '**BACKGROUND:** ... **TAKEAWAY:** ...'. Unnormalized, the labels
        land on the card and the whole reply files as the takeaway."""
        for reply, why in (("**BACKGROUND:** b.\n**TAKEAWAY:** t.", "bold"),
                           ("## BACKGROUND\nb.\n## TAKEAWAY\nt.", "heading"),
                           ("__BACKGROUND:__ b.\n__TAKEAWAY:__ t.", "underscore")):
            self.assertEqual(jd._split_sections(reply), ("b.", "t."), why)

    def test_the_words_are_untouched_inside_prose(self):
        bg, take = jd._split_sections(
            "BACKGROUND: b.\nTAKEAWAY: the fix cut BACKGROUND noise, and TAKEAWAY was never a label here.")
        self.assertEqual(bg, "b.")
        self.assertEqual(take, "the fix cut BACKGROUND noise, and TAKEAWAY was never a label here.",
                         "only a label at the START of a line is a label")

    def test_an_unlabeled_reply_is_all_takeaway_as_before(self):
        self.assertEqual(jd._split_sections("just the takeaway."), (None, "just the takeaway."))

    def test_a_dropped_takeaway_label_still_splits(self):
        """A replayed reply labeled its background and then ran straight into eight per-item paragraphs with
        no TAKEAWAY label. Unhandled, the card opens with the word 'BACKGROUND:'."""
        bg, take = jd._split_sections("BACKGROUND: you asked for chart revisions.\n\n"
                                      "All the revisions are in.\n\nThe error bars are real now.")
        self.assertEqual(bg, "you asked for chart revisions.")
        self.assertEqual(take, "All the revisions are in.\n\nThe error bars are real now.")
        self.assertNotIn("BACKGROUND", take)

    def test_a_lone_background_block_stays_the_takeaway(self):
        """One labeled block and nothing after it: the card keeps showing that text, as it always did,
        rather than a background with an empty takeaway."""
        self.assertEqual(jd._split_sections("BACKGROUND: the only thing it wrote."),
                         (None, "the only thing it wrote."))


if __name__ == "__main__":
    unittest.main()
