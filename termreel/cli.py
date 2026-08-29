"""
TermReel command-line interface.
"""

import argparse
import os
import shutil
import sys
import time
from typing import List, Optional

import termreel
from termreel.scenario.schema import ScenarioManifest
from termreel.scenario.runner import ScenarioRunner
from termreel.renderer.themes import list_themes, get_theme
from termreel.utils.asciicast import AsciicastPlayer
from termreel.emulator.state import TerminalState
from termreel.emulator.parser import ANSIParser
from termreel.renderer.cairo_renderer import CairoTerminalRenderer
from termreel.transcoder.ffmpeg_pipe import FFmpegPipe
from termreel.transcoder.gif_encoder import GifEncoder


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="termreel",
        description="TermReel: Universal Terminal Recording & Video Synthesis Engine",
    )
    parser.add_argument("-v", "--version", action="version", version=f"%(prog)s {termreel.__version__}")

    subparsers = parser.add_subparsers(dest="subcommand", help="Subcommand to execute")

    # 1. record
    record_parser = subparsers.add_parser("record", help="Record a video from a declarative scenario YAML file")
    record_parser.add_argument("scenario", help="Path to scenario YAML manifest")
    record_parser.add_argument("-o", "--output", help="Override output video path (e.g. out.mp4, out.webm, out.gif)")
    record_parser.add_argument("--fps", type=int, help="Override recording frames per second (default: 30)")
    record_parser.add_argument("--theme", help="Override visual theme (e.g. catppuccin-mocha, dracula, tokyo-night)")
    record_parser.add_argument("--backend", choices=["auto", "tmux", "pty"], default="auto", help="PTY backend (default: auto)")
    record_parser.add_argument("--cast", help="Export Asciinema v2 .cast log to specified path")
    record_parser.add_argument("--poster", help="Export poster thumbnail image to specified path")
    record_parser.add_argument("-q", "--quiet", action="store_true", help="Suppress verbose logging")

    # 2. exec
    exec_parser = subparsers.add_parser("exec", help="Directly record a single CLI command to video")
    exec_parser.add_argument("command", help="CLI command to execute and record")
    exec_parser.add_argument("-o", "--output", default="output/exec_session.mp4", help="Output video path (default: output/exec_session.mp4)")
    exec_parser.add_argument("--title", default="TermReel Execution", help="Window title")
    exec_parser.add_argument("--subtitle", default="Live Command", help="Window subtitle")
    exec_parser.add_argument("--cwd", help="Working directory for the command")
    exec_parser.add_argument("--fps", type=int, default=30, help="Frames per second (default: 30)")
    exec_parser.add_argument("--theme", default="catppuccin-mocha", help="Visual theme")
    exec_parser.add_argument("--timeout", type=float, default=60.0, help="Maximum recording duration in seconds")
    exec_parser.add_argument("--backend", choices=["auto", "tmux", "pty"], default="auto", help="PTY backend")

    # 3. cast2video
    c2v_parser = subparsers.add_parser("cast2video", help="Render an existing Asciinema .cast file into video or GIF")
    c2v_parser.add_argument("cast_file", help="Input .cast file")
    c2v_parser.add_argument("-o", "--output", default="output/cast_render.mp4", help="Output video path (default: output/cast_render.mp4)")
    c2v_parser.add_argument("--fps", type=int, default=30, help="Frames per second (default: 30)")
    c2v_parser.add_argument("--theme", default="catppuccin-mocha", help="Visual theme")
    c2v_parser.add_argument("--title", default="Asciicast Replay", help="Window title")
    c2v_parser.add_argument("--speed", type=float, default=1.0, help="Playback speed multiplier (default: 1.0)")

    # 4. validate
    val_parser = subparsers.add_parser("validate", help="Validate a scenario YAML manifest syntax and schema")
    val_parser.add_argument("scenario", help="Path to scenario YAML manifest")

    # 5. themes
    subparsers.add_parser("themes", help="List available visual themes and palettes")

    # 6. info
    subparsers.add_parser("info", help="Display environment, dependencies, and codec status")

    return parser


def cmd_record(args: argparse.Namespace) -> int:
    if not os.path.exists(args.scenario):
        print(f"Error: Scenario file not found: {args.scenario}", file=sys.stderr)
        return 1

    try:
        manifest = ScenarioManifest.from_yaml_file(args.scenario)
        if args.cast:
            manifest.metadata.cast_output = args.cast
        if args.poster:
            manifest.metadata.poster_output = args.poster

        runner = ScenarioRunner(
            manifest=manifest,
            output_override=args.output,
            fps_override=args.fps,
            theme_override=args.theme,
            backend=args.backend,
            verbose=not args.quiet,
        )
        report = runner.run()
        if report.status == "pass":
            print(f"✨ Successfully generated: {report.output_file}")
            return 0
        else:
            print(f"❌ Recording failed: {report.error_message}", file=sys.stderr)
            return 1
    except Exception as e:
        print(f"❌ Execution error: {e}", file=sys.stderr)
        return 1


def cmd_exec(args: argparse.Namespace) -> int:
    manifest_dict = {
        "version": "1.0",
        "metadata": {
            "title": args.title,
            "subtitle": args.subtitle,
            "output": args.output,
            "fps": args.fps,
            "theme": args.theme,
        },
        "environment": {
            "cwd": args.cwd,
        },
        "timeline": [
            {
                "launch": {
                    "command": args.command,
                    "timeout": args.timeout,
                }
            },
            {
                "wait_for_idle": {
                    "timeout": args.timeout,
                    "reading_pause": 2.0,
                }
            },
        ],
    }
    manifest = ScenarioManifest.from_dict(manifest_dict)
    runner = ScenarioRunner(manifest=manifest, backend=args.backend, verbose=True)
    report = runner.run()
    return 0 if report.status == "pass" else 1


def cmd_cast2video(args: argparse.Namespace) -> int:
    if not os.path.exists(args.cast_file):
        print(f"Error: Asciicast file not found: {args.cast_file}", file=sys.stderr)
        return 1

    player = AsciicastPlayer(args.cast_file)
    renderer = CairoTerminalRenderer(
        width=1280,
        height=720,
        title=args.title,
        subtitle=f"{player.width}x{player.height} Cast",
        theme=args.theme,
    )
    state = TerminalState(
        rows=renderer.rows,
        cols=renderer.cols,
        default_fg=renderer.theme.default_fg,
        default_bg=renderer.theme.terminal_bg,
        palette=renderer.theme.palette,
    )
    parser = ANSIParser(state)

    pipe = FFmpegPipe(output_file=args.output, width=1280, height=720, fps=args.fps)
    pipe.open()
    print(f"Rendering {args.cast_file} ({len(player.events)} events, duration {player.duration:.1f}s) -> {args.output}")

    # Replay events at target FPS
    event_idx = 0
    total_events = len(player.events)
    current_sim_time = 0.0
    frame_interval = 1.0 / float(args.fps)
    speed = max(0.1, args.speed)
    end_time = player.duration / speed + 1.5

    while current_sim_time <= end_time:
        # Feed all events up to current sim time
        real_time_target = current_sim_time * speed
        while event_idx < total_events and player.events[event_idx][0] <= real_time_target:
            ev_time, ev_type, ev_data = player.events[event_idx]
            if ev_type == "o":
                parser.feed(ev_data)
            event_idx += 1

        frame_bytes = renderer.draw_frame(
            state,
            status_left=f"{args.title} | {current_sim_time:.1f}s / {player.duration / speed:.1f}s",
            status_right="TermReel Cast Replay",
        )
        pipe.write_frame(frame_bytes)
        current_sim_time += frame_interval

    pipe.close()
    print(f"✅ Finished rendering: {args.output} ({pipe.frame_count} frames)")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    if not os.path.exists(args.scenario):
        print(f"Error: Scenario file not found: {args.scenario}", file=sys.stderr)
        return 1

    try:
        manifest = ScenarioManifest.from_yaml_file(args.scenario)
        print(f"✅ Valid scenario manifest: {args.scenario}")
        print(f"   Title: {manifest.metadata.title}")
        print(f"   Resolution: {manifest.metadata.resolution[0]}x{manifest.metadata.resolution[1]} @ {manifest.metadata.fps} fps")
        print(f"   Theme: {manifest.metadata.theme}")
        print(f"   Timeline steps: {len(manifest.timeline)}")
        print(f"   Triggers: {len(manifest.triggers)}")
        return 0
    except Exception as e:
        print(f"❌ Scenario validation failed: {e}", file=sys.stderr)
        return 1


def cmd_themes() -> int:
    print("🎨 Available TermReel Visual Themes:\n")
    for name in list_themes():
        t = get_theme(name)
        fg_hex = termreel.rgb_to_hex(t.default_fg)
        bg_hex = termreel.rgb_to_hex(t.terminal_bg)
        acc_hex = termreel.rgb_to_hex(t.accent_color)
        print(f"  • {name:<18} (terminal: {bg_hex}, text: {fg_hex}, accent: {acc_hex})")
    return 0


def cmd_info() -> int:
    import cairo
    tmux_path = shutil.which("tmux")
    ffmpeg_path = shutil.which("ffmpeg")
    ffprobe_path = shutil.which("ffprobe")
    agy_path = shutil.which("agy")

    print(f"⚡ TermReel v{termreel.__version__} Environment Diagnostics:\n")
    print(f"  • Python:        {sys.version.split()[0]} ({sys.executable})")
    print(f"  • PyCairo:       {cairo.cairo_version_string()}")
    print(f"  • Tmux:          {tmux_path or 'NOT FOUND'}")
    print(f"  • FFmpeg:        {ffmpeg_path or 'NOT FOUND'}")
    print(f"  • FFprobe:       {ffprobe_path or 'NOT FOUND'}")
    print(f"  • Antigravity:   {agy_path or 'NOT FOUND'}")
    print(f"  • Themes:        {len(list_themes())} available")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.subcommand:
        parser.print_help()
        return 0

    if args.subcommand == "record":
        return cmd_record(args)
    elif args.subcommand == "exec":
        return cmd_exec(args)
    elif args.subcommand == "cast2video":
        return cmd_cast2video(args)
    elif args.subcommand == "validate":
        return cmd_validate(args)
    elif args.subcommand == "themes":
        return cmd_themes()
    elif args.subcommand == "info":
        return cmd_info()

    return 0


if __name__ == "__main__":
    sys.exit(main())
