"""
Streaming video transcoder pipe driving FFmpeg in real-time with zero intermediate disk I/O.
Features non-blocking background stderr draining to prevent pipe deadlocks and timed process reaping.
"""

from collections import deque
import os
import shutil
import subprocess
import threading
import time
from typing import List, Optional
from termreel.exceptions import TranscoderError, FFmpegDeadlockError


class FFmpegPipe:
    """
    Manages a continuous streaming pipe to an FFmpeg subprocess.
    Receives raw BGRA vector image buffers via stdin and transcodes directly to MP4/WebM/GIF.
    """

    def __init__(
        self,
        output_file: str,
        width: int,
        height: int,
        fps: int = 30,
        crf: int = 20,
        preset: str = "medium",
        codec: Optional[str] = None,
        pix_fmt: str = "bgra",
    ):
        self.output_file = output_file
        self.width = width
        self.height = height
        self.fps = fps
        self.crf = crf
        self.preset = preset
        self.codec = codec
        self.pix_fmt = pix_fmt

        self.process: Optional[subprocess.Popen] = None
        self.is_open: bool = False
        self.frame_count: int = 0
        self._start_time: float = 0.0
        self._write_lock = threading.Lock()
        self._stderr_buffer: deque = deque(maxlen=100)
        self._stderr_thread: Optional[threading.Thread] = None

    def __enter__(self) -> "FFmpegPipe":
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def _build_command(self) -> List[str]:
        """Construct the FFmpeg command line arguments for pixel-perfect streaming."""
        ffmpeg_bin = shutil.which("ffmpeg")
        if not ffmpeg_bin:
            raise TranscoderError("ffmpeg binary not found in system PATH.")

        # Ensure target directory exists
        os.makedirs(os.path.dirname(os.path.abspath(self.output_file)), exist_ok=True)

        _, ext = os.path.splitext(self.output_file.lower())

        cmd = [
            ffmpeg_bin,
            "-y",
            "-f", "rawvideo",
            "-vcodec", "rawvideo",
            "-s", f"{self.width}x{self.height}",
            "-pix_fmt", self.pix_fmt,
            "-r", str(self.fps),
            "-i", "-",
        ]

        if ext == ".webm" or self.codec == "vp9":
            cmd.extend([
                "-c:v", "libvpx-vp9",
                "-crf", str(max(15, self.crf + 10)),
                "-b:v", "0",
                self.output_file,
            ])
        elif ext == ".gif" or self.codec == "gif":
            cmd.extend([
                "-vf", f"fps={self.fps},split[s0][s1];[s0]palettegen=stats_mode=diff[p];[s1][p]paletteuse=dither=bayer:bayer_scale=5",
                self.output_file,
            ])
        else:
            cmd.extend([
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                "-preset", self.preset,
                "-crf", str(self.crf),
                "-movflags", "+faststart",
                self.output_file,
            ])

        return cmd

    def _drain_stderr(self):
        """Asynchronously drain stderr pipe to prevent OS buffer deadlocks."""
        if not self.process or not self.process.stderr:
            return
        try:
            for line in iter(self.process.stderr.readline, b""):
                if line:
                    decoded = line.decode("utf-8", errors="replace").strip()
                    self._stderr_buffer.append(decoded)
        except Exception:
            pass

    def open(self):
        """Spawn the FFmpeg subprocess with standard input pipe and async stderr drainer."""
        if self.is_open:
            return

        cmd = self._build_command()
        try:
            self.process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except Exception as e:
            raise TranscoderError(f"Failed to spawn FFmpeg process: {e}") from e

        self.is_open = True
        self.frame_count = 0
        self._start_time = time.time()

        # Start non-blocking stderr drainer thread
        self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stderr_thread.start()

    def write_frame(self, frame_bytes: bytes):
        """Write a single raw BGRA frame buffer into FFmpeg stdin."""
        with self._write_lock:
            if not self.is_open or not self.process or not self.process.stdin:
                raise TranscoderError("FFmpeg pipe is not open.")

            try:
                self.process.stdin.write(frame_bytes)
                self.frame_count += 1
            except (BrokenPipeError, OSError, ValueError) as e:
                stderr_summary = "\n".join(list(self._stderr_buffer)[-10:])
                raise TranscoderError(f"FFmpeg stdin pipe broken after {self.frame_count} frames.\nStderr:\n{stderr_summary}") from e

    def close(self, timeout: float = 10.0):
        """Close stdin and wait for FFmpeg to finalize encoding with timed process reaping."""
        with self._write_lock:
            if not self.is_open:
                return

            self.is_open = False

            if self.process:
                if self.process.stdin:
                    try:
                        self.process.stdin.flush()
                        self.process.stdin.close()
                    except (BrokenPipeError, OSError, ValueError):
                        pass

                try:
                    self.process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=3.0)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                        try:
                            self.process.wait(timeout=2.0)
                        except Exception:
                            pass
                    raise FFmpegDeadlockError("FFmpeg failed to finalize container within timeout; process killed.")

                if self._stderr_thread and self._stderr_thread.is_alive():
                    self._stderr_thread.join(timeout=1.0)

                if self.process.returncode != 0:
                    stderr_summary = "\n".join(list(self._stderr_buffer)[-15:])
                    raise TranscoderError(f"FFmpeg encoding failed with exit code {self.process.returncode}:\n{stderr_summary}")

    def extract_poster(self, poster_path: str, timestamp_sec: float = 0.5) -> bool:
        """Extract a single high-resolution PNG poster frame from the rendered video."""
        if not os.path.exists(self.output_file):
            return False

        os.makedirs(os.path.dirname(os.path.abspath(poster_path)), exist_ok=True)
        cmd = [
            "ffmpeg",
            "-y",
            "-ss", str(timestamp_sec),
            "-i", self.output_file,
            "-vframes", "1",
            "-q:v", "2",
            poster_path,
        ]
        res = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True)
        return res.returncode == 0 and os.path.exists(poster_path)
