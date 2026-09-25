"""
Differential tests: TermReel's emulator versus a real tmux pane.

Every assertion here compares what TermReel's TerminalState/ANSIParser shows
after a byte stream with what tmux shows after exactly the same bytes. No
expected screen is written by hand. When the two disagree the test prints
both screens, so a failure points at a concrete emulator divergence rather
than at an assumption in the test.

Byte sources, in order of how much they rely on the test author:
  1. Real programs (vim, less, git log --graph, python REPL, bash) recorded
     on a PTY with TERM=xterm-256color. Nobody chose those sequences.
  2. A seeded random generator over the escape sequences TermReel claims to
     support, printed with the seed so failures are reproducible.
  3. Targeted edge cases (pending wrap, REP, wide chars at the margin,
     scroll regions) where emulators commonly diverge.

Tests whose names contain "slow" are skipped by ``termreel test -f``.
"""

from __future__ import annotations

import os
import random
import shutil
import subprocess
import tempfile
import unittest

from tests.tmux_oracle import (
    ScreenResult,
    TmuxOracle,
    describe_diff,
    record_pty_session,
    termreel_render,
    tmux_available,
)

requires_tmux = unittest.skipUnless(tmux_available(), "tmux is not installed")


def _compare(test: unittest.TestCase, data: bytes, cols: int = 20, rows: int = 6,
             chunks=None, label: str = "", check_joined: bool = True) -> None:
    with TmuxOracle(cols=cols, rows=rows) as oracle:
        expected = oracle.render(data)
    actual = termreel_render(data, cols=cols, rows=rows, chunks=chunks)
    same = (
        expected.lines == actual.lines
        and (expected.cursor_x, expected.cursor_y) == (actual.cursor_x, actual.cursor_y)
        and expected.alternate_on == actual.alternate_on
        and (not check_joined or expected.joined == actual.joined)
        and expected.cursor_visible == actual.cursor_visible
    )
    if not same:
        detail = describe_diff(expected, actual)
        if check_joined and expected.joined != actual.joined:
            detail += f"\njoined tmux    : {expected.joined!r}\njoined termreel: {actual.joined!r}"
        if expected.cursor_visible != actual.cursor_visible:
            detail += f"\ncursor_visible tmux={expected.cursor_visible} termreel={actual.cursor_visible}"
        test.fail(f"TermReel diverges from tmux {label}\ninput={data[:400]!r}\n{detail}")


# Cases where emulators commonly get it wrong. The expected result is whatever
# tmux does with the bytes; the comment says why the case is interesting.
TARGETED_CASES = {
    # LF moves down without returning to column 0 (the old emulator did CRLF).
    "lf_keeps_column": b"abc\x1b[1;5HX\nY",
    # After the last column the cursor is in the pending-wrap state.
    "pending_wrap_cursor": b"A" * 20,
    "pending_wrap_lf": b"A" * 20 + b"\nx",
    "pending_wrap_cr": b"A" * 20 + b"\rx",
    "pending_wrap_bs": b"A" * 20 + b"\bx",
    "pending_wrap_cup_clears": b"A" * 20 + b"\x1b[3;3Hq",
    "bs_reverse_wrap": b"A" * 20 + b"B\x08\x08x",
    # REP repeats the last printed graphic character (used by ncurses).
    "rep": b"ab\x1b[3bc",
    "rep_after_cursor_move": b"ab\x1b[C\x1b[2bz",
    "c0_controls_ignored": b"a\x00b\x7fc\x0ed\x0fe\x0bf\x0cg",
    "decaln": b"\x1b#8",
    "esc_percent_consumed": b"a\x1b%Gb",
    "wide_at_margin": b"A" * 19 + "中".encode(),
    "wide_basic": "中文x".encode(),
    "wide_overwrite_right_half": "中".encode() + b"\x1b[1;2Hx",
    "wide_overwrite_left_half": "中".encode() + b"\x1b[1;1Hx",
    "combining_accent": "e\u0301x".encode(),
    "emoji_widths": "a\u2714b\U0001F600c".encode(),
    "sgr58_underline_colour_consumed": b"\x1b[58;5;3mhi\x1b[58:2::1:2:3mX",
    "sgr_colon_truecolour": b"\x1b[38:2::10:20:30mA\x1b[48:5:3mB\x1b[0mC",
    "cbt": b"\x1b[1;12H\x1b[Zx",
    "decawm_off": b"\x1b[?7l" + b"B" * 25 + b"z",
    "decstbm_homes_cursor": b"\x1b[5;5H\x1b[2;4rX",
    "tab_stops": b"a\tb\t\tc",
    "tab_near_margin": b"\x1b[1;17Hab\tc",
    "tab_over_text": b"abcdefghij\x1b[1;1H\tX",
    "hts_sets_stop": b"\x1b[1;4H\x1bH\x1b[1;1H\tX\tZ",
    "tbc_clears_all": b"\x1b[3g\x1b[2;1H\tY",
    "tbc_clears_one": b"\x1b[1;9H\x1b[0g\x1b[1;1H\tY",
    "rep_after_lf": b"ab\r\n\x1b[2bz",
    "rep_after_esc": b"ab\x1b7\x1b[2bz",
    "rep_after_sgr": b"ab\x1b[1m\x1b[2bz",
    "irm_insert": b"abcdef\x1b[1;3H\x1b[4hXY\x1b[4l",
    "vt_ff_are_lf": b"ab\x0bc\x0cd",
    "dec_graphics_g0": b"\x1b(0lqqk\x1b(Bx",
    "dec_graphics_g1_shift": b"\x1b)0a\x0eq\x0fq",
    "ri_at_top_scrolls": b"\x1b[Hline\x1bMz",
    "cuu_stops_at_margin": b"\x1b[3;5r\x1b[4;1H\x1b[10Ax",
    "xterm_modifykeys_ignored": b"\x1b[>4;1mplain",
    "ech": b"abcdef\x1b[1;2H\x1b[2Xz",
    "el0_el1_el2": b"abcdef\x1b[1;3H\x1b[1K\r\nabcdef\x1b[2;3H\x1b[K\r\nabcdef\x1b[2K",
    "ed0_ed1": b"111111\r\n222222\r\n333333\x1b[2;3H\x1b[1J\x1b[3;4H\x1b[0J",
    "ed2_keeps_cursor": b"hello\r\nworld\x1b[2J",
    "ed3_keeps_screen": b"hello\r\nworld\x1b[3J",
    "il_dl": b"1\r\n2\r\n3\r\n4\r\n5\x1b[2;3H\x1b[2L\x1b[5;4H\x1b[1M",
    "ich_dch": b"abcdefgh\x1b[1;3H\x1b[2@\x1b[1;6H\x1b[3P",
    "su_sd_in_region": b"1\r\n2\r\n3\r\n4\r\n5\r\n6\x1b[2;5r\x1b[2S\x1b[1T",
    "scroll_region_lf": b"\x1b[2;4r\x1b[4;1Ha\nb\nc\nd",
    "origin_mode": b"\x1b[2;4r\x1b[?6h\x1b[1;1HX\x1b[10;1HY\x1b[?6l",
    "save_restore_decsc": b"ab\x1b7\x1b[4;10Hxy\x1b8Z",
    "save_restore_csi_s_u": b"ab\x1b[s\x1b[4;10Hxy\x1b[uZ",
    "alt_screen_1049": b"primary\x1b[?1049h\x1b[Halt text\x1b[?1049lX",
    "alt_screen_47": b"primary\x1b[?47halt\x1b[?47lX",
    "cursor_hide": b"x\x1b[?25l",
    "cursor_hide_show": b"x\x1b[?25l\x1b[?25h",
    "cha_hpa_vpa": b"\x1b[5Gx\x1b[3dy\x1b[2`z",
    "cnl_cpl": b"\x1b[3;5Hx\x1b[Ey\x1b[2Fz",
    "cup_out_of_range_clamps": b"\x1b[99;99Hx",
    "ris_resets": b"junk\x1b[5;5H\x1bcok",
    "decstr_soft_reset": b"\x1b[2;4r\x1b[?6h\x1b[!p\x1b[1;1Hq",
    "osc_title_bel_and_st": b"\x1b]0;title\x07A\x1b]2;x\x1b\\B",
    "dcs_ignored": b"\x1bP1$r0m\x1b\\after",
    "apc_pm_sos_ignored": b"\x1b_apc\x1b\\a\x1b^pm\x1b\\b\x1bXsos\x1b\\c",
    "bracketed_paste_mode_toggle": b"\x1b[?2004hx\x1b[?2004ly",
    "scroll_full_screen": b"".join(b"line%d\r\n" % i for i in range(12)),
    "long_wrap_then_scroll": b"W" * 130 + b"\r\nend",
    "utf8_split_invalid": b"a\xe4\xb8b\xffc",
    "c1_8bit_not_csi": b"a\xc2\x9b31mb",
    "private_sgr_ignored": b"\x1b[?4mX",
    "csi_intermediate_ignored": b"\x1b[1 qX\x1b[2\"qY",
    # Minimised from fuzz seed 231: a private-mode CSI forgets the REP char.
    "rep_after_private_mode": "😀\x1b[?1049h\x1b[2b".encode(),
    "rep_after_cursor_mode": b"x\x1b[?25h\x1b[3b",
    # Minimised from fuzz seed 284: DEC graphics wrapping onto the next row.
    "acs_wraps_across_rows": b"\x1b(0\x1b[11;24Hlqk",
}

# Bytes that should be delivered in pieces. The tuple lists cut offsets.
CHUNKED_CASES = {
    "esc_at_boundary": (b"ab\x1b[31mcd\x1b[0m", [2, 3]),
    "csi_params_split": (b"\x1b[2;15Hx", [3, 5]),
    "utf8_split": ("x中y😀z".encode(), [2, 3, 6, 8]),
    "osc_split": (b"\x1b]0;hello\x07ok", [3, 7, 9]),
    "every_byte": (b"\x1b[3;4H\x1b(0lqk\x1b(B\x1b[1bz\xe4\xb8\xad", list(range(1, 30))),
}


@requires_tmux
class TestTargetedAgainstTmux(unittest.TestCase):
    def test_targeted_cases_match_tmux(self):
        failures = []
        with TmuxOracle(cols=20, rows=6) as oracle:
            for name, data in TARGETED_CASES.items():
                expected = oracle.render(data)
                actual = termreel_render(data, cols=20, rows=6)
                if (expected.lines, expected.cursor_x, expected.cursor_y, expected.alternate_on,
                        expected.joined, expected.cursor_visible) != (
                        actual.lines, actual.cursor_x, actual.cursor_y, actual.alternate_on,
                        actual.joined, actual.cursor_visible):
                    detail = describe_diff(expected, actual)
                    if expected.joined != actual.joined:
                        detail += f"\n  joined tmux={expected.joined!r} termreel={actual.joined!r}"
                    if expected.cursor_visible != actual.cursor_visible:
                        detail += (f"\n  cursor_visible tmux={expected.cursor_visible} "
                                   f"termreel={actual.cursor_visible}")
                    failures.append(f"[{name}] input={data!r}\n{detail}")
        if failures:
            self.fail(f"{len(failures)} case(s) diverge from tmux:\n\n" + "\n\n".join(failures))

    def test_chunked_delivery_matches_tmux(self):
        for name, (data, cuts) in CHUNKED_CASES.items():
            with self.subTest(name=name):
                _compare(self, data, chunks=cuts, label=f"[{name}] chunked at {cuts}")


# ---------------------------------------------------------------------------
# Random sequences
# ---------------------------------------------------------------------------

def _random_stream(rng: random.Random, cols: int, rows: int, length: int) -> bytes:
    return b"".join(_random_tokens(rng, cols, rows, length))


def _random_tokens(rng: random.Random, cols: int, rows: int, length: int) -> list:
    """
    Build a stream from the sequences TermReel claims to handle. Parameters
    deliberately overshoot the screen so clamping paths get exercised.
    """
    words = ["hello", "x", "中文", "é", "│", "lqk", "\u00e9t\u00e9", "0123456789" * 3, "😀"]

    def n(hi):
        return rng.randint(0, hi)

    gens = [
        lambda: rng.choice(words).encode(),
        lambda: b"\r\n",
        lambda: b"\n",
        lambda: b"\r",
        lambda: b"\b",
        lambda: b"\t",
        lambda: b"\x1b[%d;%dH" % (n(rows + 3), n(cols + 5)),
        lambda: b"\x1b[%dA" % n(rows + 2),
        lambda: b"\x1b[%dB" % n(rows + 2),
        lambda: b"\x1b[%dC" % n(cols + 2),
        lambda: b"\x1b[%dD" % n(cols + 2),
        lambda: b"\x1b[%dG" % n(cols + 2),
        lambda: b"\x1b[%dd" % n(rows + 2),
        lambda: b"\x1b[%dJ" % n(2),
        lambda: b"\x1b[%dK" % n(2),
        lambda: b"\x1b[%dL" % n(4),
        lambda: b"\x1b[%dM" % n(4),
        lambda: b"\x1b[%d@" % n(6),
        lambda: b"\x1b[%dP" % n(6),
        lambda: b"\x1b[%dX" % n(6),
        lambda: b"\x1b[%db" % n(5),
        lambda: b"\x1b[%dS" % n(3),
        lambda: b"\x1b[%dT" % n(3),
        lambda: b"\x1b[%d;%dr" % (n(rows), n(rows + 1)),
        lambda: b"\x1b[r",
        lambda: b"\x1b7",
        lambda: b"\x1b8",
        lambda: b"\x1bM",
        lambda: b"\x1bD",
        lambda: b"\x1bE",
        lambda: b"\x1b(0",
        lambda: b"\x1b(B",
        lambda: b"\x1b[4h",
        lambda: b"\x1b[4l",
        lambda: b"\x1b[?7l",
        lambda: b"\x1b[?7h",
        lambda: b"\x1b[?6h",
        lambda: b"\x1b[?6l",
        lambda: b"\x1b[?25l",
        lambda: b"\x1b[?25h",
        lambda: b"\x1b[?1049h",
        lambda: b"\x1b[?1049l",
        lambda: b"\x1b[%d;%d;%dm" % (n(9), 30 + n(7), 40 + n(7)),
        lambda: b"\x1b[38;5;%dm" % n(255),
        lambda: b"\x1b[0m",
        lambda: b"\x1b]0;t\x07",
        lambda: b"\x1bH",
        lambda: b"\x1b[%dg" % rng.choice([0, 3]),
        lambda: b"\x1b[%dZ" % n(3),
        lambda: b"\x1b[%dE" % n(3),
        lambda: b"\x1b[%dF" % n(3),
    ]
    return [rng.choice(gens)() for _ in range(length)]


@requires_tmux
class TestFuzzAgainstTmux(unittest.TestCase):
    SEEDS = int(os.environ.get("TERMREEL_FUZZ_SEEDS", "40"))

    def _run_seeds(self, seeds, cols, rows, length):
        failures = []
        with TmuxOracle(cols=cols, rows=rows) as oracle:
            for seed in seeds:
                rng = random.Random(seed)
                data = _random_stream(rng, cols, rows, length)
                expected = oracle.render(data)
                cuts = sorted(rng.sample(range(1, len(data)), k=min(8, len(data) - 1)))
                for chunks in (None, cuts):
                    actual = termreel_render(data, cols=cols, rows=rows, chunks=chunks)
                    if (expected.lines, expected.cursor_x, expected.cursor_y, expected.alternate_on,
                            expected.cursor_visible) != (
                            actual.lines, actual.cursor_x, actual.cursor_y, actual.alternate_on,
                            actual.cursor_visible):
                        failures.append(
                            f"seed={seed} cols={cols} rows={rows} chunks={chunks}\n"
                            f"input={data!r}\n{describe_diff(expected, actual)}"
                        )
                        break
                if len(failures) >= 3:
                    break
        if failures:
            self.fail("fuzzed streams diverge from tmux (reproduce with the seed):\n\n"
                      + "\n\n".join(failures))

    def test_fuzz_small_screen(self):
        self._run_seeds(range(12), cols=12, rows=5, length=25)

    def test_fuzz_slow_many_seeds(self):
        self._run_seeds(range(100, 100 + self.SEEDS), cols=20, rows=8, length=60)


# ---------------------------------------------------------------------------
# Real applications
# ---------------------------------------------------------------------------

def _need(tool: str):
    return unittest.skipUnless(shutil.which(tool), f"{tool} is not installed")


@requires_tmux
class TestRealAppsAgainstTmux(unittest.TestCase):
    COLS, ROWS = 60, 16

    def _check_app(self, argv, inputs, env=None, cwd=None, check_joined=True):
        data = record_pty_session(argv, inputs, cols=self.COLS, rows=self.ROWS, env=env, cwd=cwd)
        self.assertGreater(len(data), 50, f"{argv[0]} produced almost no output: {data!r}")
        # Replay the recording in the pieces it naturally has (4 KiB reads)
        # and also byte-for-byte odd chunks so parser carry-over is exercised.
        _compare(self, data, cols=self.COLS, rows=self.ROWS, label=f"[{argv[0]}]",
                 check_joined=check_joined)
        _compare(self, data, cols=self.COLS, rows=self.ROWS,
                 chunks=list(range(7, len(data), 97)), label=f"[{argv[0]} chunked]",
                 check_joined=check_joined)

    @_need("vim")
    def test_slow_vim_edit_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "f.txt")
            with open(path, "w") as fh:
                fh.write("".join(f"line {i} " + "word " * (i % 13) + "\n" for i in range(60)))
            self._check_app(
                ["vim", "-u", "NONE", "-i", "NONE", "-N", "-n", path],
                [(0.5, b"G"), (0.2, b"ggjjjdd"), (0.2, b"ohello \xe4\xb8\xad world\x1b"),
                 (0.2, b":set number\r"), (0.2, b"\x06"), (0.2, b"/word 7\r"),
                 (0.3, b":q!\r")],
            )

    @_need("less")
    def test_slow_less_paging(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "f.txt")
            with open(path, "w") as fh:
                fh.write("".join(f"{i:04d} " + "abc\tdef " * (i % 7) + "\n" for i in range(200)))
            self._check_app(["less", "-R", path],
                            [(0.4, b" "), (0.2, b" "), (0.2, b"b"), (0.2, b"/0150\r"), (0.3, b"q")],
                            env={"LESS": "", "LESSHISTFILE": "-"})

    @_need("git")
    def test_slow_git_log_graph(self):
        with tempfile.TemporaryDirectory() as tmp:
            genv = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e", "GIT_COMMITTER_NAME": "t",
                    "GIT_COMMITTER_EMAIL": "t@e", "HOME": tmp, "GIT_CONFIG_NOSYSTEM": "1"}
            env = dict(os.environ, **genv)

            def git(*args):
                subprocess.run(["git", *args], cwd=tmp, env=env, check=True, capture_output=True)

            git("init", "-q", "-b", "main")
            for i in range(4):
                git("commit", "-q", "--allow-empty", "-m", f"main {i}")
            git("checkout", "-q", "-b", "side", "HEAD~2")
            for i in range(3):
                git("commit", "-q", "--allow-empty", "-m", f"side {i} ✔ 中文")
            git("checkout", "-q", "main")
            git("merge", "-q", "--no-ff", "-m", "merge side", "side")
            self._check_app(
                ["git", "-c", "color.ui=always", "log", "--graph", "--oneline", "--all",
                 "--decorate"],
                [], env=dict(genv, PAGER="cat", GIT_PAGER="cat"), cwd=tmp,
            )

    def test_slow_python_repl(self):
        py = shutil.which("python3")
        if not py:
            self.skipTest("python3 missing")
        self._check_app(
            [py, "-q", "-i"],
            [(0.4, b"print('x' * 150)\r"), (0.2, "for i in range(20): print(i, '\\t|', '中' * i)\r\r".encode()),
             (0.3, b"exit()\r")],
            env={"PYTHONSTARTUP": "", "PYTHON_BASIC_REPL": "1"},
        )

    def test_slow_bash_prompt_and_colours(self):
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("bash missing")
        self._check_app(
            [bash, "--norc", "--noprofile", "-i"],
            [(0.3, b"PS1='\\[\\e[32m\\]\\u$ \\[\\e[0m\\]'\r"),
             (0.2, b"printf '%s\\n' {1..30}\r"),
             (0.2, b"ls --color=always / | head -5\r"),
             (0.2, b"echo -e 'a\\tb\\tc\\x1b[31mred\\x1b[0m'\r"),
             (0.2, b"exit\r")],
            env={"HISTFILE": "/dev/null"},
        )

    @_need("htop")
    def test_slow_htop_screen(self):
        # htop's contents change every refresh, so only compare a single
        # recording against itself in both emulators (the bytes are fixed).
        self._check_app(["htop", "-d", "100"], [(1.2, b"q")], env={"HTOPRC": "/dev/null"},
                        check_joined=False)


if __name__ == "__main__":
    unittest.main()
