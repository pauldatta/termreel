"""
High-quality palette-optimized animated GIF encoder using FFmpeg.
"""

import os
import subprocess
from typing import Optional


class GifEncoder:
    """
    Encodes video files or raw frames into crisp, palette-optimized animated GIFs.
    """

    @staticmethod
    def video_to_gif(
        input_video: str,
        output_gif: str,
        fps: int = 15,
        width: int = 960,
        dither: str = "bayer:bayer_scale=5",
    ) -> bool:
        """
        Transcodes an existing MP4/WebM video into a palette-optimized GIF.
        Uses two-pass palettegen + paletteuse filter graph.
        """
        if not os.path.exists(input_video):
            raise FileNotFoundError(f"Input video not found: {input_video}")

        os.makedirs(os.path.dirname(os.path.abspath(output_gif)), exist_ok=True)

        filter_graph = (
            f"[0:v] fps={fps},scale={width}:-1:flags=lanczos,split[s0][s1];"
            f"[s0]palettegen=stats_mode=diff[p];"
            f"[s1][p]paletteuse=dither={dither}"
        )

        cmd = [
            "ffmpeg",
            "-y",
            "-i", input_video,
            "-vf", filter_graph,
            output_gif,
        ]

        res = subprocess.run(cmd, capture_output=True, text=True)
        return res.returncode == 0 and os.path.exists(output_gif)
