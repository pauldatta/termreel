"""
Tests for `termreel live`: key parsing, prefix resolution, the prefix state
machine, raw-mode terminal handling, grid/pixel geometry, crossfade blending,
single-reader PTY behaviour, and pause/cut duration.

These exercise real POSIX pseudo-terminals and real ffmpeg output rather than
mocks, because every bug this feature was built around was a behaviour of the
real thing that a mock would have happily faked.
"""

import fcntl
import json
import os
import pty
import select
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import threading
import time
import unittest

from termreel.exceptions import KeySpecError
from termreel.live.config import (
    DEFAULT_PREFIX_SPEC,
    PREFIX_ENV_VAR,
    load_live_config,
    resolve_prefix,
    validate_prefix_spec,
)
from termreel.live.keyprobe import classify, format_report
from termreel.live.passthrough import (
    ACTION_MARK,
    ACTION_STOP,
    ACTION_TOGGLE_PAUSE,
    ACTION_UNKNOWN,
    PASTE_END,
    PASTE_START,
    PassthroughLoop,
    PrefixFSM,
    TerminalGuard,
)
from termreel.live.recorder import LiveRecorder, blend_frames
from termreel.renderer.cairo_renderer import (
    CairoTerminalRenderer,
    grid_for_pixels,
    pixels_for_grid,
)
from termreel.supervisor.pty_session import PtySupervisor
from termreel.utils.keystrokes import KeyMap, describe_key_bytes, parse_key_spec


def ffprobe_duration(path):
    """Container duration in seconds, or None if ffprobe is unavailable."""
    if shutil.which("ffprobe") is None:
        return None
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=nw=1:nk=1",
            path,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return float(result.stdout.strip())


def set_winsize(fd, rows, cols):
    """Resize a tty the way a window manager does, via TIOCSWINSZ."""
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


class TestParseKeySpec(unittest.TestCase):
    """The parser that replaced the ten-entry control-key lookup table."""

    def test_ctrl_letters_are_computed_not_tabulated(self):
        # Every letter must work, not just the handful the old table listed.
        for offset in range(26):
            letter = chr(ord("a") + offset)
            expected = chr((ord(letter) & 0x1F))
            self.assertEqual(parse_key_spec(f"C-{letter}"), expected)
            self.assertEqual(parse_key_spec(f"ctrl+{letter}"), expected)
            self.assertEqual(parse_key_spec(f"ctrl-{letter}"), expected)
            self.assertEqual(parse_key_spec(f"^{letter.upper()}"), expected)

    def test_ctrl_t_is_0x14(self):
        # The exact case that used to be typed into the shell as "C-t".
        self.assertEqual(parse_key_spec("C-t"), "\x14")
        self.assertEqual(parse_key_spec("Ctrl-T"), "\x14")
        self.assertEqual(parse_key_spec("^t"), "\x14")

    def test_ctrl_punctuation(self):
        self.assertEqual(parse_key_spec("C-]"), "\x1d")
        self.assertEqual(parse_key_spec("C-["), "\x1b")
        self.assertEqual(parse_key_spec("C-\\"), "\x1c")
        self.assertEqual(parse_key_spec("C-^"), "\x1e")
        self.assertEqual(parse_key_spec("C-_"), "\x1f")
        self.assertEqual(parse_key_spec("C-@"), "\x00")
        self.assertEqual(parse_key_spec("C-space"), "\x00")
        self.assertEqual(parse_key_spec("C-?"), "\x7f")

    def test_hex_byte_specs(self):
        self.assertEqual(parse_key_spec("0x14"), "\x14")
        self.assertEqual(parse_key_spec("0X14"), "\x14")
        self.assertEqual(parse_key_spec("0x0"), "\x00")
        self.assertEqual(parse_key_spec("0xff"), "\xff")

    def test_none_disables(self):
        for spec in ("none", "NONE", "off", "disabled"):
            self.assertEqual(parse_key_spec(spec), "")

    def test_named_keys_unchanged(self):
        self.assertEqual(parse_key_spec("Enter"), "\r")
        self.assertEqual(parse_key_spec("enter"), "\r")
        self.assertEqual(parse_key_spec("Escape"), "\x1b")
        self.assertEqual(parse_key_spec("Tab"), "\t")
        self.assertEqual(parse_key_spec("Backspace"), "\x7f")
        self.assertEqual(parse_key_spec("Up"), "\x1b[A")
        self.assertEqual(parse_key_spec("PageDown"), "\x1b[6~")

    def test_single_printable_character_passes_through(self):
        # Needed by the [y/N] auto-answer trigger.
        self.assertEqual(parse_key_spec("y"), "y")
        self.assertEqual(parse_key_spec("1"), "1")

    def test_raw_control_characters_accepted(self):
        self.assertEqual(parse_key_spec("\r"), "\r")
        self.assertEqual(parse_key_spec("\n"), "\r")
        self.assertEqual(parse_key_spec("\t"), "\t")
        self.assertEqual(parse_key_spec(" "), " ")

    def test_invalid_specs_raise_rather_than_falling_through(self):
        # The whole point: an unparseable spec must not become literal text.
        for spec in ("", "   ", "nonsense", "C-", "C-ab", "C-1", "0xzz", "0x100", "Ctrl+"):
            with self.subTest(spec=spec):
                with self.assertRaises(KeySpecError):
                    parse_key_spec(spec)

    def test_non_string_raises(self):
        for spec in (None, 12345, ["C-t"]):
            with self.subTest(spec=spec):
                with self.assertRaises(KeySpecError):
                    parse_key_spec(spec)

    def test_keymap_to_pty_delegates(self):
        self.assertEqual(KeyMap.to_pty("ctrl+c"), "\x03")
        self.assertEqual(KeyMap.to_pty("C-t"), "\x14")
        with self.assertRaises(KeySpecError):
            KeyMap.to_pty("definitely-not-a-key")

    def test_describe_key_bytes_round_trips(self):
        for spec in ("C-t", "C-a", "C-]", "Enter", "Tab", "Escape", "Backspace"):
            data = parse_key_spec(spec).encode("latin-1")
            label = describe_key_bytes(data)
            self.assertEqual(
                parse_key_spec(label),
                parse_key_spec(spec),
                f"{spec!r} -> {label!r} did not round-trip",
            )

    def test_describe_multibyte_falls_back_to_hex(self):
        # An Option-key accented character on an unconfigured macOS terminal.
        self.assertEqual(describe_key_bytes("é".encode("utf-8")), "0xc3 0xa9")


class TestSendKeyUsesRealBytes(unittest.TestCase):
    """The §3.2 bug: send_key used to type literal text for unmapped keys."""

    def test_supervisor_send_key_transmits_control_byte(self):
        master, slave = pty.openpty()
        supervisor = PtySupervisor(command="cat", rows=10, cols=40)
        try:
            supervisor.start()
            # Send through the supervisor's own master and read what the
            # child would see by inspecting the raw bytes written.
            written = []
            original = supervisor.send_text
            supervisor.send_text = lambda text, delay_per_char=0.0: written.append(text)
            supervisor.send_key("C-t")
            supervisor.send_key("C-g")
            supervisor.send_text = original
            self.assertEqual(written, ["\x14", "\x07"])
        finally:
            supervisor.terminate()
            for fd in (master, slave):
                try:
                    os.close(fd)
                except OSError:
                    pass

    def test_unknown_key_raises_instead_of_typing_it(self):
        supervisor = PtySupervisor(command="cat", rows=10, cols=40)
        with self.assertRaises(KeySpecError):
            supervisor.send_key("C-t-nonsense")


class TestTriggerActionsSurviveBadKeySpecs(unittest.TestCase):
    """
    Fallout of making unknown key specs raise: trigger actions run on a daemon
    thread, where an exception is an unhandled traceback that also discards
    every action queued behind it. An existing manifest saying
    ``action: "yes"`` used to type "yes"; it must now fail loudly but locally.
    """

    class _RecordingSupervisor:
        def __init__(self):
            self.keys = []
            self.text = []

        def send_key(self, key_name):
            self.keys.append(parse_key_spec(key_name))

        def send_text(self, text, delay_per_char=0.0):
            self.text.append(text)

    def test_a_bad_key_is_reported_and_the_rest_still_run(self):
        from io import StringIO
        from termreel.reactor.monitor import ScreenMonitor
        from termreel.reactor.triggers import ActionType, TriggerAction

        monitor = ScreenMonitor()
        supervisor = self._RecordingSupervisor()
        actions = [
            TriggerAction(action_type=ActionType.SEND_KEY, value="Down", delay_after=0.0),
            TriggerAction(action_type=ActionType.SEND_KEY, value="not a key", delay_after=0.0),
            TriggerAction(action_type=ActionType.SEND_KEY, value="Enter", delay_after=0.0),
        ]

        captured, original = StringIO(), sys.stderr
        sys.stderr = captured
        try:
            monitor._execute_action(actions, supervisor)
        finally:
            sys.stderr = original

        self.assertEqual(
            supervisor.keys,
            ["\x1b[B", "\r"],
            "a bad key spec swallowed the actions queued behind it",
        )
        self.assertIn("Trigger action skipped", captured.getvalue())
        self.assertIn("not a key", captured.getvalue())


class TestPrefixResolution(unittest.TestCase):

    def test_default_when_nothing_configured(self):
        binding = resolve_prefix(cli_value=None, env={}, config={})
        self.assertEqual(binding.spec, DEFAULT_PREFIX_SPEC)
        self.assertEqual(binding.byte, b"\x14")
        self.assertEqual(binding.source, "default")
        self.assertEqual(binding.label, "^T")

    def test_precedence_cli_over_env_over_config(self):
        env = {PREFIX_ENV_VAR: "C-g"}
        config = {"prefix": "C-]"}

        self.assertEqual(resolve_prefix("C-n", env, config).byte, b"\x0e")
        self.assertEqual(resolve_prefix(None, env, config).byte, b"\x07")
        self.assertEqual(resolve_prefix(None, {}, config).byte, b"\x1d")
        self.assertEqual(resolve_prefix(None, {}, {}).byte, b"\x14")

    def test_source_is_reported(self):
        self.assertEqual(resolve_prefix("C-g", {}, {}).source, "--prefix")
        self.assertEqual(
            resolve_prefix(None, {PREFIX_ENV_VAR: "C-g"}, {}).source,
            f"${PREFIX_ENV_VAR}",
        )

    def test_dangerous_bindings_are_refused_with_a_reason(self):
        for spec in ("C-c", "C-d", "C-s", "C-z", "C-q", "C-\\", "Enter"):
            with self.subTest(spec=spec):
                with self.assertRaises(KeySpecError) as ctx:
                    resolve_prefix(spec, {}, {})
                self.assertIn(spec.split("-")[-1].lower(), str(ctx.exception).lower())

    def test_escape_is_refused_because_it_prefixes_arrow_keys(self):
        with self.assertRaises(KeySpecError):
            resolve_prefix("Escape", {}, {})
        with self.assertRaises(KeySpecError):
            resolve_prefix("C-[", {}, {})

    def test_none_is_refused_because_stop_would_be_unreachable(self):
        with self.assertRaises(KeySpecError) as ctx:
            resolve_prefix("none", {}, {})
        self.assertIn("pause or stop", str(ctx.exception))

    def test_multi_byte_key_is_refused(self):
        with self.assertRaises(KeySpecError) as ctx:
            resolve_prefix("Up", {}, {})
        self.assertIn("single keystroke", str(ctx.exception))

    def test_unparseable_prefix_is_refused(self):
        with self.assertRaises(KeySpecError):
            resolve_prefix("gibberish", {}, {})

    def test_config_file_is_read(self):
        workdir = tempfile.mkdtemp(prefix="termreel_cfg_")
        try:
            path = os.path.join(workdir, "config.yaml")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write('live:\n  prefix: "C-a"\n')
            self.assertEqual(load_live_config(path), {"prefix": "C-a"})
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    def test_broken_config_file_does_not_break_recording(self):
        workdir = tempfile.mkdtemp(prefix="termreel_cfg_")
        try:
            path = os.path.join(workdir, "config.yaml")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("live: [this is not a mapping\n")
            self.assertEqual(load_live_config(path), {})
            self.assertEqual(load_live_config(os.path.join(workdir, "absent.yaml")), {})
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    def test_validate_reports_the_source_in_the_message(self):
        with self.assertRaises(KeySpecError) as ctx:
            validate_prefix_spec("C-c", "$TERMREEL_PREFIX")
        self.assertIn("$TERMREEL_PREFIX", str(ctx.exception))


class TestPrefixFSM(unittest.TestCase):

    def setUp(self):
        self.fsm = PrefixFSM(b"\x14")

    def test_ordinary_input_is_forwarded_untouched(self):
        forward, actions = self.fsm.feed(b"git status\r")
        self.assertEqual(forward, b"git status\r")
        self.assertEqual(actions, [])

    def test_prefix_plus_binding_is_consumed(self):
        forward, actions = self.fsm.feed(b"\x14p")
        self.assertEqual(forward, b"")
        self.assertEqual(actions, [ACTION_TOGGLE_PAUSE])

    def test_all_default_bindings(self):
        forward, actions = self.fsm.feed(b"\x14p\x14 \x14m\x14q")
        self.assertEqual(forward, b"")
        self.assertEqual(
            actions,
            [ACTION_TOGGLE_PAUSE, ACTION_TOGGLE_PAUSE, ACTION_MARK, ACTION_STOP],
        )

    def test_bindings_are_case_insensitive(self):
        _, actions = self.fsm.feed(b"\x14P\x14Q")
        self.assertEqual(actions, [ACTION_TOGGLE_PAUSE, ACTION_STOP])

    def test_double_prefix_types_a_literal_prefix(self):
        # Without this, rebinding only moves the collision somewhere else.
        forward, actions = self.fsm.feed(b"\x14\x14")
        self.assertEqual(forward, b"\x14")
        self.assertEqual(actions, [])

    def test_triple_prefix_is_literal_then_pending(self):
        forward, actions = self.fsm.feed(b"\x14\x14\x14")
        self.assertEqual(forward, b"\x14")
        self.assertEqual(actions, [])
        self.assertTrue(self.fsm.pending)
        forward, actions = self.fsm.feed(b"q")
        self.assertEqual(forward, b"")
        self.assertEqual(actions, [ACTION_STOP])

    def test_unbound_key_after_prefix_is_reported_and_swallowed(self):
        forward, actions = self.fsm.feed(b"\x14Z")
        self.assertEqual(forward, b"")
        self.assertEqual(actions, [ACTION_UNKNOWN])

    def test_prefix_state_survives_a_read_boundary(self):
        # A prefix and its key routinely land in different read() chunks.
        forward, actions = self.fsm.feed(b"echo hi\x14")
        self.assertEqual(forward, b"echo hi")
        self.assertEqual(actions, [])
        forward, actions = self.fsm.feed(b"q")
        self.assertEqual(forward, b"")
        self.assertEqual(actions, [ACTION_STOP])

    def test_bracketed_paste_suppresses_the_fsm(self):
        # A paste burst arrives as one read and can legitimately contain the
        # prefix byte; interpreting it would corrupt the pasted text.
        payload = b"line1 \x14p line2"
        data = PASTE_START + payload + PASTE_END
        forward, actions = self.fsm.feed(data)
        self.assertEqual(forward, data)
        self.assertEqual(actions, [])

    def test_paste_spanning_multiple_reads(self):
        forward1, actions1 = self.fsm.feed(PASTE_START + b"abc\x14")
        forward2, actions2 = self.fsm.feed(b"q def" + PASTE_END)
        self.assertEqual(forward1 + forward2, PASTE_START + b"abc\x14q def" + PASTE_END)
        self.assertEqual(actions1 + actions2, [])

    def test_paste_markers_split_across_reads(self):
        # The ESC[200~ marker itself straddling a chunk boundary.
        forward1, _ = self.fsm.feed(b"\x1b[20")
        forward2, actions = self.fsm.feed(b"0~\x14p" + PASTE_END)
        self.assertEqual(forward1 + forward2, b"\x1b[200~\x14p" + PASTE_END)
        self.assertEqual(actions, [])

    def test_hotkeys_work_again_after_a_paste_ends(self):
        self.fsm.feed(PASTE_START + b"x" + PASTE_END)
        forward, actions = self.fsm.feed(b"\x14q")
        self.assertEqual(forward, b"")
        self.assertEqual(actions, [ACTION_STOP])

    def test_alternate_prefix(self):
        fsm = PrefixFSM(b"\x01")
        forward, actions = fsm.feed(b"\x14p")
        self.assertEqual(forward, b"\x14p", "0x14 should be ordinary input for a C-a prefix")
        self.assertEqual(actions, [])
        forward, actions = fsm.feed(b"\x01q")
        self.assertEqual(actions, [ACTION_STOP])

    def test_prefix_must_be_one_byte(self):
        for prefix in (b"", b"ab"):
            with self.subTest(prefix=prefix):
                with self.assertRaises(ValueError):
                    PrefixFSM(prefix)

    def test_reset_clears_partial_state(self):
        self.fsm.feed(b"\x14")
        self.assertTrue(self.fsm.pending)
        self.fsm.reset()
        self.assertFalse(self.fsm.pending)
        forward, actions = self.fsm.feed(b"q")
        self.assertEqual(forward, b"q")
        self.assertEqual(actions, [])

    def test_empty_chunk_is_a_no_op(self):
        self.assertEqual(self.fsm.feed(b""), (b"", []))


class TestTerminalGuard(unittest.TestCase):
    """pty.openpty() gives a real tty, so raw mode is testable headlessly."""

    def test_raw_mode_is_entered_and_restored(self):
        master, slave = pty.openpty()
        try:
            before = termios.tcgetattr(slave)
            with TerminalGuard(slave, install_signal_handlers=False):
                during = termios.tcgetattr(slave)
                # Raw mode must clear ISIG, otherwise Ctrl-C would kill
                # termreel instead of reaching the recorded shell.
                self.assertTrue(before[3] & termios.ISIG)
                self.assertFalse(during[3] & termios.ISIG)
                self.assertFalse(during[3] & termios.ICANON)
                self.assertFalse(during[3] & termios.ECHO)
            after = termios.tcgetattr(slave)
            self.assertEqual(before, after)
        finally:
            os.close(master)
            os.close(slave)

    def test_restore_is_idempotent(self):
        master, slave = pty.openpty()
        try:
            before = termios.tcgetattr(slave)
            guard = TerminalGuard(slave, install_signal_handlers=False)
            guard.__enter__()
            guard.restore()
            guard.restore()
            guard.__exit__(None, None, None)
            self.assertEqual(termios.tcgetattr(slave), before)
        finally:
            os.close(master)
            os.close(slave)

    def test_terminal_is_restored_even_when_the_body_raises(self):
        master, slave = pty.openpty()
        try:
            before = termios.tcgetattr(slave)
            with self.assertRaises(RuntimeError):
                with TerminalGuard(slave, install_signal_handlers=False):
                    raise RuntimeError("boom")
            self.assertEqual(termios.tcgetattr(slave), before)
        finally:
            os.close(master)
            os.close(slave)

    def test_active_flag_tracks_raw_mode(self):
        master, slave = pty.openpty()
        try:
            guard = TerminalGuard(slave, install_signal_handlers=False)
            self.assertFalse(guard.active)
            with guard:
                self.assertTrue(guard.active)
            self.assertFalse(guard.active)
            self.assertTrue(guard.restored)
        finally:
            os.close(master)
            os.close(slave)

    def test_first_termination_signal_asks_the_owner_to_stop(self):
        """
        Exiting from the handler would skip the owner's teardown, which is
        where the encoder is finalised and the recorded child's process group
        is killed. The first signal must therefore delegate, not exit.
        """
        master, slave = pty.openpty()
        requested = []
        try:
            guard = TerminalGuard(
                slave,
                install_signal_handlers=False,
                on_terminate=requested.append,
            )
            with guard:
                guard._on_signal(signal.SIGTERM, None)
                self.assertEqual(requested, [signal.SIGTERM])
                # Still raw: the owner's own finally block restores it, after
                # it has finished writing out the recording.
                self.assertTrue(guard.active)
        finally:
            os.close(master)
            os.close(slave)

    def test_second_termination_signal_stops_delegating(self):
        """A wedged shutdown must not make the process unkillable."""
        master, slave = pty.openpty()
        requested = []
        chained = []
        try:
            before = termios.tcgetattr(slave)
            guard = TerminalGuard(
                slave,
                install_signal_handlers=False,
                on_terminate=requested.append,
            )
            with guard:
                # Stand in for a real previous handler so the fallback path is
                # exercised without the os._exit branch taking the test with it.
                guard._previous_handlers[signal.SIGTERM] = lambda s, f: chained.append(s)
                guard._on_signal(signal.SIGTERM, None)
                guard._on_signal(signal.SIGTERM, None)
            self.assertEqual(len(requested), 1, "the owner was asked to stop twice")
            self.assertEqual(chained, [signal.SIGTERM])
            self.assertEqual(termios.tcgetattr(slave), before)
        finally:
            os.close(master)
            os.close(slave)

    def test_a_raising_terminate_callback_does_not_wedge_the_handler(self):
        master, slave = pty.openpty()
        try:
            def explode(signum):
                raise RuntimeError("callback exploded")

            guard = TerminalGuard(slave, install_signal_handlers=False, on_terminate=explode)
            with guard:
                guard._on_signal(signal.SIGTERM, None)  # must not propagate
                self.assertTrue(guard.active)
        finally:
            os.close(master)
            os.close(slave)


class TestPassthroughLoop(unittest.TestCase):

    def test_forwards_input_and_dispatches_actions(self):
        master, slave = pty.openpty()
        received = bytearray()
        actions = []
        loop = PassthroughLoop(
            stdin_fd=slave,
            write_to_child=received.extend,
            fsm=PrefixFSM(b"\x14"),
            on_action=actions.append,
        )
        try:
            # Raw mode, as the recorder always runs it. In cooked mode the
            # slave's ICRNL would rewrite the CR to NL before we saw it.
            with TerminalGuard(slave, install_signal_handlers=False):
                loop.start()
                os.write(master, b"echo hello\r")
                os.write(master, b"\x14p")
                deadline = time.time() + 3.0
                while time.time() < deadline and (bytes(received) != b"echo hello\r" or not actions):
                    time.sleep(0.02)
                self.assertEqual(bytes(received), b"echo hello\r")
                self.assertEqual(actions, [ACTION_TOGGLE_PAUSE])
        finally:
            loop.stop()
            os.close(master)
            os.close(slave)

    def test_stop_unblocks_a_loop_sitting_in_select(self):
        master, slave = pty.openpty()
        loop = PassthroughLoop(
            stdin_fd=slave,
            write_to_child=lambda data: None,
            fsm=PrefixFSM(b"\x14"),
            on_action=lambda action: None,
        )
        try:
            loop.start()
            time.sleep(0.1)
            start = time.time()
            loop.stop(join_timeout=2.0)
            self.assertLess(time.time() - start, 1.5)
            self.assertTrue(loop.stopped)
        finally:
            os.close(master)
            os.close(slave)

    def test_loop_exits_on_stdin_eof(self):
        master, slave = pty.openpty()
        loop = PassthroughLoop(
            stdin_fd=slave,
            write_to_child=lambda data: None,
            fsm=PrefixFSM(b"\x14"),
            on_action=lambda action: None,
        )
        try:
            loop.start()
            time.sleep(0.1)
            os.close(master)
            deadline = time.time() + 3.0
            while time.time() < deadline and not loop.stopped:
                time.sleep(0.02)
            self.assertTrue(loop.stopped)
            self.assertIsNone(loop.error)
        finally:
            loop.stop()
            try:
                os.close(slave)
            except OSError:
                pass


class TestSinglePtyReader(unittest.TestCase):
    """
    §2.1: a pty master is a single-consumer stream.

    A second os.read() loop steals bytes from the supervisor's parser, so the
    output hook exists precisely so nobody needs one.
    """

    def test_a_pty_master_hands_each_byte_to_exactly_one_reader(self):
        """
        Deterministic proof of the single-consumer property, no threads.

        Two os.read() calls on the same master stand in for two readers: the
        first consumes its bytes outright and the second can never see them
        again. That is why `live` mirrors through on_output instead of opening
        a second read loop next to the supervisor's.
        """
        master, slave = pty.openpty()
        try:
            os.write(slave, b"FIRST_HALF__SECOND_HALF\n")

            ready, _, _ = select.select([master], [], [], 5.0)
            self.assertTrue(ready, "nothing arrived on the pty master")

            reader_a = os.read(master, 12)
            self.assertEqual(reader_a, b"FIRST_HALF__")

            ready, _, _ = select.select([master], [], [], 5.0)
            self.assertTrue(ready, "the remainder never arrived")
            reader_b = os.read(master, 4096)

            self.assertIn(b"SECOND_HALF", reader_b)
            self.assertNotIn(
                b"FIRST_HALF",
                reader_b,
                "the second reader saw bytes the first had already consumed; "
                "the single-consumer property no longer holds",
            )
        finally:
            os.close(master)
            os.close(slave)

    def test_the_mirror_receives_exactly_what_the_parser_consumed(self):
        """
        The guarantee `live` is built on: the on_output hook is fed the same
        byte stream the parser is, with nothing added and nothing dropped.

        Deliberately a byte-for-byte comparison rather than a marker search.
        Markers are a weak probe here because bash echoes the command as well
        as printing its output, so the same text legitimately appears twice.
        """
        mirrored = bytearray()
        supervisor = PtySupervisor(
            command="bash --norc --noprofile",
            rows=24,
            cols=80,
            on_output=mirrored.extend,
        )
        try:
            supervisor.start()
            for index in range(6):
                supervisor.send_text(f"echo MIRROR_{index}_PAYLOAD\r")
                time.sleep(0.15)
            self.assertTrue(supervisor.wait_for_output("MIRROR_5_PAYLOAD", timeout=8.0))
            time.sleep(0.4)

            with supervisor._lock:
                parsed_bytes = bytes(supervisor._raw_output_buffer)
            mirror_bytes = bytes(mirrored)

            self.assertTrue(parsed_bytes, "the parser consumed nothing")
            self.assertEqual(
                mirror_bytes,
                parsed_bytes,
                "the mirror and the parser disagree about the byte stream; "
                "a second consumer of the pty master has appeared",
            )
        finally:
            supervisor.terminate()

    def test_output_hook_sees_everything_the_parser_sees(self):
        mirrored = bytearray()
        supervisor = PtySupervisor(
            command="bash --norc --noprofile",
            rows=24,
            cols=80,
            on_output=mirrored.extend,
        )
        try:
            supervisor.start()
            for index in range(6):
                supervisor.send_text(f"echo MARKER_{index}_PAYLOAD\r")
                time.sleep(0.15)
            time.sleep(0.8)

            parsed = supervisor.state.get_rendered_text()
            mirror_text = mirrored.decode("utf-8", errors="replace")
            for index in range(6):
                marker = f"MARKER_{index}_PAYLOAD"
                self.assertIn(marker, mirror_text, f"{marker} missing from the mirror")
                self.assertIn(marker, parsed, f"{marker} missing from the parsed grid")
        finally:
            supervisor.terminate()

    def test_injected_state_is_the_one_that_gets_parsed(self):
        from termreel.emulator.parser import ANSIParser
        from termreel.emulator.state import TerminalState

        shared = TerminalState(rows=24, cols=80)
        supervisor = PtySupervisor(
            command="bash --norc --noprofile",
            rows=24,
            cols=80,
            state=shared,
            parser=ANSIParser(shared),
        )
        try:
            supervisor.start()
            supervisor.send_text("echo INJECTED_STATE_PROBE\r")
            self.assertTrue(supervisor.wait_for_output("INJECTED_STATE_PROBE", timeout=5.0))
            self.assertIs(supervisor.state, shared)
            self.assertIn("INJECTED_STATE_PROBE", shared.get_rendered_text())
        finally:
            supervisor.terminate()

    def test_a_slow_output_hook_does_not_lose_parser_data(self):
        def slow_hook(chunk):
            time.sleep(0.05)

        supervisor = PtySupervisor(
            command="bash --norc --noprofile",
            rows=24,
            cols=80,
            on_output=slow_hook,
        )
        try:
            supervisor.start()
            supervisor.send_text("echo SLOW_HOOK_PROBE\r")
            self.assertTrue(supervisor.wait_for_output("SLOW_HOOK_PROBE", timeout=8.0))
        finally:
            supervisor.terminate()

    def test_a_raising_output_hook_does_not_kill_the_reader(self):
        def broken_hook(chunk):
            raise RuntimeError("hook exploded")

        supervisor = PtySupervisor(
            command="bash --norc --noprofile",
            rows=24,
            cols=80,
            on_output=broken_hook,
        )
        try:
            supervisor.start()
            supervisor.send_text("echo BROKEN_HOOK_PROBE\r")
            self.assertTrue(supervisor.wait_for_output("BROKEN_HOOK_PROBE", timeout=8.0))
        finally:
            supervisor.terminate()


class TestControllingTerminal(unittest.TestCase):
    """
    §2.2: the child must own the PTY as its controlling terminal.

    Linux granted one implicitly to a session leader that opened a tty, which
    is why this went unnoticed; BSD and macOS require the explicit ioctl.
    """

    def test_non_shell_child_gets_a_controlling_terminal(self):
        # A list argv skips the shell wrapper that used to paper over this.
        script = (
            "import os,sys\n"
            "try:\n"
            "    fd=os.open('/dev/tty', os.O_RDWR)\n"
            "    os.write(fd, b'CTTY_OK\\n')\n"
            "    os.close(fd)\n"
            "except OSError as e:\n"
            "    sys.stdout.write('CTTY_FAIL %s\\n' % e)\n"
            "sys.stdout.flush()\n"
        )
        supervisor = PtySupervisor(
            command=[sys.executable, "-c", script],
            rows=24,
            cols=80,
        )
        try:
            supervisor.start()
            self.assertTrue(
                supervisor.wait_for_output("CTTY_OK", timeout=8.0),
                f"child could not open /dev/tty; screen was:\n{supervisor.state.get_rendered_text()}",
            )
        finally:
            supervisor.terminate()

    def test_interactive_shell_reports_job_control(self):
        supervisor = PtySupervisor(command="bash --norc --noprofile -i", rows=24, cols=80)
        try:
            supervisor.start()
            supervisor.send_text("case $- in *m*) echo JOBCTL_ON;; *) echo JOBCTL_OFF;; esac\r")
            self.assertTrue(
                supervisor.wait_for_output("JOBCTL_ON", timeout=8.0),
                f"job control is off; screen was:\n{supervisor.state.get_rendered_text()}",
            )
        finally:
            supervisor.terminate()


class TestGridGeometry(unittest.TestCase):

    def test_known_canvas_sizes(self):
        self.assertEqual(grid_for_pixels(1280, 720), (131, 29))
        self.assertEqual(grid_for_pixels(1920, 1080), (202, 48))

    def test_helper_agrees_with_the_renderer(self):
        for width, height in ((1280, 720), (1920, 1080), (800, 600)):
            with self.subTest(size=(width, height)):
                renderer = CairoTerminalRenderer(width=width, height=height)
                self.assertEqual(grid_for_pixels(width, height), (renderer.cols, renderer.rows))

    def test_round_trip_across_the_full_size_range(self):
        for cols in range(10, 280, 9):
            for rows in range(5, 100, 7):
                with self.subTest(cols=cols, rows=rows):
                    width, height = pixels_for_grid(cols, rows)
                    self.assertEqual(grid_for_pixels(width, height), (cols, rows))

    def test_round_trip_through_an_actual_renderer(self):
        for cols, rows in ((80, 24), (100, 30), (202, 48), (272, 72)):
            with self.subTest(cols=cols, rows=rows):
                width, height = pixels_for_grid(cols, rows)
                renderer = CairoTerminalRenderer(width=width, height=height)
                self.assertEqual((renderer.cols, renderer.rows), (cols, rows))

    def test_minimums_are_enforced(self):
        width, height = pixels_for_grid(1, 1)
        self.assertEqual(grid_for_pixels(width, height), (10, 5))


class TestBlendFrames(unittest.TestCase):

    def setUp(self):
        self.width, self.height = 32, 16
        stride = self.width * 4
        self.size = stride * self.height
        self.black = bytes([0, 0, 0, 255]) * (self.width * self.height)
        self.white = bytes([255, 255, 255, 255]) * (self.width * self.height)

    def test_alpha_zero_keeps_the_base(self):
        self.assertEqual(blend_frames(self.black, self.white, 0.0, self.width, self.height), self.black)

    def test_alpha_one_replaces_with_the_overlay(self):
        self.assertEqual(blend_frames(self.black, self.white, 1.0, self.width, self.height), self.white)

    def test_midpoint_lands_between_the_two(self):
        blended = blend_frames(self.black, self.white, 0.5, self.width, self.height)
        self.assertEqual(len(blended), self.size)
        channel = blended[0]
        self.assertGreater(channel, 100)
        self.assertLess(channel, 155)

    def test_alpha_is_monotonic(self):
        previous = -1
        for step in range(0, 11):
            value = blend_frames(self.black, self.white, step / 10.0, self.width, self.height)[0]
            self.assertGreaterEqual(value, previous)
            previous = value

    def test_out_of_range_alpha_is_clamped(self):
        self.assertEqual(blend_frames(self.black, self.white, -3.0, self.width, self.height), self.black)
        self.assertEqual(blend_frames(self.black, self.white, 9.0, self.width, self.height), self.white)

    def test_wrong_sized_buffer_raises(self):
        with self.assertRaises(ValueError):
            blend_frames(self.black, self.white[:-4], 0.5, self.width, self.height)

    def test_blends_real_rendered_frames(self):
        renderer = CairoTerminalRenderer(width=400, height=300)
        from termreel.emulator.state import TerminalState

        state = TerminalState(rows=renderer.rows, cols=renderer.cols)
        first = renderer.draw_frame(state, status_pill="● REC 00:01")
        for char in "SECOND FRAME":
            state.write_char(char)
        second = renderer.draw_frame(state, status_pill="● REC 00:02")
        self.assertNotEqual(first, second)
        blended = blend_frames(second, first, 0.5, 400, 300)
        self.assertEqual(len(blended), len(first))
        self.assertNotEqual(blended, first)
        self.assertNotEqual(blended, second)


class TestKeyProbe(unittest.TestCase):

    def test_safe_keys(self):
        self.assertEqual(classify(b"\x14")[0], "safe")
        self.assertEqual(classify(b"\x07")[0], "safe")

    def test_refused_keys(self):
        for data in (b"\x03", b"\x04", b"\x13", b"\x1a"):
            with self.subTest(data=data):
                self.assertEqual(classify(data)[0], "refused")

    def test_contested_keys(self):
        self.assertEqual(classify(b"\x01")[0], "contested")
        self.assertEqual(classify(b"\x02")[0], "contested")

    def test_escape_sequences_are_not_usable_as_prefixes(self):
        verdict, explanation = classify(b"\x1b[A")
        self.assertEqual(verdict, "not-a-key")
        self.assertIn("escape sequence", explanation)

    def test_option_accented_character_is_explained(self):
        verdict, explanation = classify("é".encode("utf-8"))
        self.assertEqual(verdict, "not-a-key")
        self.assertIn("Option", explanation)

    def test_report_lines_mention_the_bytes(self):
        line = format_report(b"\x14")
        self.assertIn("0x14", line)
        self.assertIn("C-t", line)


@unittest.skipIf(shutil.which("ffmpeg") is None, "ffmpeg is required")
class TestLiveRecordingEndToEnd(unittest.TestCase):
    """
    Drive a real recording through a PTY pair and check the encoded result.

    Exercises the pause gate, the crossfade, deadline pacing and the cast
    clock against actual ffmpeg output rather than internal counters alone.
    """

    def setUp(self):
        self.workdir = tempfile.mkdtemp(prefix="termreel_live_test_")
        self.master, self.slave = pty.openpty()
        self.mirrored = bytearray()
        self._drain_stop = threading.Event()
        self._drainer = threading.Thread(target=self._drain, daemon=True)
        self._drainer.start()

    def tearDown(self):
        self._drain_stop.set()
        for fd in (self.master, self.slave):
            try:
                os.close(fd)
            except OSError:
                pass
        shutil.rmtree(self.workdir, ignore_errors=True)

    def _drain(self):
        """
        Consume mirrored output so the pty buffer cannot fill and stall.

        Kept rather than discarded: this is byte for byte what the operator's
        terminal would have received, which is the only way to check that
        TermReel's own status lines are emitted in a form raw mode can render.
        """
        while not self._drain_stop.is_set():
            try:
                ready, _, _ = select.select([self.master], [], [], 0.05)
            except (OSError, ValueError):
                return
            if ready:
                try:
                    chunk = os.read(self.master, 65536)
                except OSError:
                    return
                if not chunk:
                    return
                self.mirrored.extend(chunk)

    def _recorder(self, **overrides):
        options = dict(
            command="bash --norc --noprofile",
            output=os.path.join(self.workdir, "live.mp4"),
            fps=10,
            cols=60,
            rows=16,
            crossfade=0.5,
            prefix=resolve_prefix("C-t", {}, {}),
            stdin_fd=self.slave,
            stdout_fd=self.slave,
            cwd=self.workdir,
            verbose=False,
            enable_telemetry=False,
        )
        options.update(overrides)
        return LiveRecorder(**options)

    def _run(self, recorder, script):
        """Run the recorder on a thread while `script` types at it."""
        result = {}

        def target():
            try:
                result["report"] = recorder.run()
            except BaseException as exc:  # surfaced by the assertion below
                result["error"] = exc

        thread = threading.Thread(target=target)
        thread.start()
        try:
            script(lambda data: os.write(self.master, data))
        finally:
            os.write(self.master, b"\x14q")
            thread.join(timeout=30)
        self.assertFalse(thread.is_alive(), "recorder did not shut down")
        self.assertNotIn("error", result, f"recorder raised: {result.get('error')}")
        return result["report"]

    def test_pause_writes_no_frames_and_is_cut_from_the_video(self):
        recorder = self._recorder()

        def script(send):
            time.sleep(1.2)
            send(b"echo PHASE_ONE\r")
            time.sleep(1.5)

            before = recorder.frames_written
            send(b"\x14p")
            time.sleep(0.3)
            self.assertTrue(recorder.paused)
            time.sleep(2.0)
            self.during_pause = recorder.frames_written - before

            # The child keeps running while recording is paused.
            send(b"echo WHILE_PAUSED\r")
            time.sleep(0.8)

            send(b"\x14p")
            time.sleep(0.3)
            self.assertFalse(recorder.paused)
            send(b"echo PHASE_TWO\r")
            time.sleep(1.5)

        report = self._run(recorder, script)

        self.assertEqual(report.status, "pass", report.error_message)
        self.assertEqual(self.during_pause, 0, "frames were written while paused")
        self.assertEqual(report.frame_errors, 0)
        self.assertGreater(report.paused_seconds, 1.5)

        # Paused wall time must not appear in the video.
        self.assertGreater(report.wall_seconds - report.video_seconds, 1.5)

        duration = ffprobe_duration(report.output_file)
        self.assertIsNotNone(duration)
        self.assertAlmostEqual(
            duration,
            report.frames_written / float(report.fps),
            delta=0.3,
            msg=f"container duration {duration} disagrees with {report.frames_written} frames",
        )

        screen = recorder.state.get_rendered_text()
        self.assertIn("PHASE_TWO", screen, "post-resume output never reached the grid")

    def test_crossfade_adds_frames_and_hard_cuts_does_not(self):
        counts = {}
        for label, options in (
            ("crossfade", {"crossfade": 0.8}),
            ("hard", {"hard_cuts": True}),
        ):
            recorder = self._recorder(
                output=os.path.join(self.workdir, f"{label}.mp4"), **options
            )

            def script(send, rec=recorder):
                time.sleep(1.0)
                send(b"\x14p")
                time.sleep(0.6)
                rec._transition_baseline = rec.frames_written
                send(b"\x14p")
                time.sleep(1.2)

            report = self._run(recorder, script)
            self.assertEqual(report.status, "pass", report.error_message)
            counts[label] = report.frames_written - recorder._transition_baseline

        # A 0.8s crossfade at 10fps inserts ~8 extra frames the hard cut skips.
        self.assertGreater(
            counts["crossfade"],
            counts["hard"] + 4,
            f"crossfade did not add blend frames: {counts}",
        )

    def test_cast_timestamps_follow_video_time_not_wall_time(self):
        cast_path = os.path.join(self.workdir, "live.cast")
        recorder = self._recorder(cast=cast_path)

        def script(send):
            time.sleep(1.0)
            send(b"echo BEFORE_PAUSE\r")
            time.sleep(1.0)
            send(b"\x14p")
            time.sleep(2.5)
            send(b"\x14p")
            time.sleep(0.3)
            send(b"echo AFTER_PAUSE\r")
            time.sleep(1.2)

        report = self._run(recorder, script)
        self.assertEqual(report.status, "pass", report.error_message)

        with open(cast_path, "r", encoding="utf-8") as handle:
            lines = [line for line in handle.read().splitlines() if line.strip()]
        header = json.loads(lines[0])
        self.assertEqual(header["version"], 2)
        events = [json.loads(line) for line in lines[1:]]
        self.assertTrue(events, "no cast events were recorded")

        timestamps = [event[0] for event in events]
        self.assertEqual(timestamps, sorted(timestamps), "cast timestamps went backwards")
        # Wall time spanned >6s but the video is much shorter; the last cast
        # timestamp must track the video, not the wall clock.
        self.assertLessEqual(timestamps[-1], report.video_seconds + 0.5)
        self.assertLess(timestamps[-1], report.wall_seconds - 1.0)

        payload = "".join(event[2] for event in events)
        self.assertIn("AFTER_PAUSE", payload)

    def test_socket_pause_resume_and_stop(self):
        import socket

        recorder = self._recorder(enable_telemetry=True)

        def call(method):
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(5.0)
                client.connect(recorder.telemetry.socket_path)
                client.sendall((json.dumps({"method": method, "id": 1}) + "\n").encode("utf-8"))
                return json.loads(client.recv(65536).decode("utf-8").splitlines()[0])

        def script(send):
            deadline = time.time() + 8.0
            while time.time() < deadline and recorder.telemetry is None:
                time.sleep(0.05)
            self.assertIsNotNone(recorder.telemetry, "telemetry server never came up")
            time.sleep(0.5)

            self.assertTrue(call("PAUSE")["result"]["paused"])
            self.assertTrue(recorder.paused)
            time.sleep(0.5)

            self.assertFalse(call("RESUME")["result"]["paused"])
            self.assertFalse(recorder.paused)
            time.sleep(0.5)

            self.assertTrue(call("TOGGLE_PAUSE")["result"]["paused"])
            call("TOGGLE_PAUSE")
            time.sleep(0.3)

        report = self._run(recorder, script)
        self.assertEqual(report.status, "pass", report.error_message)

    def test_rejects_a_non_tty_stdin(self):
        from termreel.exceptions import TermReelError

        read_fd, write_fd = os.pipe()
        try:
            recorder = self._recorder(stdin_fd=read_fd)
            with self.assertRaises(TermReelError) as ctx:
                recorder.preflight()
            self.assertIn("interactive terminal", str(ctx.exception))
        finally:
            os.close(read_fd)
            os.close(write_fd)

    def test_status_pill_reflects_state(self):
        recorder = self._recorder()
        self.assertTrue(recorder.status_pill().startswith("● REC"))
        self.assertEqual(recorder.status_color(), recorder.renderer.theme.traffic_close)
        recorder._paused = True
        self.assertEqual(recorder.status_pill(), "⏸ PAUSED")
        self.assertEqual(recorder.status_color(), recorder.renderer.theme.traffic_minimize)

    def test_operator_status_lines_are_crlf_terminated_in_raw_mode(self):
        """
        tty.setraw clears OPOST, so a bare "\\n" moves down without returning
        the carriage. Status lines written that way stair-step diagonally
        across the operator's screen, each one further right than the last.
        """
        recorder = self._recorder(verbose=True)

        def script(send):
            time.sleep(1.0)
            send(b"\x14p")     # pause   -> "Paused. Nothing is being recorded."
            time.sleep(0.6)
            send(b"\x14p")     # resume  -> "Recording."
            time.sleep(0.4)
            send(b"\x14?")     # help
            time.sleep(0.4)
            send(b"\x14z")     # unbound -> hint
            time.sleep(0.4)

        report = self._run(recorder, script)
        self.assertEqual(report.status, "pass", report.error_message)

        stream = bytes(self.mirrored)
        self.assertIn(b"[termreel] Paused.", stream, "the operator was never told it paused")
        self.assertIn(b"[termreel] Recording.", stream)
        self.assertIn(b"pause/resume", stream, "^T ? printed no help")
        self.assertIn(b"Unbound key.", stream)

        for index, byte in enumerate(stream):
            if byte == 0x0A and (index == 0 or stream[index - 1] != 0x0D):
                context = stream[max(0, index - 60):index + 1]
                self.fail(
                    "bare LF (no preceding CR) written to a raw-mode terminal; "
                    f"the line before it was {context!r}"
                )

    def test_help_is_not_silenced_by_quiet(self):
        """`^T ?` is an explicit request, not the status noise -q suppresses."""
        recorder = self._recorder(verbose=False)

        def script(send):
            time.sleep(1.0)
            send(b"\x14p")      # would print "Paused." if -q were ignored
            time.sleep(0.4)
            send(b"\x14p")      # would print "Recording."
            time.sleep(0.4)
            send(b"\x14?")      # must print regardless
            time.sleep(0.5)

        report = self._run(recorder, script)
        self.assertEqual(report.status, "pass", report.error_message)
        stream = bytes(self.mirrored)
        self.assertIn(b"pause/resume", stream, "--quiet swallowed the help hotkey")
        self.assertNotIn(b"[termreel] Paused.", stream, "--quiet did not suppress status")
        self.assertNotIn(b"[termreel] Recording.", stream)

    def test_resizing_the_window_mid_session_reaches_the_child(self):
        """
        The grid is clamped to the locked canvas, not frozen at startup. If a
        mid-session resize never reaches the child, its winsize goes stale and
        everything it prints from then on wraps at the wrong column.
        """
        set_winsize(self.slave, 20, 50)
        recorder = self._recorder(cols=90, rows=30)

        def script(send):
            time.sleep(1.2)
            self.initial = (recorder.supervisor.rows, recorder.supervisor.cols)

            set_winsize(self.slave, 24, 70)
            deadline = time.time() + 5.0
            while time.time() < deadline and (recorder.supervisor.rows,
                                              recorder.supervisor.cols) != (24, 70):
                time.sleep(0.05)
            self.after_resize = (recorder.supervisor.rows, recorder.supervisor.cols)

            send(b"echo COLS=$(tput cols) ROWS=$(tput lines)\r")
            time.sleep(1.5)

        report = self._run(recorder, script)
        self.assertEqual(report.status, "pass", report.error_message)
        self.assertEqual(self.initial, (20, 50), "startup clamp did not follow the terminal")
        self.assertEqual(self.after_resize, (24, 70), "resize never reached the supervisor")
        self.assertIn(
            "COLS=70 ROWS=24",
            recorder.state.get_rendered_text(),
            "the child still believes it has the old geometry",
        )

    def test_a_terminal_larger_than_the_locked_grid_warns_and_clamps(self):
        set_winsize(self.slave, 40, 120)
        recorder = self._recorder(cols=50, rows=15, verbose=True)

        def script(send):
            time.sleep(1.5)

        report = self._run(recorder, script)
        self.assertEqual(report.status, "pass", report.error_message)
        self.assertEqual(
            (recorder.supervisor.rows, recorder.supervisor.cols),
            (15, 50),
            "the child was allowed to exceed the locked recording grid",
        )
        self.assertIn(b"locked at", bytes(self.mirrored),
                      "the operator was never told their terminal is being cropped")

    def test_stopping_while_paused_still_counts_the_final_pause(self):
        recorder = self._recorder()

        def script(send):
            time.sleep(1.0)
            send(b"\x14p")
            time.sleep(1.6)
            self.assertTrue(recorder.paused)
            # _run sends ^T q next, i.e. we stop without ever resuming.

        report = self._run(recorder, script)
        self.assertEqual(report.status, "pass", report.error_message)
        self.assertGreater(
            report.paused_seconds,
            1.2,
            "the pause that was still open at shutdown was dropped from the total",
        )
        self.assertGreater(report.wall_seconds - report.video_seconds, 1.2)


@unittest.skipIf(shutil.which("ffmpeg") is None, "ffmpeg is required")
class TestLiveSurvivesTermination(unittest.TestCase):
    """
    A live recording's only ordinary exit is the stop hotkey, so SIGTERM is
    the path an operator hits when something goes wrong -- a window manager
    closing the tab, a supervisor reaping the job, a plain `kill`.

    Exiting from inside the signal handler restored the terminal but skipped
    every other piece of teardown: run() never returned, the asciicast tail
    was never flushed and the recorded shell was never reaped.
    """

    CHILD = r'''
import os, pty, select, sys, threading, time
sys.path.insert(0, {repo!r})
from termreel.live.config import resolve_prefix
from termreel.live.recorder import LiveRecorder

workdir = sys.argv[1]
master, slave = pty.openpty()

def drain():
    while True:
        ready, _, _ = select.select([master], [], [], 0.05)
        if ready:
            try:
                if not os.read(master, 65536):
                    return
            except OSError:
                return

threading.Thread(target=drain, daemon=True).start()

recorder = LiveRecorder(
    command="bash --norc --noprofile",
    output=os.path.join(workdir, "sigterm.mp4"),
    cast=os.path.join(workdir, "sigterm.cast"),
    fps=10, cols=60, rows=16,
    prefix=resolve_prefix("C-t", {{}}, {{}}),
    stdin_fd=slave, stdout_fd=slave, cwd=workdir,
    verbose=False, enable_telemetry=False,
)

def feed():
    time.sleep(1.2)
    os.write(master, b"echo TERM_PROBE_MARKER\r")

threading.Thread(target=feed, daemon=True).start()
print("READY", flush=True)
report = recorder.run()
print("SHELLPID", recorder.supervisor.process.pid, flush=True)
print("FRAMES", report.frames_written, flush=True)
'''

    def test_sigterm_finalises_the_recording_instead_of_abandoning_it(self):
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        workdir = tempfile.mkdtemp(prefix="termreel_sigterm_")
        try:
            script = self.CHILD.format(repo=repo)
            proc = subprocess.Popen(
                [sys.executable, "-c", script, workdir],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            deadline = time.time() + 20.0
            while time.time() < deadline and not os.path.exists(
                os.path.join(workdir, "sigterm.cast")
            ):
                time.sleep(0.1)
            time.sleep(3.5)  # let it record, and start the background job

            proc.send_signal(signal.SIGTERM)
            try:
                output = proc.communicate(timeout=25)[0]
            except subprocess.TimeoutExpired:
                proc.kill()
                output = proc.communicate()[0]
                self.fail(f"SIGTERM did not bring the recording down; output was:\n{output}")

            self.assertIn(
                "FRAMES",
                output,
                f"run() never returned, so teardown was skipped; output was:\n{output}",
            )
            frames = int([l for l in output.splitlines() if l.startswith("FRAMES")][0].split()[1])
            self.assertGreater(frames, 10, "almost nothing was recorded before the signal")

            cast = os.path.join(workdir, "sigterm.cast")
            payload = "".join(
                json.loads(line)[2]
                for line in open(cast, encoding="utf-8").read().splitlines()[1:]
                if line.strip()
            )
            self.assertIn(
                "TERM_PROBE_MARKER",
                payload,
                "the asciicast was never flushed on the way out",
            )

            video = os.path.join(workdir, "sigterm.mp4")
            deadline = time.time() + 10.0
            duration = None
            while time.time() < deadline:
                duration = ffprobe_duration(video)
                if duration:
                    break
                time.sleep(0.3)
            self.assertIsNotNone(duration, "the encoder produced nothing readable")
            self.assertAlmostEqual(duration, frames / 10.0, delta=0.5)

            # The recorded shell itself must be reaped, which only happens if
            # the teardown path ran at all.
            shell_pid = int(
                [l for l in output.splitlines() if l.startswith("SHELLPID")][0].split()[1]
            )
            listing = subprocess.run(
                ["ps", "-o", "pid=", "-p", str(shell_pid)], capture_output=True, text=True
            ).stdout.strip()
            self.assertEqual(
                listing,
                "",
                f"the recorded shell (pid {shell_pid}) outlived the recording",
            )
        finally:
            shutil.rmtree(workdir, ignore_errors=True)


class TestBothBackendsRenderContent(unittest.TestCase):
    """
    §3.1: `--backend pty` used to render an empty grid with only a cursor,
    because the supervisor parsed into a TerminalState nothing ever drew.
    """

    def _render_backend(self, backend):
        from termreel.scenario.runner import ScenarioRunner
        from termreel.scenario.schema import ScenarioManifest

        workdir = tempfile.mkdtemp(prefix=f"termreel_backend_{backend}_")
        manifest = ScenarioManifest.from_dict({
            "version": "1.0",
            "metadata": {
                "title": "backend probe",
                "output": os.path.join(workdir, f"{backend}.mp4"),
                "fps": 8,
                "resolution": [800, 600],
            },
            "timeline": [
                {"launch": {"command": "bash --norc --noprofile"}},
                {"type": {
                    "text": "echo HELLO_BACKEND_PROBE",
                    "speed": 0.005,
                    "send_key": "Enter",
                    "pause": 1.2,
                }},
            ],
        })
        runner = ScenarioRunner(manifest=manifest, backend=backend, verbose=False)
        try:
            runner.run()
            return runner.state.get_rendered_text(), runner.renderer
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    @unittest.skipIf(shutil.which("ffmpeg") is None, "ffmpeg is required")
    def test_pty_backend_renders_the_same_content_as_tmux(self):
        pty_text, pty_renderer = self._render_backend("pty")
        self.assertIn(
            "HELLO_BACKEND_PROBE",
            pty_text,
            f"pty backend produced a blank grid:\n{pty_text!r}",
        )

        if shutil.which("tmux") is not None:
            tmux_text, _ = self._render_backend("tmux")
            self.assertIn("HELLO_BACKEND_PROBE", tmux_text)

    @unittest.skipIf(shutil.which("ffmpeg") is None, "ffmpeg is required")
    def test_pty_backend_frame_differs_from_a_blank_frame(self):
        from termreel.emulator.state import TerminalState
        from termreel.scenario.runner import ScenarioRunner
        from termreel.scenario.schema import ScenarioManifest

        workdir = tempfile.mkdtemp(prefix="termreel_pixel_")
        try:
            manifest = ScenarioManifest.from_dict({
                "version": "1.0",
                "metadata": {
                    "title": "pixel probe",
                    "output": os.path.join(workdir, "pixels.mp4"),
                    "fps": 8,
                    "resolution": [800, 600],
                },
                "timeline": [
                    {"launch": {"command": "bash --norc --noprofile"}},
                    {"type": {
                        "text": "echo PIXEL_PROBE_TEXT",
                        "speed": 0.005,
                        "send_key": "Enter",
                        "pause": 1.2,
                    }},
                ],
            })
            runner = ScenarioRunner(manifest=manifest, backend="pty", verbose=False)
            runner.run()

            renderer = runner.renderer
            recorded = renderer.draw_frame(runner.state, status_pill="")

            blank = TerminalState(
                rows=renderer.rows,
                cols=renderer.cols,
                default_fg=renderer.theme.default_fg,
                default_bg=renderer.theme.terminal_bg,
                palette=renderer.theme.palette,
            )
            blank.cursor_visible = runner.state.cursor_visible
            blank.cursor.row = runner.state.cursor.row
            blank.cursor.col = runner.state.cursor.col
            empty = renderer.draw_frame(blank, status_pill="")

            self.assertNotEqual(
                recorded,
                empty,
                "the pty backend rendered pixels identical to an empty grid",
            )
        finally:
            shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
