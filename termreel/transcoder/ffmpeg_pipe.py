"""
Zero-disk-I/O streaming frame transcoder via FFmpeg stdin pipes.
"""

import os
import subprocess
import threading
import time
from typing import Optional, Dict, Any, List


class FFmpegPipe:
    """
    Direct memory-to-video encoder piping raw BGRA frames directly to FFmpeg standard input.
    Supports H.264 MP4 (faststart), WebM (VP9), GIF, and thumbnail poster generation.
    Thread-safe writes via lock.
    """

    def __init__(
        self,
        output_file: str,
        width: int = 1280,
        height: int = 720,
        fps: int = 30,
        codec: str = "auto",
        crf: int = 20,
        preset: str = "medium",
    ):
        self.output_file = output_file
        self.width = width
        self.height = height
        self.fps = fps
        self.codec = codec
        self.crf = crf
        self.preset = preset

        self.process: Optional[subprocess.Popen] = None
        self.frame_count = 0
        self.is_open = False
        self._start_time = 0.0
        self._write_lock = threading.Lock()

        os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)


    def _build_command(self) -> List[str]:
        """Construct the FFmpeg command line based on requested output format."""
        ext = os.path.splitext(self.output_file)[1].lower()

        # Input rawvideo configuration
        cmd = [
            "ffmpeg",
            "-y",  # Overwrite output without asking
            "-f", "rawvideo",
            "-vcodec", "rawvideo",
            "-s", f"{self.width}x{self.height}",
            "-pix_fmt", "bgra",
            "-r", str(self.fps),
            "-i", "-",  # Read from stdin
        ]

        if ext == ".webm" or self.codec == "vp9":
            cmd.extend([
                "-c:v", "libvpx-vp9",
                "-crf", str(max(15, self.crf + 10)),
                "-b:v", "0",
                self.output_file,
            ])
        elif ext == ".gif" or self.codec == "gif":
            # Direct GIF encoding with palette filter
            cmd.extend([
                "-vf", f"fps={self.fps},split[s0][s1];[s0]palettegen=stats_mode=diff[p];[s1][p]paletteuse=dither=bayer:bayer_scale=5",
                self.output_file,
            ])
        else:
            # Default to H.264 MP4 with faststart for instant web streaming
            cmd.extend([
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                "-preset", self.preset,
                "-crf", str(self.crf),
                "-movflags", "+faststart",
                self.output_file,
            ])

        return cmd

    def open(self):
        """Spawn the FFmpeg subprocess with standard input pipe."""
        if self.is_open:
            return

        cmd = self._build_command()
        self.process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        self.is_open = True
        self.frame_count = 0
        self._start_time = time.time()

    def write_frame(self, frame_bytes: bytes):
        """Write a single raw BGRA frame buffer into FFmpeg stdin."""
        with self._write_lock:
            if not self.is_open or not self.process or not self.process.stdin:
                raise RuntimeError("FFmpeg pipe is not open.")

            try:
                self.process.stdin.write(frame_bytes)
                self.frame_count += 1
            except (BrokenPipeError, OSError) as e:
                stderr_out = ""
                if self.process.stderr:
                    stderr_out = self.process.stderr.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"FFmpeg stdin pipe broken after {self.frame_count} frames: {stderr_out}") from e

    def close(self):
        """Close stdin and wait for FFmpeg to finalize encoding."""
        with self._write_lock:
            if not self.is_open:
                return

            self.is_open = False
            stderr_text = ""

            if self.process:
                if self.process.stdin:
                    try:
                        self.process.stdin.flush()
                        self.process.stdin.close()
                    except (BrokenPipeError, OSError):
                        pass

                _, stderr_bytes = self.process.communicate()
                if stderr_bytes:
                    stderr_text = stderr_bytes.decode("utf-8", errors="replace")

                if self.process.returncode != 0:
                    raise RuntimeError(f"FFmpeg encoding failed with exit code {self.process.returncode}: {stderr_text}")


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
        res = subprocess.run(cmd, capture_output=True)
        return res.returncode == 0 and os.path.exists(poster_path)

    @property
    def duration(self) -> float:
        """Calculate recorded duration in seconds based on frame count."""
        return self.frame_count / float(self.fps) if self.fps > 0 else 0.0

    def __enter__(self) -> "FFmpegPipe":
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
