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
from termreel.generator.explorer import CLIExplorer
from termreel.generator.scaffold import ScenarioGenerator


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
    record_parser.add_argument("-c", "--continue", "--resume", dest="resume", action="store_true", help="Resume the latest conversation or session in the workspace")
    record_parser.add_argument("--conversation", dest="conversation_id", help="Resume a specific previous conversation by ID")
    record_parser.add_argument("--workspace", dest="workspace_path", help="Attach to an existing workspace directory instead of creating a temporary one")
    record_parser.add_argument("--preserve-workspace", action="store_true", help="Preserve temporary workspace directory after scenario execution for future session resumption")
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

    # 5. probe
    probe_parser = subparsers.add_parser("probe", help="Explore a CLI tool by probing its help, version, and subcommands")
    probe_parser.add_argument("cli", help="Name or path of the CLI binary to probe (e.g. git, agy, gcloud, gh)")

    # 6. generate / init
    gen_parser = subparsers.add_parser("generate", aliases=["init"], help="Explore a CLI tool and generate a tailored scenario YAML manifest")
    gen_parser.add_argument("cli", help="Target CLI tool to scaffold scenario for (e.g. git, agy, gcloud, gh)")
    gen_parser.add_argument("-o", "--output", help="Output path for the generated YAML file (default: scenarios/<cli>_demo.yaml)")
    gen_parser.add_argument("--title", help="Window title for the recording")
    gen_parser.add_argument("--subtitle", help="Window subtitle for the recording")
    gen_parser.add_argument("--theme", default="catppuccin-mocha", help="Visual theme (default: catppuccin-mocha)")
    gen_parser.add_argument("--fps", type=int, default=30, help="Target recording FPS (default: 30)")
    gen_parser.add_argument("-p", "--print", action="store_true", help="Print generated YAML to stdout instead of saving")

    # 7. themes
    subparsers.add_parser("themes", help="List available visual themes and palettes")

    # 8. info
    subparsers.add_parser("info", help="Display environment, dependencies, and codec status")

    # 9. test
    test_parser = subparsers.add_parser("test", help="Run the test suite concurrently with high-speed async execution")
    test_parser.add_argument("-w", "--workers", type=int, default=8, help="Number of concurrent worker threads (default: 8)")
    test_parser.add_argument("-d", "--dir", default="tests", help="Directory containing test cases (default: tests)")

    # 10. batch
    batch_parser = subparsers.add_parser("batch", help="Run scenario recordings concurrently in parallel batches")
    batch_parser.add_argument("scenarios", nargs="+", help="Scenario YAML manifest files or glob patterns (e.g. scenarios/*.yaml)")
    batch_parser.add_argument("-c", "--concurrency", type=int, default=4, help="Number of parallel worker threads (default: 4)")
    batch_parser.add_argument("-o", "--output-dir", help="Destination directory for rendered MP4 videos")
    batch_parser.add_argument("--generate-posters", dest="generate_posters", action="store_true", default=True, help="Automatically extract poster frame for each (default: True)")
    batch_parser.add_argument("--no-posters", dest="generate_posters", action="store_false", help="Disable automatic poster extraction")
    batch_parser.add_argument("--poster-time", type=float, default=0.5, help="Timestamp in seconds for poster frame extraction (default: 0.5)")
    batch_parser.add_argument("--report", help="Destination path for JSON or Markdown batch summary report")
    batch_parser.add_argument("--theme", help="Override visual theme for all scenarios")
    batch_parser.add_argument("--fps", type=int, help="Override FPS for all scenarios")
    batch_parser.add_argument("-q", "--quiet", action="store_true", help="Suppress terminal progress overview")

    # 11. audit
    audit_parser = subparsers.add_parser("audit", help="Audit video recording quality using multimodal AI or local heuristics")
    audit_parser.add_argument("video", help="Path to video file to audit (e.g. output/demo.mp4)")
    audit_parser.add_argument("--spec", help="Path to scenario YAML manifest or specification file")
    audit_parser.add_argument("--model", default="gemini-3.1-pro-preview", help="Gemini multimodal model name (default: gemini-3.1-pro-preview)")
    audit_parser.add_argument("--threshold", type=int, default=80, help="Pass/fail scorecard score threshold 0-100 (default: 80)")
    audit_parser.add_argument("--chunk-duration", type=float, default=300.0, help="Maximum segment window in seconds for long video auditing (default: 300.0s / 5 mins)")
    audit_parser.add_argument("--no-chunk", action="store_true", help="Disable automated windowed chunking for long videos")
    audit_parser.add_argument("--report", help="Destination path to save Markdown or JSON audit report")
    audit_parser.add_argument("--json", action="store_true", help="Output scorecard in JSON format")

    # 12. peek
    peek_parser = subparsers.add_parser("peek", help="Observe running TermReel sessions in real-time")
    peek_parser.add_argument("session", nargs="?", default=None, help="Target session ID, prefix, or PID (default: latest active)")
    peek_parser.add_argument("-f", "--follow", "--watch", dest="follow", action="store_true", help="Follow live terminal updates in real-time")
    peek_parser.add_argument("--list", action="store_true", help="List all active and recent sessions")
    peek_parser.add_argument("--image", help="Capture high-res PNG vector screenshot of current frame")
    peek_parser.add_argument("--web", nargs="?", const=8989, type=int, default=None, help="Launch web dashboard (default: 8989)")
    peek_parser.add_argument("--raw", action="store_true", help="Output raw plain screen text without HUD banner")
    peek_parser.add_argument("--interval", type=float, default=0.1, help="Refresh interval for follow mode in seconds (default: 0.1)")

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

        if getattr(args, "resume", False):
            manifest.environment.resume = True
        if getattr(args, "conversation_id", None):
            manifest.environment.conversation_id = args.conversation_id
        if getattr(args, "workspace_path", None):
            manifest.environment.workspace_path = args.workspace_path
        if getattr(args, "preserve_workspace", False):
            manifest.environment.preserve_workspace = True

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
            if report.conversation_id:
                print(f"📌 Active Conversation ID: {report.conversation_id}")
            if report.workspace_dir:
                print(f"📁 Workspace Directory: {report.workspace_dir}")
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


def cmd_probe(args: argparse.Namespace) -> int:
    explorer = CLIExplorer(args.cli)
    if not explorer.is_installed():
        print(f"❌ Error: CLI binary '{args.cli}' was not found on PATH.", file=sys.stderr)
        return 1

    try:
        spec = explorer.probe()
        print(f"🔍 Discovered CLI Specification for: {spec.name}\n")
        print(f"  • Path:        {spec.executable_path}")
        print(f"  • Version:     {spec.version}")
        print(f"  • Category:    {spec.category.upper()}")
        print(f"  • Usage:       {spec.usage}")
        print(f"  • Summary:     {spec.summary}")
        if spec.subcommands:
            print(f"\n  📦 Detected Subcommands ({len(spec.subcommands)}):")
            for sub in spec.subcommands[:10]:
                print(f"     - {sub.name:<18} {sub.description[:50]}")
        if spec.flags:
            print(f"\n  🚩 Detected Flags ({len(spec.flags)}):")
            print(f"     {' '.join(spec.flags[:12])}")
        if spec.recommended_permissions:
            print(f"\n  🛡️  Recommended Permissions:")
            print(f"     {', '.join(spec.recommended_permissions)}")
        return 0
    except Exception as e:
        print(f"❌ Probing failed: {e}", file=sys.stderr)
        return 1


def cmd_generate(args: argparse.Namespace) -> int:
    explorer = CLIExplorer(args.cli)
    if not explorer.is_installed():
        print(f"❌ Error: CLI binary '{args.cli}' was not found on PATH.", file=sys.stderr)
        return 1

    try:
        spec = explorer.probe()
        yaml_content = ScenarioGenerator.generate(
            spec=spec,
            title=args.title,
            subtitle=args.subtitle,
            theme=args.theme,
            fps=args.fps,
        )

        if args.print:
            print(yaml_content)
            return 0

        target_output = args.output or f"examples/{spec.name}_generated.yaml"
        os.makedirs(os.path.dirname(os.path.abspath(target_output)), exist_ok=True)
        with open(target_output, "w", encoding="utf-8") as f:
            f.write(yaml_content)

        print(f"✨ Successfully generated scenario YAML for '{spec.name}': {target_output}")
        print(f"   Category: {spec.category.upper()} | Theme: {args.theme} | Steps: inferred")
        print(f"\nTo record this scenario, run:")
        print(f"   termreel record {target_output}")
        return 0
    except Exception as e:
        print(f"❌ Generation failed: {e}", file=sys.stderr)
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


def cmd_test(args: argparse.Namespace) -> int:
    from termreel.testing import run_parallel_tests
    return run_parallel_tests(start_dir=args.dir, max_workers=args.workers)


def cmd_batch(args: argparse.Namespace) -> int:
    from termreel.batch import BatchOrchestrator
    orchestrator = BatchOrchestrator(
        scenarios=args.scenarios,
        concurrency=args.concurrency,
        output_dir=args.output_dir,
        generate_posters=args.generate_posters,
        poster_time=args.poster_time,
        report_file=args.report,
        theme_override=args.theme,
        fps_override=args.fps,
        quiet=args.quiet,
    )
    report = orchestrator.run()
    return 0 if report.failed == 0 else 1


def cmd_audit(args: argparse.Namespace) -> int:
    if not os.path.exists(args.video):
        print(f"Error: Video file not found: {args.video}", file=sys.stderr)
        return 1

    from termreel.audit import VideoAuditor
    auditor = VideoAuditor(
        video_path=args.video,
        spec_path=args.spec,
        model_name=args.model,
        threshold=args.threshold,
        chunk_duration=getattr(args, "chunk_duration", 300.0),
        auto_chunk=not getattr(args, "no_chunk", False),
    )
    report = auditor.audit()


    if args.report:
        auditor.save_report(args.report)

    if args.json:
        print(report.to_json())
    else:
        print(report.to_markdown())

    return 0 if report.passed else 1


def cmd_peek(args: argparse.Namespace) -> int:
    from termreel.peek import PeekClient

    client = PeekClient()

    if getattr(args, "list", False):
        print(client.list_sessions())
        return 0

    session = client.find_target_session(getattr(args, "session", None))
    if session is None:
        target = getattr(args, "session", None)
        if target:
            print(f"❌ Error: No TermReel session found matching '{target}'.", file=sys.stderr)
        else:
            print("❌ Error: No active TermReel sessions found. Run 'termreel peek --list' to view recent sessions.", file=sys.stderr)
        return 1

    if getattr(args, "image", None):
        out_path = args.image
        success = client.capture_image(session, out_path)
        if success:
            print(f"📸 Frame capture saved to: {out_path}")
            return 0
        else:
            print(f"❌ Failed to capture frame image to: {out_path}", file=sys.stderr)
            return 1

    if getattr(args, "web", None) is not None:
        port = args.web
        client.serve_web(session, port=port)
        return 0

    if getattr(args, "follow", False):
        interval = getattr(args, "interval", 0.1)
        client.follow(session, interval=interval)
        return 0

    # Default: snapshot
    raw = getattr(args, "raw", False)
    snapshot = client.render_snapshot(session, raw=raw)
    print(snapshot)
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
    elif args.subcommand == "probe":
        return cmd_probe(args)
    elif args.subcommand in ("generate", "init"):
        return cmd_generate(args)
    elif args.subcommand == "themes":
        return cmd_themes()
    elif args.subcommand == "info":
        return cmd_info()
    elif args.subcommand == "test":
        return cmd_test(args)
    elif args.subcommand == "batch":
        return cmd_batch(args)
    elif args.subcommand == "audit":
        return cmd_audit(args)
    elif args.subcommand == "peek":
        return cmd_peek(args)

    return 0



if __name__ == "__main__":
    sys.exit(main())
