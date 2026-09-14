"""
Drive a real `termreel live` recording headlessly through a PTY pair.

Not a unit test, and deliberately not named test_*, so the suite runner does
not pick it up. This is the manual end-to-end check that the whole stack (raw
stdin, prefix hotkeys, pause gate, crossfade, encoder) actually works.

    python3 tests/manual_live_e2e_check.py

Needs ffmpeg and ffprobe on PATH. Takes about 12 seconds.
"""

import os
import pty
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from termreel.live.config import resolve_prefix
from termreel.live.recorder import LiveRecorder


def ffprobe_duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", path],
        capture_output=True, text=True,
    )
    return float(out.stdout.strip())


def main():
    workdir = tempfile.mkdtemp(prefix="termreel_live_e2e_")
    out = os.path.join(workdir, "live.mp4")
    cast = os.path.join(workdir, "live.cast")

    # A PTY pair standing in for the operator's terminal.
    op_master, op_slave = pty.openpty()

    prefix = resolve_prefix(cli_value="C-t")
    rec = LiveRecorder(
        command="bash --norc --noprofile",
        output=out,
        cast=cast,
        fps=10,
        crossfade=0.6,
        cols=80,
        rows=24,
        prefix=prefix,
        stdin_fd=op_slave,
        stdout_fd=op_slave,
        cwd=workdir,
        verbose=True,
        enable_telemetry=True,
    )

    result = {}

    def run():
        result["report"] = rec.run()

    thread = threading.Thread(target=run)
    thread.start()

    def send(data, wait=0.0):
        os.write(op_master, data)
        if wait:
            time.sleep(wait)

    # Drain whatever the recorder mirrors back so the pty buffer never fills.
    draining = threading.Event()

    def drain():
        while not draining.is_set():
            try:
                if not os.read(op_master, 65536):
                    break
            except OSError:
                break

    drainer = threading.Thread(target=drain, daemon=True)
    drainer.start()

    time.sleep(1.5)
    send(b"echo LIVE_PHASE_ONE\r", 2.0)
    frames_before_pause = rec.frames_written

    send(b"\x14p", 0.3)                      # ^T p -> pause
    assert rec.paused, "recorder did not pause"
    time.sleep(2.0)
    frames_during_pause = rec.frames_written - frames_before_pause

    send(b"echo LIVE_WHILE_PAUSED\r", 1.0)   # child keeps running while paused
    send(b"\x14p", 0.3)                      # ^T p -> resume
    assert not rec.paused, "recorder did not resume"

    send(b"echo LIVE_PHASE_TWO\r", 2.0)
    send(b"\x14m", 0.3)                      # ^T m -> mark

    # Double prefix must type a literal 0x14 into the child, not toggle.
    paused_before = rec.paused
    send(b"\x14\x14", 0.5)
    assert rec.paused == paused_before, "double prefix was treated as a hotkey"

    send(b"\x14q", 0.5)                      # ^T q -> stop
    thread.join(timeout=20)
    draining.set()

    report = result["report"]
    duration = ffprobe_duration(out)
    expected = report.frames_written / float(report.fps)
    screen_text = rec.state.get_rendered_text()

    print("\n--- results ---")
    print(f"status               : {report.status}")
    print(f"frames during pause  : {frames_during_pause}  (must be 0)")
    print(f"frames written       : {report.frames_written}")
    print(f"expected duration    : {expected:.3f}s")
    print(f"ffprobe duration     : {duration:.3f}s")
    print(f"paused seconds cut   : {report.paused_seconds:.2f}")
    print(f"wall seconds         : {report.wall_seconds:.2f}")
    print(f"marks                : {report.marks}")
    print(f"frame errors         : {report.frame_errors}")
    print(f"file size            : {report.file_size_bytes}")
    print(f"cast events          : {sum(1 for _ in open(cast)) - 1}")
    print(f"PHASE_ONE on screen  : {'LIVE_PHASE_ONE' in screen_text}")
    print(f"PHASE_TWO on screen  : {'LIVE_PHASE_TWO' in screen_text}")
    print(f"PAUSED cmd ran       : {'LIVE_WHILE_PAUSED' in screen_text}")

    failures = []
    if report.status != "pass":
        failures.append(f"status {report.status}: {report.error_message}")
    if frames_during_pause != 0:
        failures.append(f"{frames_during_pause} frames written while paused")
    if abs(duration - expected) > 0.25:
        failures.append(f"duration {duration} != expected {expected}")
    if report.paused_seconds < 1.5:
        failures.append(f"paused_seconds {report.paused_seconds} too small")
    if report.wall_seconds - duration < 1.5:
        failures.append("paused time was not actually cut from the video")
    if not report.marks:
        failures.append("mark hotkey produced nothing")
    if report.frame_errors:
        failures.append(f"{report.frame_errors} frame errors")
    if "LIVE_PHASE_TWO" not in screen_text:
        failures.append("post-resume output missing from the recorded grid")

    print("\nFAILURES:" if failures else "\nALL CHECKS PASSED")
    for item in failures:
        print(f"  - {item}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
