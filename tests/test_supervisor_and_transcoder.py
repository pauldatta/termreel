"""
Low-level systems, supervisor, and transcoder integration test suite.
Tests PtySupervisor (POSIX pseudo-terminals), FFmpegPipe (real-time video transcoding),
and GifEncoder (palette-optimized GIF synthesis).
"""

import fcntl
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import termios
import time
import unittest
from unittest.mock import MagicMock, patch

from termreel.exceptions import TranscoderError, FFmpegDeadlockError
from termreel.supervisor.pty_session import PtySupervisor
from termreel.transcoder.ffmpeg_pipe import FFmpegPipe
from termreel.transcoder.gif_encoder import GifEncoder


class TestPtySupervisor(unittest.TestCase):
    """Integration and unit tests for POSIX PtySupervisor."""

    def test_launch_echo_process(self):
        """Test launching child process with echo and extracting screen text."""
        sup = PtySupervisor(command="echo 'TermReel PTY'", rows=24, cols=80)
        sup.start()
        try:
            matched = sup.wait_for_output("TermReel PTY", timeout=4.0)
            self.assertTrue(matched, "Expected 'TermReel PTY' to appear in output")
            screen_text = sup.get_screen()
            self.assertIn("TermReel PTY", screen_text)

            # Process should finish quickly
            deadline = time.time() + 3.0
            while sup.is_alive() and time.time() < deadline:
                time.sleep(0.05)
            self.assertFalse(sup.is_alive())
        finally:
            sup.terminate()

        self.assertIsNone(sup.master_fd)
        self.assertIsNone(sup.slave_fd)

    def test_interactive_cat_keystrokes_and_eof(self):
        """Test interactive cat session, keystroke injection, screen extraction, and EOF."""
        sup = PtySupervisor(command="cat", rows=24, cols=80)
        sup.start()
        try:
            self.assertTrue(sup.is_alive())

            # Send input via send_input and Enter key
            sup.send_input("Interactive Cat Input")
            sup.send_key("Enter")

            matched = sup.wait_for_output("Interactive Cat Input", timeout=4.0)
            self.assertTrue(matched, "Expected cat to echo back input")
            self.assertIn("Interactive Cat Input", sup.get_screen())

            # Send EOF (C-d) to cat process
            sup.send_key("C-d")

            # Cat should terminate upon receiving EOF
            deadline = time.time() + 3.0
            while sup.is_alive() and time.time() < deadline:
                time.sleep(0.05)
            self.assertFalse(sup.is_alive(), "Process should terminate after C-d EOF")
        finally:
            sup.terminate()

    def test_interactive_python_repl_and_control_keys(self):
        """Test interactive python REPL, arithmetic evaluation, arrows, and control keys."""
        cmd = f"{sys.executable} -q"
        sup = PtySupervisor(command=cmd, rows=25, cols=80)
        sup.start()
        try:
            self.assertTrue(sup.is_alive())
            prompt_seen = sup.wait_for_output(">>>", timeout=5.0)
            self.assertTrue(prompt_seen, "Expected Python prompt >>>")

            # Evaluate python expression
            sup.send_input("print(1330 + 7)")
            sup.send_key("Enter")

            calc_seen = sup.wait_for_output("1337", timeout=4.0)
            self.assertTrue(calc_seen, "Expected 1337 in output")
            self.assertIn("1337", sup.get_screen())

            # Test special key sequences: Escape, arrows, control keys
            sup.send_key("Escape")
            sup.send_key("Up")
            sup.send_key("Down")
            sup.send_key("Left")
            sup.send_key("Right")
            time.sleep(0.05)

            # Test C-u (clear line in readline)
            sup.send_input("discarded_text")
            sup.send_key("C-u")

            # Test C-c (KeyboardInterrupt)
            sup.send_key("C-c")
            ki_seen = sup.wait_for_output("KeyboardInterrupt", timeout=4.0)
            self.assertTrue(ki_seen or sup.state.contains(">>>"), "Expected KeyboardInterrupt or prompt")

            # Clean exit via exit()
            sup.send_input("exit()\n")
            deadline = time.time() + 3.0
            while sup.is_alive() and time.time() < deadline:
                time.sleep(0.05)
        finally:
            sup.terminate()

    def test_terminal_resizing_and_ioctl_propagation(self):
        """Verify resize(rows, cols) updates state and propagates TIOCSWINSZ ioctl."""
        sup = PtySupervisor(command="bash", rows=20, cols=60)
        sup.start()
        try:
            self.assertIsNotNone(sup.master_fd)

            # Read initial window geometry from master_fd using TIOCGWINSZ
            winsize = fcntl.ioctl(sup.master_fd, termios.TIOCGWINSZ, b"\x00" * 8)
            r, c, _, _ = struct.unpack("HHHH", winsize)
            self.assertEqual((r, c), (20, 60))

            # Resize terminal
            sup.resize(rows=35, cols=110)
            self.assertEqual(sup.rows, 35)
            self.assertEqual(sup.cols, 110)
            self.assertEqual(sup.state.rows, 35)
            self.assertEqual(sup.state.cols, 110)

            # Read updated window geometry from master_fd
            winsize = fcntl.ioctl(sup.master_fd, termios.TIOCGWINSZ, b"\x00" * 8)
            r, c, _, _ = struct.unpack("HHHH", winsize)
            self.assertEqual((r, c), (35, 110))

            # Verify child process receives updated geometry
            sup.send_input(f"{sys.executable} -c 'import shutil; print(shutil.get_terminal_size())'\n")
            sz_seen = sup.wait_for_output("terminal_size(columns=110", timeout=5.0)
            self.assertTrue(sz_seen, "Expected child process to output terminal size")
            screen = sup.get_screen()
            self.assertIn("columns=110", screen)
            self.assertIn("lines=35", screen)
        finally:
            sup.terminate()

    def test_process_lifecycle_and_clean_fd_cleanup(self):
        """Verify process termination, FD closing without leaks, and context manager."""
        sup = PtySupervisor(command="sleep 30", rows=24, cols=80)
        sup.start()
        self.assertTrue(sup.is_alive())
        master_fd = sup.master_fd
        self.assertIsNotNone(master_fd)
        self.assertIsNone(sup.slave_fd, "Slave FD must be closed in parent process")

        # Master FD is open and valid
        os.fstat(master_fd)

        # Terminate process
        sup.terminate()
        self.assertFalse(sup.is_alive())
        self.assertIsNone(sup.master_fd)

        # Master FD must be closed and cleared
        self.assertIsNone(sup.master_fd)
        try:
            os.fstat(master_fd)
        except OSError:
            pass

        # Calling send_text after terminate must raise RuntimeError
        with self.assertRaises(RuntimeError):
            sup.send_text("test")

        # Test context manager lifecycle
        with PtySupervisor(command="sleep 30", rows=24, cols=80) as ctx_sup:
            ctx_fd = ctx_sup.master_fd
            self.assertTrue(ctx_sup.is_alive())
            os.fstat(ctx_fd)

        self.assertFalse(ctx_sup.is_alive())
        self.assertIsNone(ctx_sup.master_fd)
        try:
            os.fstat(ctx_fd)
        except OSError:
            pass


class TestFFmpegPipe(unittest.TestCase):
    """Integration and error handling tests for FFmpegPipe."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="termreel_pipe_test_")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_initialization_with_custom_attributes(self):
        """Test pipe initialization with custom dimensions, fps, crf, preset, and pix_fmt."""
        out_mp4 = os.path.join(self.test_dir, "test.mp4")
        pipe = FFmpegPipe(
            output_file=out_mp4,
            width=640,
            height=360,
            fps=24,
            crf=18,
            preset="fast",
            pix_fmt="bgra",
        )
        self.assertEqual(pipe.width, 640)
        self.assertEqual(pipe.height, 360)
        self.assertEqual(pipe.fps, 24)
        self.assertEqual(pipe.crf, 18)
        self.assertEqual(pipe.preset, "fast")
        self.assertEqual(pipe.pix_fmt, "bgra")

        cmd = pipe._build_command()
        self.assertIn("-s", cmd)
        self.assertIn("640x360", cmd)
        self.assertIn("-pix_fmt", cmd)
        self.assertIn("bgra", cmd)
        self.assertIn("-r", cmd)
        self.assertIn("24", cmd)
        self.assertIn("-preset", cmd)
        self.assertIn("fast", cmd)
        self.assertIn("-crf", cmd)
        self.assertIn("18", cmd)

        # Test WebM VP9 command building
        webm_pipe = FFmpegPipe(os.path.join(self.test_dir, "test.webm"), 320, 240, codec="vp9")
        webm_cmd = webm_pipe._build_command()
        self.assertIn("libvpx-vp9", webm_cmd)

        # Test GIF command building
        gif_pipe = FFmpegPipe(os.path.join(self.test_dir, "test.gif"), 320, 240)
        gif_cmd = gif_pipe._build_command()
        self.assertTrue(any("palettegen" in arg for arg in gif_cmd))

    def test_frame_writing_stderr_draining_and_transcode(self):
        """Test writing raw BGRA frames, stderr draining, and clean MP4 container finalization."""
        out_mp4 = os.path.join(self.test_dir, "render.mp4")
        width, height = 320, 240
        fps = 15

        pipe = FFmpegPipe(output_file=out_mp4, width=width, height=height, fps=fps)
        pipe.open()
        self.assertTrue(pipe.is_open)

        # Write 15 solid blue/red frames
        frame_bytes = (b"\xff\x00\x00\xff") * (width * height)
        for _ in range(15):
            pipe.write_frame(frame_bytes)

        self.assertEqual(pipe.frame_count, 15)

        # Wait briefly to let stderr thread drain FFmpeg startup logs
        time.sleep(0.2)
        self.assertGreater(len(pipe._stderr_buffer), 0, "Stderr buffer should have captured FFmpeg output")

        pipe.close()
        self.assertFalse(pipe.is_open)
        self.assertIsNotNone(pipe.process)
        self.assertEqual(pipe.process.returncode, 0)
        self.assertTrue(os.path.exists(out_mp4))
        self.assertGreater(os.path.getsize(out_mp4), 0)

        # Verify poster extraction
        poster_png = os.path.join(self.test_dir, "poster.png")
        success = pipe.extract_poster(poster_png, timestamp_sec=0.1)
        self.assertTrue(success)
        self.assertTrue(os.path.exists(poster_png))
        self.assertGreater(os.path.getsize(poster_png), 0)

    def test_error_handling_writing_before_open(self):
        """Writing to an unopened pipe must raise TranscoderError."""
        out_mp4 = os.path.join(self.test_dir, "unopened.mp4")
        pipe = FFmpegPipe(output_file=out_mp4, width=160, height=120)
        with self.assertRaises(TranscoderError):
            pipe.write_frame(b"\x00" * (160 * 120 * 4))

    def test_error_handling_broken_pipe(self):
        """Writing to a broken FFmpeg stdin pipe must raise TranscoderError."""
        out_mp4 = os.path.join(self.test_dir, "broken.mp4")
        pipe = FFmpegPipe(output_file=out_mp4, width=160, height=120)
        pipe.open()
        try:
            # Abruptly close stdin pipe
            pipe.process.stdin.close()
            with self.assertRaises(TranscoderError):
                pipe.write_frame(b"\x00" * (160 * 120 * 4))
        finally:
            pipe.close()

    def test_process_reaping_on_timeout(self):
        """Verify process termination and FFmpegDeadlockError when wait() times out."""
        out_mp4 = os.path.join(self.test_dir, "timeout.mp4")
        pipe = FFmpegPipe(output_file=out_mp4, width=160, height=120)
        pipe.open()
        real_proc = pipe.process

        try:
            # Mock wait() to simulate process deadlock
            pipe.process.wait = MagicMock(side_effect=[
                subprocess.TimeoutExpired(cmd="ffmpeg", timeout=0.01),
                subprocess.TimeoutExpired(cmd="ffmpeg", timeout=0.01),
                0,
            ])
            pipe.process.terminate = MagicMock()
            pipe.process.kill = MagicMock()

            with self.assertRaises(FFmpegDeadlockError):
                pipe.close(timeout=0.01)

            pipe.process.terminate.assert_called()
            pipe.process.kill.assert_called()
        finally:
            if real_proc:
                try:
                    real_proc.kill()
                    real_proc.wait(timeout=1.0)
                except Exception:
                    pass

    def test_nonzero_exit_raises_transcoder_error(self):
        """Verify close() raises TranscoderError when FFmpeg exits with non-zero code."""
        out_mp4 = os.path.join(self.test_dir, "failed.mp4")
        pipe = FFmpegPipe(output_file=out_mp4, width=160, height=120)
        pipe.open()
        real_proc = pipe.process
        pipe.process.returncode = 1
        pipe._stderr_buffer.append("Error: Invalid codec parameters")

        try:
            with patch.object(pipe.process, "wait", return_value=1):
                with self.assertRaises(TranscoderError) as ctx:
                    pipe.close()
                self.assertIn("FFmpeg encoding failed with exit code 1", str(ctx.exception))
        finally:
            if real_proc:
                try:
                    real_proc.kill()
                    real_proc.wait(timeout=1.0)
                except Exception:
                    pass


class TestGifEncoder(unittest.TestCase):
    """Integration and error handling tests for GifEncoder."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="termreel_gif_test_")
        # Create a small valid test MP4 video using FFmpegPipe
        self.test_video = os.path.join(self.test_dir, "source.mp4")
        width, height = 160, 120
        with FFmpegPipe(output_file=self.test_video, width=width, height=height, fps=10) as pipe:
            for i in range(10):
                # Gradient-like frame
                b_val = (i * 25) % 256
                frame = bytes([b_val, 128, 64, 255]) * (width * height)
                pipe.write_frame(frame)

        self.assertTrue(os.path.exists(self.test_video))
        self.assertGreater(os.path.getsize(self.test_video), 0)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_palette_optimized_video_to_gif(self):
        """Convert small MP4 video to palette-optimized GIF and validate output."""
        output_gif = os.path.join(self.test_dir, "output.gif")
        result = GifEncoder.video_to_gif(
            input_video=self.test_video,
            output_gif=output_gif,
            fps=10,
            width=160,
        )
        self.assertTrue(result)
        self.assertTrue(os.path.exists(output_gif))
        self.assertGreater(os.path.getsize(output_gif), 0)

        # Validate GIF header magic bytes (GIF87a or GIF89a)
        with open(output_gif, "rb") as f:
            header = f.read(6)
        self.assertIn(header, (b"GIF87a", b"GIF89a"))

    def test_error_handling_nonexistent_input_video(self):
        """Converting a non-existent input video must raise FileNotFoundError."""
        output_gif = os.path.join(self.test_dir, "missing.gif")
        with self.assertRaises(FileNotFoundError):
            GifEncoder.video_to_gif(
                input_video=os.path.join(self.test_dir, "nonexistent_source.mp4"),
                output_gif=output_gif,
            )

    def test_custom_dither_and_scaling(self):
        """Test video_to_gif with custom width and dither algorithm."""
        output_gif = os.path.join(self.test_dir, "custom.gif")
        result = GifEncoder.video_to_gif(
            input_video=self.test_video,
            output_gif=output_gif,
            fps=5,
            width=120,
            dither="floyd_steinberg",
        )
        self.assertTrue(result)
        self.assertTrue(os.path.exists(output_gif))
        self.assertGreater(os.path.getsize(output_gif), 0)


if __name__ == "__main__":
    unittest.main()
