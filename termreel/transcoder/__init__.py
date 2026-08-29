"""
Transcoder: FFmpeg memory pipe and animated GIF encoder.
"""

from termreel.transcoder.ffmpeg_pipe import FFmpegPipe
from termreel.transcoder.gif_encoder import GifEncoder

__all__ = [
    "FFmpegPipe",
    "GifEncoder",
]
