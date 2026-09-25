"""
Streaming video transcoder pipe driving FFmpeg in real time.

Frames are written as raw BGRA to FFmpeg's stdin. Output goes to a
``<name>.partial<ext>`` file next to the target and is renamed into place
only when FFmpeg finishes cleanly, so an interrupted or failed recording never
leaves a truncated file under the requested name.

Encoding details that matter:

* libx264/libvpx with yuv420p need even dimensions. Frames of any size are
  padded to the next even width/height instead of failing.
* GIF output is two-pass. Frames are streamed losslessly (FFV1) to a temp
  file; on close, a palette is generated from that file and applied in a
  second FFmpeg run. The old single-pass ``split``/``palettegen`` graph had to
  buffer every frame in memory until the end, which is what made GIFs longer
  than about a minute time out. GIFs are also capped at 15 fps and 960 px
  wide by default.
* The time allowed for FFmpeg to finish scales with the number of frames,
  because the encoder can lag behind the capture loop.
"""

from collections import deque
import os
import shutil
import subprocess
import tempfile
import threading
import time
from typing import List, Optional
from termreel.exceptions import TranscoderError, FFmpegDeadlockError

EVEN_PAD_FILTER = "pad=ceil(iw/2)*2:ceil(ih/2)*2"
GIF_MAX_FPS = 15
GIF_MAX_WIDTH = 960


def partial_path_for(output_file: str) -> str:
    """``/x/demo.mp4`` -> ``/x/demo.partial.mp4`` (extension kept for muxer detection)."""
    root, ext = os.path.splitext(output_file)
    return f"{root}.partial{ext}"


class FFmpegPipe:
    """
    Manages a continuous streaming pipe to an FFmpeg subprocess.
    Receives raw BGRA frame buffers via stdin and transcodes to MP4/WebM/GIF.
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
        gif_fps: Optional[int] = None,
        gif_max_width: int = GIF_MAX_WIDTH,
    ):
        self.output_file = output_file
        self.width = width
        self.height = height
        self.fps = fps
        self.crf = crf
        self.preset = preset
        self.codec = codec
        self.pix_fmt = pix_fmt
        self.gif_fps = gif_fps
        self.gif_max_width = gif_max_width

        self.process: Optional[subprocess.Popen] = None
        self.is_open: bool = False
        self.frame_count: int = 0
        self._start_time: float = 0.0
        self._write_lock = threading.Lock()
        self._stderr_buffer: deque = deque(maxlen=100)
        self._stderr_thread: Optional[threading.Thread] = None
        self._gif_tmpdir: Optional[str] = None
        self._gif_intermediate: Optional[str] = None

    def __enter__(self) -> "FFmpegPipe":
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    # ------------------------------------------------------------ commands

    @property
    def is_gif(self) -> bool:
        _, ext = os.path.splitext(self.output_file.lower())
        return ext == ".gif" or self.codec == "gif"

    @property
    def partial_file(self) -> str:
        return partial_path_for(self.output_file)

    def _ffmpeg(self) -> str:
        ffmpeg_bin = shutil.which("ffmpeg")
        if not ffmpeg_bin:
            raise TranscoderError("ffmpeg binary not found in system PATH.")
        return ffmpeg_bin

    def _input_args(self) -> List[str]:
        return [
            "-f", "rawvideo",
            "-vcodec", "rawvideo",
            "-s", f"{self.width}x{self.height}",
            "-pix_fmt", self.pix_fmt,
            "-r", str(self.fps),
            "-i", "-",
        ]

    def _gif_filter_prefix(self) -> str:
        fps = min(self.gif_fps or self.fps, GIF_MAX_FPS) or GIF_MAX_FPS
        return f"fps={fps},scale='min({self.gif_max_width},iw)':-2:flags=lanczos"

    def _build_command(self) -> List[str]:
        """Construct the streaming FFmpeg command line."""
        ffmpeg_bin = self._ffmpeg()
        os.makedirs(os.path.dirname(os.path.abspath(self.output_file)), exist_ok=True)
        _, ext = os.path.splitext(self.output_file.lower())
        cmd = [ffmpeg_bin, "-y", *self._input_args()]

        if self.is_gif:
            # Pass 1: lossless intermediate. Palette + GIF happen in close().
            if self._gif_intermediate is None:
                self._gif_tmpdir = tempfile.mkdtemp(prefix="termreel-gif-")
                self._gif_intermediate = os.path.join(self._gif_tmpdir, "frames.mkv")
            cmd.extend(["-c:v", "ffv1", "-pix_fmt", "bgra", self._gif_intermediate])
        elif ext == ".webm" or self.codec == "vp9":
            cmd.extend([
                "-vf", EVEN_PAD_FILTER,
                "-c:v", "libvpx-vp9",
                "-pix_fmt", "yuv420p",
                "-crf", str(max(15, self.crf + 10)),
                "-b:v", "0",
                self.partial_file,
            ])
        else:
            cmd.extend([
                "-vf", EVEN_PAD_FILTER,
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                "-preset", self.preset,
                "-crf", str(self.crf),
                "-movflags", "+faststart",
                self.partial_file,
            ])
        return cmd

    def _gif_commands(self) -> List[List[str]]:
        """Palette generation and application commands for the GIF passes."""
        ffmpeg_bin = self._ffmpeg()
        assert self._gif_intermediate and self._gif_tmpdir
        palette = os.path.join(self._gif_tmpdir, "palette.png")
        prefix = self._gif_filter_prefix()
        return [
            [ffmpeg_bin, "-y", "-i", self._gif_intermediate,
             "-vf", f"{prefix},palettegen=stats_mode=diff", palette],
            [ffmpeg_bin, "-y", "-i", self._gif_intermediate, "-i", palette,
             "-lavfi", f"{prefix}[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle",
             "-f", "gif", self.partial_file],
        ]

    # ------------------------------------------------------------ process

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

    def finalize_timeout(self) -> float:
        """Seconds to allow FFmpeg to finish, scaled by frames written."""
        return 10.0 + 0.05 * self.frame_count

    def close(self, timeout: Optional[float] = None):
        """
        Close stdin, wait for FFmpeg to finish, run GIF passes if needed, and
        move the partial file into place. On failure the partial file is
        removed and an exception is raised.
        """
        with self._write_lock:
            if not self.is_open:
                return
            self.is_open = False
            if timeout is None:
                timeout = self.finalize_timeout()

            try:
                self._finish_process(timeout)
                if self.is_gif:
                    self._run_gif_passes(timeout)
                os.replace(self.partial_file, self.output_file)
            except BaseException:
                try:
                    if os.path.exists(self.partial_file):
                        os.remove(self.partial_file)
                except OSError:
                    pass
                raise
            finally:
                if self._gif_tmpdir:
                    shutil.rmtree(self._gif_tmpdir, ignore_errors=True)
                    self._gif_tmpdir = None
                    self._gif_intermediate = None
                if self.process and self.process.stderr:
                    if self._stderr_thread and self._stderr_thread.is_alive():
                        self._stderr_thread.join(timeout=1.0)
                    try:
                        self.process.stderr.close()
                    except OSError:
                        pass

    def _finish_process(self, timeout: float) -> None:
        if not self.process:
            raise TranscoderError("FFmpeg process was never started.")
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
            raise FFmpegDeadlockError(
                f"FFmpeg failed to finalize {self.frame_count} frames within {timeout:.0f}s; process killed."
            )

        if self._stderr_thread and self._stderr_thread.is_alive():
            self._stderr_thread.join(timeout=1.0)

        if self.process.returncode != 0:
            stderr_summary = "\n".join(list(self._stderr_buffer)[-15:])
            raise TranscoderError(f"FFmpeg encoding failed with exit code {self.process.returncode}:\n{stderr_summary}")

    def _run_gif_passes(self, timeout: float) -> None:
        for cmd in self._gif_commands():
            try:
                res = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True,
                                     timeout=timeout)
            except subprocess.TimeoutExpired as e:
                raise FFmpegDeadlockError(f"GIF pass timed out after {timeout:.0f}s") from e
            if res.returncode != 0:
                tail = res.stderr.decode("utf-8", errors="replace").strip().splitlines()[-15:]
                raise TranscoderError("GIF encoding failed:\n" + "\n".join(tail))

    def extract_poster(self, poster_path: str, timestamp_sec: float = 0.5) -> bool:
        """Extract a single PNG poster frame from the finished video."""
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
        if res.returncode == 0 and os.path.exists(poster_path) and os.path.getsize(poster_path) > 0:
            return True
        # Short videos: -ss past the end yields no frame. Take the first one.
        res = subprocess.run(["ffmpeg", "-y", "-i", self.output_file, "-vframes", "1", poster_path],
                             stdin=subprocess.DEVNULL, capture_output=True)
        return res.returncode == 0 and os.path.exists(poster_path) and os.path.getsize(poster_path) > 0
