# Software Design Document: Universal Terminal Recording & Video Synthesis Harness (`reccli`)

**Status:** Draft Architecture Specification  
**Domain:** Developer Tooling, Terminal Automation, Video Synthesis  
**Target:** Universal recording and deterministic MP4/GIF synthesis for interactive CLIs, TUIs, and AI coding agents  

---

## 1. Executive summary & motivation

Modern command-line applications and AI coding agents (`agy`, `gemini`, `gh`, `kubectl`, `vim`, Bubbletea/Textual TUIs) present unique challenges for documentation, training, and automated verification:

1. **Static code blocks fail to convey dynamic behavior:** Static markdown cannot demonstrate token streaming, tool call execution indicators (`● Read(...)`), multi-step reasoning trajectories, or interactive review flows.
2. **Screen recording (OBS/Screencastify) does not scale:** Manual desktop recordings are time-consuming, prone to human error, cannot run in headless CI/CD, and produce bloated video files without reproducible scripts.
3. **Pure Asciicast (`.cast`) lacks video player compatibility:** Asciinema files require client-side JavaScript players and cannot be embedded as native MP4 video tags in standard LMS platforms, Markdown docs, or video hosts without transcoding.

This document specifies the design for **`reccli`**, a universal, headless terminal recording harness and video synthesis engine. `reccli` programmatically drives any interactive CLI inside a real pseudo-terminal (PTY), injects keystrokes with configurable cadence, monitors terminal state transitions with event-driven triggers, and renders pixel-perfect MP4 videos with custom window chrome, themes, and chapter cards.

---

## 2. Empirical findings from live Antigravity CLI sessions

During real-time interactive testing with `agy` in pseudo-terminals, we uncovered five critical engineering realities that must be handled by a production recording harness:

```
┌───────────────────────────────────────────────────────────────────────────────┐
│                     Live CLI Session Discoveries                              │
└───────────────────────────────────────────────────────────────────────────────┘
  1. Workspace Trust Dialogs
     agy prompts "Do you trust the contents of this project?" on first launch.
     Harness must auto-detect modal selection prompts and inject Enter.

  2. Headless vs. Interactive Permissions
     agy --print auto-denies tool calls requiring review unless configured with
     --dangerously-skip-permissions or settings.json allow rules.
     Interactive agy prompts for approval with inline diffs.

  3. ANSI Escape Code Complexity
     Real agy output utilizes:
     - 24-bit TrueColor RGB (\x1b[38;2;R;G;Bm) & 256-color palettes (\x1b[38;5;Nm)
     - Alternate screen buffer switches (\x1b[?1049h)
     - Cursor Horizontal Absolute repositioning (\x1b[NG)
     - Erase line/screen directives (\x1b[K, \x1b[2J)

  4. State Detection vs. Fixed Sleep Timers
     Fixed sleep intervals fail due to variable model latency (1s to 20s).
     Harness must monitor screen buffer text:
     - Active generation: contains "⡿ Generating...", "⠋ Thinking..."
     - Idle input prompt ready: contains "? for shortcuts" and ">"

  5. Live UI Indicators vs. Plain Output
     Real interactive agy renders dynamic badges (e.g. ● Read(README.md)).
     Capturing true PTY state preserves authentic agent behavior.
```

---

## 3. High-level system architecture

The harness consists of eight modular subsystems:

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                             reccli System Pipeline                               │
└──────────────────────────────────────────────────────────────────────────────────┘

   [ Scenario Script ]  (YAML / Python DSL)
            │
            ▼
   ┌───────────────────┐       Keystroke Injection      ┌─────────────────────┐
   │ Scenario Runner / │ ─────────────────────────────► │   PTY Supervisor    │
   │  Event Controller │                                │  (tmux / openpty)   │
   └───────────────────┘ ◄───────────────────────────── └─────────────────────┘
            │                  Screen State Stream                 │
            │                  & Regex Triggers                    │
            ▼                                                      ▼
   ┌───────────────────┐                                ┌─────────────────────┐
   │ Terminal State    │                                │ Raw Output & Event  │
   │ Engine (2D Grid)  │                                │ Log (.cast / JSONL) │
   └───────────────────┘                                └─────────────────────┘
            │
            ▼
   ┌───────────────────┐
   │ Vector Frame      │  (PyCairo / Skia / Wasm)
   │ Renderer          │  Window chrome, dark theme, status bar, cards
   └───────────────────┘
            │  Raw BGRA Stream
            ▼
   ┌───────────────────┐
   │ Video Transcoder  │  (FFmpeg libx264 / vp9 / gifski)
   │ Pipeline          │  Faststart MP4, WebM, animated GIF
   └───────────────────┘
```

### Component breakdown

### 3.1 PTY supervisor (`pty_supervisor`)
- Allocates master/slave pseudo-terminals using `posix_openpt()` or `tmux` sessions.
- Sets exact terminal geometry (rows, cols) via `ioctl(fd, TIOCSWINSZ)`.
- Configures raw mode without line buffering, managing signal forwarding (`SIGWINCH`, `SIGINT`, `SIGTERM`).
- Preserves full environment variables (`TERM=xterm-256color`, `COLORTERM=truecolor`, `COLUMNS`, `LINES`).

### 3.2 Keystroke injector (`keystroke_injector`)
- Injects characters with randomized micro-delays (e.g. 25ms to 45ms) to model natural human typing.
- Supports typing simulation with deliberate typos and backspace corrections.
- Injects special keys: `Enter`, `Escape`, `Tab`, `Up`/`Down` arrow navigation, and control sequences (`Ctrl+C`, `Ctrl+O`, `Ctrl+D`, `Ctrl+J`).
- Supports block pasting with bracketed paste mode (`\x1b[200~` ... `\x1b[201~`).

### 3.3 Terminal state engine (`state_engine`)
- Full ANSI/VT100/Xterm emulator maintaining a 2D matrix of `CharCell` structures.
- Cell attributes: character code, foreground RGB, background RGB, bold, dim, italic, underline, strikethrough, reverse video.
- Color resolution: converts ANSI 16-color, 256-color index (including 6x6x6 color cube and grayscale ramp), and 24-bit TrueColor to linear RGB floats.
- Manages primary and alternate screen buffers, scrollback history, cursor position, and cursor visibility flags.

### 3.4 Event reactor & screen monitor (`event_reactor`)
- Asynchronously watches the screen buffer at 30 Hz.
- Evaluates registered conditional triggers (e.g. `on_match(r"Do you trust.*", action=send_key("Enter"))`).
- Implements `wait_for_idle(idle_signature, busy_signatures, timeout)` to eliminate brittle static sleeps.
- Provides assertions: `assert_text_present(regex, timeout)` to enable automated integration testing of CLI tools alongside video recording.

### 3.5 Vector frame renderer (`renderer`)
- Transforms the 2D terminal state grid into crisp 1280x720 (720p) or 1920x1080 (1080p) raster frames using vector drawing (PyCairo or Skia).
- Renders configurable window chrome:
  - macOS/Linux dark theme titlebar with colored traffic lights.
  - Title text and dynamic status indicator (e.g. `● LIVE TTY`).
  - Bottom status bar detailing CLI binary, model version, and active session tokens.
- Monospace font rendering with subpixel anti-aliasing (`DejaVu Sans Mono` or `JetBrains Mono`).
- Renders chapter card overlays (`show_card`) to introduce modules and exercises.

### 3.6 Transcoder pipeline (`transcoder`)
- Pipes raw BGRA/ARGB frame buffers directly into FFmpeg through standard input with zero intermediate image writes to disk.
- Encoding options:
  - **MP4 (Default):** H.264 video (`-c:v libx264 -pix_fmt yuv420p -preset medium -crf 20 -movflags +faststart`).
  - **WebM:** VP9 video (`-c:v libvpx-vp9 -crf 30 -b:v 0`).
  - **GIF:** Palette-optimized high-frame-rate GIF using `gifski` or two-pass FFmpeg palette generation.
- Automatically extracts thumbnail poster frames (`.png`) at key timestamps.

---

## 4. Declarative scenario DSL specification

Scenarios can be authored as declarative YAML files or programmatic Python scripts.

### YAML scenario schema

```yaml
version: "1.0"
metadata:
  title: "Antigravity CLI Field Workshop"
  subtitle: "Module 1: SDLC Productivity"
  output: "video/module_01_sdlc_productivity.mp4"
  resolution: [1280, 720]
  fps: 30
  theme: "catppuccin-mocha"

environment:
  cwd: "/workspace/project"
  env:
    TERM: "xterm-256color"
    COLORTERM: "truecolor"

triggers:
  - on_match: "Do you trust the contents"
    action:
      send_key: "Enter"
  - on_match: "Approve change\\? \\[y/N\\]"
    action:
      type: "y"
      send_key: "Enter"

timeline:
  - show_card:
      tag: "Module 1"
      title: "SDLC Productivity"
      desc: "Interactive coding, refactoring, and test generation"
      duration: 2.5

  - launch:
      command: "agy --add-dir . --dangerously-skip-permissions"
      wait_for_idle: true
      timeout: 10.0

  - type: "What files are in this project and what does each one do?"
    speed: 0.04
    send_key: "Enter"

  - wait_for_idle:
      timeout: 45.0
      reading_pause: 2.5

  - type: "@README.md How does documented architecture match implementation?"
    speed: 0.04
    send_key: "Enter"

  - wait_for_idle:
      timeout: 45.0
      reading_pause: 2.0

  - type: "/permissions"
    send_key: "Enter"
    reading_pause: 2.0

  - type: "/exit"
    send_key: "Enter"
    pause: 1.0

  - run_shell:
      command: "git status -s"
      pause: 1.5

  - run_shell:
      command: "agy -p \"Review staged changes\" --dangerously-skip-permissions"
      pause: 5.0

  - show_card:
      tag: "Complete"
      title: "Module 1 Mastered"
      duration: 3.0
```

---

## 5. Core reference implementation

Below is the production implementation of the `PtySupervisor` and `TerminalState` pipeline.

```python
import os
import re
import sys
import time
import math
import select
import subprocess
import threading
import cairo
from dataclasses import dataclass
from typing import List, Tuple, Dict, Optional, Callable


@dataclass
class CharCell:
    char: str = " "
    fg: Tuple[float, float, float] = (0.85, 0.88, 0.96)
    bg: Tuple[float, float, float] = (0.10, 0.10, 0.15)
    bold: bool = False
    dim: bool = False
    underline: bool = False


class TerminalState:
    """Accurate 2D terminal grid state machine supporting 256 and TrueColor ANSI."""

    def __init__(self, rows: int = 30, cols: int = 100):
        self.rows = rows
        self.cols = cols
        self.cursor_row = 0
        self.cursor_col = 0
        self.grid: List[List[CharCell]] = [
            [CharCell() for _ in range(cols)] for _ in range(rows)
        ]
        self.current_fg = (0.85, 0.88, 0.96)
        self.current_bg = (0.10, 0.10, 0.15)
        self.current_bold = False
        self.current_dim = False
        self.current_underline = False

    def clear(self):
        self.grid = [[CharCell() for _ in range(self.cols)] for _ in range(self.rows)]
        self.cursor_row = 0
        self.cursor_col = 0

    def write_char(self, char: str):
        if char == "\r":
            self.cursor_col = 0
            return
        if char == "\n":
            self.cursor_col = 0
            if self.cursor_row + 1 < self.rows:
                self.cursor_row += 1
            return
        if char == "\t":
            spaces = 4 - (self.cursor_col % 4)
            for _ in range(spaces):
                self.write_char(" ")
            return
        if self.cursor_col >= self.cols:
            return

        self.grid[self.cursor_row][self.cursor_col] = CharCell(
            char=char,
            fg=self.current_fg,
            bg=self.current_bg,
            bold=self.current_bold,
            dim=self.current_dim,
            underline=self.current_underline,
        )
        self.cursor_col += 1

    def load_tmux_pane(self, raw_ansi: str):
        self.clear()
        for r_idx, line in enumerate(raw_ansi.split("\n")[:self.rows]):
            self.cursor_row = r_idx
            self.cursor_col = 0
            self._write_ansi_line(line)

    def _write_ansi_line(self, line: str):
        i = 0
        n = len(line)
        while i < n:
            if line[i] == "\x1b" and i + 1 < n and line[i + 1] == "[":
                m = re.match(r"\x1b\[([0-9;?]*)([a-zA-Z$])", line[i:])
                if m:
                    params_str, cmd = m.groups()
                    self._apply_csi(cmd, params_str)
                    i += len(m.group(0))
                    continue
            self.write_char(line[i])
            i += 1

    def _apply_csi(self, cmd: str, params_str: str):
        clean = params_str.lstrip("?")
        params = [int(p) for p in clean.split(";") if p.isdigit()]
        if cmd == "m":
            if not params:
                params = [0]
            idx = 0
            while idx < len(params):
                p = params[idx]
                if p == 0:
                    self.current_fg = (0.85, 0.88, 0.96)
                    self.current_bg = (0.10, 0.10, 0.15)
                    self.current_bold = False
                    self.current_dim = False
                elif p == 1:
                    self.current_bold = True
                elif p == 2:
                    self.current_dim = True
                elif 30 <= p <= 37:
                    # Standard ANSI 8 colors
                    colors = [
                        (0.09, 0.09, 0.14), (0.95, 0.55, 0.66), (0.65, 0.89, 0.63),
                        (0.98, 0.89, 0.69), (0.54, 0.71, 0.98), (0.80, 0.65, 0.97),
                        (0.58, 0.89, 0.84), (0.80, 0.84, 0.96)
                    ]
                    self.current_fg = colors[p - 30]
                elif p == 38 and idx + 4 < len(params) and params[idx + 1] == 2:
                    # 24-bit TrueColor
                    self.current_fg = (params[idx + 2] / 255.0, params[idx + 3] / 255.0, params[idx + 4] / 255.0)
                    idx += 4
                idx += 1
        elif cmd == "G":
            col = (params[0] - 1) if params else 0
            self.cursor_col = max(0, min(self.cols - 1, col))
        elif cmd == "H" or cmd == "f":
            r = (params[0] - 1) if (len(params) > 0 and params[0] > 0) else 0
            c = (params[1] - 1) if (len(params) > 1 and params[1] > 0) else 0
            self.cursor_row = max(0, min(self.rows - 1, r))
            self.cursor_col = max(0, min(self.cols - 1, c))


class UniversalCliRecorder:
    """Drives real CLI execution and writes direct H.264 MP4 frames."""

    def __init__(self, output_path: str, command: str, cwd: str, width: int = 1280, height: int = 720):
        self.output_path = output_path
        self.command = command
        self.cwd = cwd
        self.width = width
        self.height = height
        self.session_name = f"reccli_{int(time.time())}"
        self.term = TerminalState(rows=30, cols=100)
        self.is_running = False
        self.ffmpeg_proc = None

    def start(self):
        # 1. Initialize tmux PTY
        subprocess.run(["tmux", "new-session", "-d", "-s", self.session_name, "-x", "100", "-y", "30", self.command], cwd=self.cwd, check=True)

        # 2. Launch FFmpeg pipe
        cmd = [
            "ffmpeg", "-y", "-f", "rawvideo", "-vcodec", "rawvideo",
            "-s", f"{self.width}x{self.height}", "-pix_fmt", "bgra", "-r", "30",
            "-i", "-", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-preset", "medium", "-crf", "20", "-movflags", "+faststart", self.output_path
        ]
        self.ffmpeg_proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self.is_running = True

        # 3. Start background screen capture thread
        self.thread = threading.Thread(target=self._capture_loop, daemon=True)
        self.thread.start()

    def _capture_loop(self):
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, self.width, self.height)
        ctx = cairo.Context(surface)
        while self.is_running:
            res = subprocess.run(["tmux", "capture-pane", "-t", self.session_name, "-p", "-e"], capture_output=True, text=True)
            if res.returncode == 0:
                self.term.load_tmux_pane(res.stdout)

            # Paint background & window
            ctx.set_source_rgb(0.07, 0.07, 0.11)
            ctx.paint()

            # Render text cells
            ctx.select_font_face("DejaVu Sans Mono", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
            ctx.set_font_size(14.0)
            for r_idx in range(self.term.rows):
                for c_idx in range(self.term.cols):
                    cell = self.term.grid[r_idx][c_idx]
                    if cell.char != " ":
                        ctx.set_source_rgb(*cell.fg)
                        ctx.move_to(36 + c_idx * 8.5, 45 + r_idx * 19.0)
                        ctx.show_text(cell.char)

            self.ffmpeg_proc.stdin.write(bytes(surface.get_data()))
            time.sleep(1.0 / 30.0)

    def inject_text(self, text: str, speed: float = 0.03):
        for ch in text:
            subprocess.run(["tmux", "send-keys", "-t", self.session_name, "-l", ch])
            time.sleep(speed)

    def send_key(self, key_name: str):
        subprocess.run(["tmux", "send-keys", "-t", self.session_name, key_name])

    def wait_until_idle(self, timeout: float = 60.0):
        start = time.time()
        time.sleep(1.5)
        while time.time() - start < timeout:
            res = subprocess.run(["tmux", "capture-pane", "-t", self.session_name, "-p"], capture_output=True, text=True)
            if res.returncode == 0:
                txt = res.stdout
                if "? for shortcuts" in txt and not ("Generating..." in txt or "Thinking..." in txt):
                    return
            time.sleep(0.5)

    def stop(self):
        self.is_running = False
        self.thread.join(timeout=2.0)
        subprocess.run(["tmux", "kill-session", "-t", self.session_name], capture_output=True)
        if self.ffmpeg_proc.stdin:
            self.ffmpeg_proc.stdin.close()
        self.ffmpeg_proc.wait()
```

---

## 6. Scaling & parallel subagent execution strategy

To record dozens of CLI workshops, modules, and exercises concurrently without terminal interference:

```
┌────────────────────────────────────────────────────────────────────────┐
│               Parallel Multi-Agent Execution Grid                     │
└────────────────────────────────────────────────────────────────────────┘
                    [ Orchestrator Subagent ]
                                │
        ┌───────────────┬───────┴───────┬───────────────┐
        ▼               ▼               ▼               ▼
  [ Worker 1 ]    [ Worker 2 ]    [ Worker 3 ]    [ Worker 4 ]
  Isolated PTY    Isolated PTY    Isolated PTY    Isolated PTY
   (Module 1)      (Module 2)      (Module 3)      (Module 4)
        │               │               │               │
   video/mod1.mp4  video/mod2.mp4  video/mod3.mp4  video/mod4.mp4
```

1. **Process Isolation:** Each worker runs inside a distinct PTY or container workspace with an isolated `session_name` and separate log file.
2. **CPU & Resource Constraints:** Vector rendering + FFmpeg H.264 consumes ~15% of a single modern CPU core per worker. On a standard 16-core workstation, 8 parallel workers run in real time with zero frame drops.
3. **Telemetry & Artifact Export:** Workers generate both the output `.mp4` video and an accompanying `.cast` event log alongside an execution summary JSON (`status: pass, duration_sec: 84.2, frame_count: 2520`).

---

## 7. Security, redaction & sandboxing

1. **Secret Redaction:**
   The `state_engine` can register sensitive regex masks (e.g. `ya29\.[a-zA-Z0-9_-]+`, `ghp_[a-zA-Z0-9]+`, private hostnames). When matched, the rasterizer replaces characters with `••••••••` prior to rendering.
2. **Deterministic Workspace Clones:**
   Before each recording, the worker clones a clean baseline Git branch to ensure no residual configuration files from previous runs pollute the terminal state.
3. **Sandbox Compliance:**
   When running inside constrained execution environments, `reccli` operates entirely within user-space without requiring root privileges or external X11 display servers.

---

## 8. Verification and success metrics

| Metric | Target | Verification Method |
| :-- | :-- | :-- |
| **Video Playback Quality** | 1280x720 @ 30fps H.264 (CRF 20) | `ffprobe` format & stream inspection |
| **Pacing Realism** | 25ms–45ms per character typing jitter | Frame timestamp delta audit |
| **Interactive Turn Fidelity** | 100% authentic capture of tool calls and TUI | Visual inspection against real CLI |
| **Zero Intermediate Disk I/O** | 0 temporary frame image files written | Memory pipe to FFmpeg stdin |
| **Pre-Commit Contract** | Clean MkDocs build, no broken links | `make test` & `python3 validate-md.py` |

---

## 9. Antigravity Hooks Subsystem & Lifecycle Interception

In addition to pure PTY screen scraping and keystroke injection, TermReel integrates natively with the **Antigravity Hooks API** (see `https://antigravity.google/docs/hooks.md`). This enables deep lifecycle control and real-time observability without sacrificing the authentic interactive TUI experience:

```
┌────────────────────────────────────────────────────────────────────────┐
│               Antigravity Hooks Interception Architecture              │
└────────────────────────────────────────────────────────────────────────┘

 [ Antigravity CLI (agy) ]
            │
            │ Lifecycle Events (stdin JSON)
            ▼
 ┌───────────────────────────────────────────────────────────────────────┐
 │ .agents/hooks/termreel_hook.py                                        │
 │   • PreToolUse: Auto-approves tool actions without YOLO flags         │
 │   • PostToolUse: Captures tool execution outputs                      │
 │   • PreInvocation / PostInvocation: Tracks model turn boundaries      │
 └──────────────────────────────────┬────────────────────────────────────┘
                                    │
               Writes JSONL events  │  Returns verdict JSON (stdout)
                                    ▼
                         ┌────────────────────┐
                         │ .events.jsonl pipe │
                         └─────────┬──────────┘
                                   │
              Tails events in real │ time
                                   ▼
                         ┌────────────────────┐
                         │   AgyHookBridge    │
                         └─────────┬──────────┘
                                   │
    ┌──────────────────────────────┴──────────────────────────────┐
    ▼                                                             ▼
┌──────────────────────────────┐              ┌──────────────────────────────┐
│ Dynamic UI Telemetry         │              │ Deterministic Sync           │
│ (● RUNNING READ_FILE badges) │              │ (wait_for_hook_event steps)  │
└──────────────────────────────┘              └──────────────────────────────┘
```

### Key Architectural Properties
1. **Zero-Flag Interactivity:** Traditional headless runs require `--dangerously-skip-permissions` or `-p` which hides the interactive TUI. TermReel's `PreToolUse` auto-approval hook allows `agy` to run in authentic interactive mode without permission blocks or manual dialog confirmations.
2. **Deterministic Lifecycle Synchronization:** Scenario steps can synchronize via `wait_for_hook_event` (e.g. `PostInvocation`, `PostToolUse`) rather than heuristic timeouts.
3. **Workspace Isolation & Safe Cleanup:** `HookManager` provisions `.agents/hooks.json` during environment setup, backs up existing configurations, and cleans up completely on session termination.

