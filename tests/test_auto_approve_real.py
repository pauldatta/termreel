"""
Auto-approve against real programs on real terminals.

The yes/no trigger used to key prompt instances by counting identical
matching lines. Once identical prompts filled the screen and started
scrolling, the count stopped changing, so the next prompt was never answered
and the scenario hung. These tests drive real bash ``read`` loops through a
real PTY and a real tmux pane with more prompts than screen rows, and check
that every prompt gets exactly one answer: counted both by the injections
TermReel reports and by what the child actually read.
"""

import os
import shutil
import tempfile
import time
import unittest

from termreel.reactor.monitor import ScreenMonitor
from termreel.reactor.triggers import create_agy_permission_dialog_trigger, create_yes_no_prompt_trigger
from termreel.supervisor.pty_session import PtySupervisor
from termreel.supervisor.tmux_session import TmuxSupervisor

ROWS = 6
PROMPTS = 14  # more than twice the screen height, so prompts scroll


def _write_script(workdir: str, body: str) -> str:
    path = os.path.join(workdir, "prompts.sh")
    with open(path, "w") as fh:
        fh.write("#!/bin/bash\ncd \"$(dirname \"$0\")\"\n" + body)
    os.chmod(path, 0o755)
    return path


def _loop_script(n: int, silent: bool) -> str:
    # silent: the answer is not echoed (read -s), so the only thing that
    # tells two prompts apart is their position/scroll count.
    read = "read -s -r -p 'Continue? [y/N] ' a; echo" if silent else \
        "read -r -p 'Continue? [y/N] ' a"
    return (
        "n=0\n"
        f"while [ $n -lt {n} ]; do\n"
        f"  {read}\n"
        "  n=$((n+1))\n"
        "  echo \"$a\" >> answers.log\n"
        "done\n"
        "echo ALL_DONE\n"
        "sleep 60\n"
    )


def _answers(workdir: str):
    try:
        with open(os.path.join(workdir, "answers.log")) as fh:
            return fh.read().split("\n")[:-1]
    except FileNotFoundError:
        return []


def _drive(sup, trigger, done, timeout: float, settle: float = 1.0):
    """Evaluate triggers at ~30 Hz like the runner's capture loop."""
    monitor = ScreenMonitor(supervisor=sup, triggers=[trigger])
    deadline = time.time() + timeout
    finished_at = None
    while time.time() < deadline:
        monitor.evaluate_and_react(sup, async_action=True)
        if finished_at is None and done():
            finished_at = time.time()
        if finished_at is not None and time.time() - finished_at >= settle:
            break
        time.sleep(1 / 30)
    monitor.wait_for_actions(timeout=3.0)
    return monitor.injections


def _fast_trigger():
    return create_yes_no_prompt_trigger(response="y", delay_before=0.05, delay_after=0.05)


def _fast_dialog_trigger():
    # The shipped permission-dialog trigger (edge="presence"), with short
    # delays so the test does not take minutes.
    return create_agy_permission_dialog_trigger(choice=1, delay_before=0.05, delay_after=0.05)


class _BackendMixin:
    backend = None

    def setUp(self):
        self.workdir = tempfile.mkdtemp(prefix="termreel_autoapprove_")
        self.sup = None

    def tearDown(self):
        if self.sup is not None:
            try:
                self.sup.terminate()
            except Exception:
                pass
        shutil.rmtree(self.workdir, ignore_errors=True)

    def _start(self, body: str, rows: int = ROWS):
        script = _write_script(self.workdir, body)
        cls = PtySupervisor if self.backend == "pty" else TmuxSupervisor
        self.sup = cls(command=f"bash --norc --noprofile {script}", cwd=self.workdir,
                       rows=rows, cols=60)
        self.sup.start()

    def _screen_done(self):
        return "ALL_DONE" in self.sup.capture_plain()

    def test_slow_every_scrolling_identical_prompt_is_answered_once(self):
        self._start(_loop_script(PROMPTS, silent=False))
        injections = _drive(self.sup, _fast_trigger(), self._screen_done, timeout=40)
        self.assertTrue(self._screen_done(),
                        f"loop never finished; screen:\n{self.sup.capture_plain()}")
        self.assertEqual(_answers(self.workdir), ["y"] * PROMPTS)
        self.assertEqual(len(injections), PROMPTS,
                         f"expected one injection per prompt, got {len(injections)}")

    def test_slow_unechoed_prompts_are_told_apart_by_scrolling(self):
        self._start(_loop_script(PROMPTS, silent=True))
        injections = _drive(self.sup, _fast_trigger(), self._screen_done, timeout=40)
        self.assertTrue(self._screen_done(),
                        f"loop never finished; screen:\n{self.sup.capture_plain()}")
        self.assertEqual(_answers(self.workdir), ["y"] * PROMPTS)
        self.assertEqual(len(injections), PROMPTS)

    def test_slow_prompt_left_on_screen_is_not_answered_again(self):
        # The prompt stays at the cursor, unanswered on screen (echo off),
        # for 3 s while a ticker keeps redrawing the top row. A level trigger
        # would re-send "y" every cooldown; the edge trigger must send once.
        body = (
            "printf '\\n\\n\\n'\n"
            "stty -echo\n"
            "( for i in 1 2 3 4 5 6 7 8 9 10 11 12; do"
            " printf '\\0337\\033[1;1Htick %s\\0338' $i; sleep 0.25; done ) &\n"
            "printf 'Continue? [y/N] '\n"
            "sleep 3.2\n"
            "read -r -t 1 a\n"
            "echo \"$a\" >> answers.log\n"
            "read -r -t 0.5 extra && echo \"EXTRA:$extra\" >> answers.log\n"
            "stty echo\n"
            "echo; echo ALL_DONE\n"
            "sleep 60\n"
        )
        self._start(body)
        injections = _drive(self.sup, _fast_trigger(), self._screen_done, timeout=15)
        self.assertTrue(self._screen_done(),
                        f"script never finished; screen:\n{self.sup.capture_plain()}")
        self.assertEqual(_answers(self.workdir), ["y"])
        self.assertEqual(len(injections), 1)

    # --- permission dialogs (edge="presence") -------------------------------

    def test_slow_sequential_dialogs_in_scrolling_output_each_answered_once(self):
        # Each dialog stays visible above the next one. Re-arming only when
        # the text left the screen answered the first dialog and then hung.
        n = 6
        body = (
            f"for i in $(seq {n}); do\n"
            "  printf 'Requesting permission for: step %s\\n' $i\n"
            "  printf 'Do you want to proceed?\\n> 1. Yes\\n  2. No\\n'\n"
            "  read -r a; echo \"got$i\" >> answers.log\n"
            "  echo \"ran step $i\"\n"
            "done\n"
            "echo ALL_DONE\n"
            "sleep 60\n"
        )
        self._start(body, rows=24)
        injections = _drive(self.sup, _fast_dialog_trigger(), self._screen_done, timeout=30)
        self.assertTrue(self._screen_done(),
                        f"dialog loop never finished; screen:\n{self.sup.capture_plain()}")
        self.assertEqual(_answers(self.workdir), [f"got{i}" for i in range(1, n + 1)])
        self.assertEqual(len(injections), n)

    def test_slow_dialog_redrawn_in_place_is_answered_once_then_next_dialog_once(self):
        # A full-screen TUI redraws its dialog every 0.25 s while the answer
        # sits unread in the tty for 3 s (as when the key did not dismiss
        # it). The child then counts the lines it received: exactly one.
        # After a "working..." screen, a second dialog must get one answer.
        body = (
            "printf '\\033[?1049h'; stty -echo\n"
            "draw() { printf '\\033[H\\033[2J  Requesting permission for:\\r\\n    %s\\r\\n"
            "  Do you want to proceed?\\r\\n  > 1. Yes\\r\\n    2. No\\r\\n' \"$1\"; }\n"
            "for i in $(seq 12); do draw 'rm -rf build'; sleep 0.25; done\n"
            "n=0; while read -r -t 0.4 a; do n=$((n+1)); done; echo \"first:$n\" >> answers.log\n"
            "printf '\\033[H\\033[2Jworking...\\r\\n'; sleep 0.6\n"
            "for i in $(seq 8); do draw 'git push'; sleep 0.25; done\n"
            "n=0; while read -r -t 0.4 a; do n=$((n+1)); done; echo \"second:$n\" >> answers.log\n"
            "printf '\\033[?1049l'; stty echo; echo ALL_DONE\n"
            "sleep 60\n"
        )
        self._start(body, rows=12)
        injections = _drive(self.sup, _fast_dialog_trigger(), self._screen_done, timeout=20)
        self.assertTrue(self._screen_done(),
                        f"script never finished; screen:\n{self.sup.capture_plain()}")
        self.assertEqual(_answers(self.workdir), ["first:1", "second:1"])
        self.assertEqual(len(injections), 2)


class TestAutoApprovePty(_BackendMixin, unittest.TestCase):
    backend = "pty"


@unittest.skipIf(shutil.which("tmux") is None, "tmux is required")
class TestAutoApproveTmux(_BackendMixin, unittest.TestCase):
    backend = "tmux"


@unittest.skipIf(shutil.which("ffmpeg") is None, "ffmpeg is required")
class TestAutoApproveThroughRunner(unittest.TestCase):
    """The same loop through ScenarioRunner with the shipped trigger delays."""

    def test_slow_runner_answers_each_prompt_and_reports_it(self):
        from termreel.scenario.runner import ScenarioRunner
        from termreel.scenario.schema import parse_manifest_dict

        workdir = tempfile.mkdtemp(prefix="termreel_autoapprove_runner_")
        try:
            n = 10
            script = _write_script(workdir, _loop_script(n, silent=False))
            manifest = parse_manifest_dict({
                "version": "1.0",
                "metadata": {
                    "title": "auto-approve",
                    "output": os.path.join(workdir, "out.mp4"),
                    "fps": 10,
                    "cols": 60,
                    "rows": 5,
                },
                "environment": {"auto_approve_dialogs": True},
                "timeline": [
                    {"launch": {"command": f"bash --norc --noprofile {script}"}},
                    {"wait_for_text": {"pattern": "ALL_DONE", "timeout": 40, "pause": 1.0}},
                ],
            })
            runner = ScenarioRunner(manifest=manifest, backend="pty", verbose=False)
            report = runner.run()
            self.assertEqual(_answers(workdir), ["y"] * n)
            yes_no = [i for i in report.injections if "yes_no" in str(i.get("trigger"))]
            self.assertEqual(len(yes_no), n, report.injections)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)


class TestYamlTriggerEdge(unittest.TestCase):
    """`edge:` on a user trigger maps onto the same edge logic."""

    def _runner(self, workdir, trigger):
        from termreel.scenario.runner import ScenarioRunner
        from termreel.scenario.schema import parse_manifest_dict

        manifest = parse_manifest_dict({
            "version": "1.0",
            "metadata": {"output": os.path.join(workdir, "x.mp4")},
            "environment": {"auto_trust": False},
            "triggers": [trigger],
            "timeline": [{"pause": 0.1}],
        })
        return ScenarioRunner(manifest=manifest, verbose=False)

    def test_invalid_edge_is_rejected(self):
        from termreel.exceptions import ScenarioValidationError
        from termreel.scenario.schema import parse_manifest_dict

        with self.assertRaises(ScenarioValidationError):
            parse_manifest_dict({
                "version": "1.0",
                "triggers": [{"on_match": "x", "action": "Enter", "edge": "rising"}],
                "timeline": [{"pause": 0.1}],
            })

    def test_slow_yaml_line_edge_trigger_answers_each_scrolling_prompt_once(self):
        workdir = tempfile.mkdtemp(prefix="termreel_yaml_edge_")
        sup = None
        try:
            runner = self._runner(workdir, {
                "on_match": r"Continue\? \[y/N\]",
                "action": [{"type": "type", "value": "y", "delay_before": 0.05},
                           {"type": "send_key", "value": "Enter", "delay_after": 0.05}],
                "max_firings": 100,
                "cooldown": 0.2,
                "edge": "line",
            })
            triggers = runner.monitor.triggers
            self.assertEqual([t.edge for t in triggers], ["line"])
            script = _write_script(workdir, _loop_script(PROMPTS, silent=False))
            sup = PtySupervisor(command=f"bash --norc --noprofile {script}", cwd=workdir,
                                rows=ROWS, cols=60)
            sup.start()
            done = lambda: "ALL_DONE" in sup.capture_plain()  # noqa: E731
            injections = _drive(sup, triggers[0], done, timeout=40)
            self.assertTrue(done(), sup.capture_plain())
            self.assertEqual(_answers(workdir), ["y"] * PROMPTS)
            self.assertEqual(len(injections), PROMPTS)
        finally:
            if sup is not None:
                sup.terminate()
            shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
