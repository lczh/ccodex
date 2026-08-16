"""macOS ships bash 3.2 (GPLv2-frozen), whose $() parser is a naive quote-counting scan rather
than the recursive real parser bash 4 introduced. A heredoc INSIDE a command substitution is its
best-known landmine: apostrophes in the heredoc body desync the scan and the script dies with
"unexpected EOF while looking for matching quote" attributed to some unrelated later line.
bin/romp-uninstall shipped exactly that (a python heredoc inside $()) and broke every macOS
install's uninstaller — caught only by the v1.3.0 release's dispatch-gated macOS CI run
(2026-08-16), since Linux bash 5 parses it fine. The fix wraps the heredoc in a function.

Linux CI cannot RUN bash 3.2, so pin the construct out structurally: no shell entrypoint may
open a heredoc on the same line as a command substitution. (A heredoc opener on a later line
inside a multi-line $() would evade this scan; none exists and none should be added.)
"""
import os
import re
import subprocess
import unittest

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)

# $( ... << — a heredoc opened inside a command substitution, on one line. Redirections like
# $(cmd <file) and arithmetic $((x<<2)) must not match: require the literal "<<" NOT preceded
# by "(" (rules out $(( )) shifts) and not "<<<" (a herestring is parsed fine).
_LANDMINE = re.compile(r"\$\((?!\()[^)]*<<(?!<)")


def _shell_sources():
    out = subprocess.run(["git", "-C", ROOT, "ls-files", "bin", "*.sh", "scripts"],
                         capture_output=True, text=True, check=True).stdout.split()
    for rel in out:
        path = os.path.join(ROOT, rel)
        try:
            with open(path, "rb") as f:
                head = f.readline()
        except OSError:
            continue
        if head.startswith(b"#!") and b"sh" in head:
            yield rel, path


class NoHeredocInsideCommandSubstitution(unittest.TestCase):
    def test_no_shell_script_opens_a_heredoc_inside_a_substitution(self):
        hits = []
        checked = 0
        for rel, path in _shell_sources():
            checked += 1
            with open(path, encoding="utf-8", errors="replace") as f:
                for n, line in enumerate(f, 1):
                    stripped = line.lstrip()
                    if stripped.startswith("#"):
                        continue                     # commentary may cite the pattern
                    if _LANDMINE.search(line):
                        hits.append("%s:%d: %s" % (rel, n, line.strip()))
        self.assertGreater(checked, 5, "the shell-source sweep found too few files to be real")
        self.assertEqual(hits, [], "heredoc inside $() breaks macOS bash 3.2's parser; "
                                   "wrap the heredoc in a function and substitute the call:\n"
                         + "\n".join(hits))


if __name__ == "__main__":
    unittest.main()
