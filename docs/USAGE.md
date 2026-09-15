# TermReel User & Developer Guide

**TermReel** (`termreel`) is a standalone, headless CLI recording harness and deterministic video synthesis engine. It drives interactive CLIs, TUIs, and AI coding agents (`agy`, `gemini`, `gh`, `kubectl`, `vim`, Bubbletea/Textual apps) inside a real pseudo-terminal (PTY), simulates natural human keystrokes, reacts to live screen events, and streams pixel-perfect H.264 MP4, WebM, and GIF videos.

---

## 1. Quick Start

### Check Environment & Available Themes
```bash
termreel info
termreel themes
```

### Direct CLI Execution Recording
Record any command directly to video without writing a scenario file:
```bash
termreel exec "git status" --output output/git_status.mp4 --theme tokyo-night
```

### Declarative Scenario Recording
Record complex multi-step workshops and tutorials with custom chapter cards and triggers:
```bash
termreel record examples/agy_quickstart.yaml -o output/agy_quickstart.mp4
```

### Replay Asciinema Cast to Video
Convert an existing `.cast` recording file into high-fidelity video:
```bash
termreel cast2video session.cast -o session.mp4 --theme dracula
```

---

## 2. CLI Command Reference

### `termreel record <scenario.yaml>`
Runs a declarative YAML scenario and synthesizes the video stream.

| Option | Default | Description |
| :--- | :--- | :--- |
| `scenario` | *(required)* | Path to the YAML scenario manifest. |
| `-o, --output` | Manifest output | Target video path (`.mp4`, `.webm`, `.gif`). |
| `--fps` | `30` | Frame rate (FPS) for the synthesized video. |
| `--theme` | `catppuccin-mocha` | Visual color theme for chrome and terminal. |
| `--backend` | `auto` | PTY supervisor backend (`auto`, `tmux`, `pty`). |
| `--cast` | `None` | Export Asciinema v2 `.cast` event log. |
| `--poster` | `None` | Extract a high-resolution PNG thumbnail poster. |
| `-q, --quiet` | `False` | Suppress verbose step execution logs. |

### `termreel exec "<command>"`
Runs a single command in an isolated PTY and records the session.

| Option | Default | Description |
| :--- | :--- | :--- |
| `command` | *(required)* | Shell command or CLI binary to run. |
| `-o, --output` | `output/exec_session.mp4` | Target video path. |
| `--title` | `TermReel Execution` | Window titlebar title. |
| `--subtitle` | `Live Command` | Window titlebar subtitle. |
| `--cwd` | Current dir | Working directory for the command. |
| `--fps` | `30` | Recording FPS. |
| `--theme` | `catppuccin-mocha` | Visual theme. |
| `--timeout` | `60.0` | Maximum recording timeout in seconds. |

### `termreel cast2video <input.cast>`
Transcodes an asciicast `.cast` log into an MP4 or GIF video.

| Option | Default | Description |
| :--- | :--- | :--- |
| `cast_file` | *(required)* | Path to input `.cast` file. |
| `-o, --output` | `output/cast_render.mp4` | Target output video path. |
| `--fps` | `30` | Output frame rate. |
| `--theme` | `catppuccin-mocha` | Visual theme. |
| `--speed` | `1.0` | Playback speed multiplier (e.g. `1.5` for 1.5x speed). |

### `termreel live [command]`
Records an interactive terminal session driven by hand, with hotkey pause/resume and crossfading.

| Option | Default | Description |
| :--- | :--- | :--- |
| `command` | `$SHELL` | Shell or CLI command to record (e.g. `bash`, `agy`). |
| `-o, --output` | `output/live.mp4` | Output video path. |
| `--fps` | `15` | Frame rate (15 FPS recommended for human typing). |
| `--theme` | `catppuccin-mocha` | Visual color theme. |
| `--prefix` | `C-t` | Hotkey prefix key for pause (`^T p`) and stop (`^T q`). |
| `--crossfade` | `0.25` | Crossfade blend duration across cuts in seconds. |
| `--cast` | `None` | Also export an Asciinema `.cast` event log. |

### `termreel peek [session_id]`
Non-invasively inspects running recording sessions via local UNIX socket IPC.

| Option | Default | Description |
| :--- | :--- | :--- |
| `session_id` | Latest active | Target session ID, prefix, or PID. |
| `-f, --follow` | `False` | Live TUI stream (10 FPS) following terminal updates. |
| `--image` | `None` | Render and export a high-res PNG vector screenshot. |
| `--web` | `None` | Launch local web dashboard (default port `8989`). |
| `--raw` | `False` | Dump raw plain screen text without HUD borders. |
| `--list` | `False` | List all active and recent recording sessions. |

### `termreel mask`
Inspects, tests, and validates screen masking and realistic value substitutions.

| Option | Default | Description |
| :--- | :--- | :--- |
| `--verify, -v` | `None` | Verify mask rules against a `.cast`, `.yaml`, or text file. |
| `--strict` | `False` | Fail with exit code 1 if any configured rule has 0 matches. |
| `--test` | `None` | Test mask rules against an inline text string. |
| `--list` | `False` | List all active masking rules loaded from global config. |

### `termreel probe <command>`
Analyzes an interactive binary, extracting subcommands, help text, and permission requirements.

### `termreel generate <command>`
Scaffolds a customized scenario YAML manifest with chapter cards, cadences, and color themes.

### `termreel batch <scenarios...>`
Concurrently renders multiple scenario manifests in parallel with structured JSON/Markdown reports.

### `termreel audit <video.mp4>`
Multimodal video quality and specification rubric evaluation powered by Gemini and Vertex AI.

### `termreel test`
Accelerated parallel test runner with auto-scaling workers, fast mode (`-f`), and filter patterns (`-k`).

### `termreel themes`
Lists all built-in color themes with terminal, text, and accent color hex codes.

### `termreel info`
Displays Python, PyCairo, Tmux, FFmpeg, and Antigravity CLI diagnostic status.

---

## 3. Scenario Manifest Specification (YAML)

A TermReel scenario is structured into five primary sections:

```yaml
version: "1.0"

metadata:
  title: "Antigravity CLI Field Workshop"
  subtitle: "Module 1: SDLC Productivity"
  output: "output/module_01.mp4"
  poster_output: "output/module_01_poster.png"
  cast_output: "output/module_01.cast"
  resolution: [1280, 720]
  fps: 30
  theme: "catppuccin-mocha"
  crf: 20
  preset: "medium"
  statusbar_left: "Antigravity CLI v1.1.22 | Real TTY | gemini-3.7-flash"
  statusbar_right: "TermReel HD"

environment:
  create_temp_workspace: true
  temp_workspace_prefix: "agy_ws_"
  setup_commands:
    - "git init"
    - "echo 'def run(): pass' > app.py"
    - "git add . && git commit -m 'Initial commit'"
  cleanup_commands: []

redactions:
  - "ya29\\.[a-zA-Z0-9_\\-]+"
  - "AIza[0-9A-Za-z\\-_]{25,45}"

triggers:
  - on_match: "Do you trust the contents of this project"
    action: "Enter"
    once: true
  - on_match: "Approve change\\? \\[y/N\\]"
    action:
      type: "type"
      value: "y"
      delay: 0.2
    once: false

timeline:
  - show_card:
      tag: "Module 1"
      title: "Interactive Code Review"
      desc: "Analyzing workspace with Antigravity CLI"
      duration: 2.5

  - launch:
      command: "agy --add-dir . --dangerously-skip-permissions"
      wait_for_idle: true
      timeout: 15.0

  - type:
      text: "What files are in this project?"
      speed: 0.035
      jitter: 0.015
      send_key: "Enter"

  - wait_for_idle:
      timeout: 45.0
      reading_pause: 2.5

  - type:
      text: "/exit"
      send_key: "Enter"
      pause: 1.0

  - show_card:
      tag: "Complete"
      title: "Session Finished"
      duration: 2.0
```

### Timeline Action Types

| Action | Parameters | Description |
| :--- | :--- | :--- |
| `show_card` / `card` | `tag`, `title`, `desc`, `duration` | Renders a styled chapter or announcement card. |
| `launch` | `command`, `env`, `wait_for_idle`, `wait_for_prompt`, `timeout` | Launches the interactive CLI process in a PTY/tmux. |
| `type` | `text`, `speed`, `jitter`, `typos`, `send_key`, `pause`, `collapse_newlines` | Types text with simulated human typing cadence. |
| `send_key` / `key` | `key`, `delay_before`, `pause_after` | Sends special key (string or structured dict). |
| `edit_file` / `edit` | `path`, `content`, `editor`, `action`, `syntax`, `pause_after` | Hermetic Vim file editor with clean buffer handling. |
| `speedup` | `factor`, `indicator`, `min_duration` | Time dilation / timelapse with synced `.cast` clock. |
| `assert` / `assert_output` | `contains`, `not_contains`, `pattern`, `scope`, `on_fail` | Semantic assertion gate with scrollback inspection. |
| `split_pane` | `direction`, `size_percent`, `command` | Split tmux window horizontally or vertically. |
| `inspect_modal` | `open_command`, `open_key`, `wait_for_render`, `display_duration` | Opens, views, and dismisses a TUI modal dialog. |
| `paste` | `text`, `pause` | Pastes a text block using bracketed paste mode. |
| `wait_for_idle` | `timeout`, `reading_pause`, `idle_regex`, `wait_for_prompt` | Polls until CLI finishes processing and returns to prompt. |
| `wait_for_text` / `wait` | `pattern`, `timeout`, `pause` | Polls until a regex pattern appears on screen. |
| `pause` / `sleep` | `seconds` | Static duration sleep. |
| `run_shell` / `exec` | `command`, `speed`, `pause`, `speedup`, `assert_output` | Types shell command and executes with Enter. |
| `set_statusbar` | `left`, `right`, `pill` | Dynamically updates status bar text and pill. |

---

## 4. Visual Themes

TermReel includes 9 built-in color themes:

- `catppuccin-mocha` (Default dark theme)
- `catppuccin-latte` (Clean light theme)
- `dracula` (High-contrast dark purple theme)
- `tokyo-night` (Deep indigo night theme)
- `nord` (Arctic blue palette)
- `one-dark` (Atom/VS Code One Dark palette)
- `monokai` (Vibrant green/yellow/pink theme)
- `github-dark` (Official GitHub Dark theme)
- `matrix` (Terminal green hacker aesthetic)

---

## 5. Python Programmatic API

You can also use TermReel programmatically from Python:

```python
from termreel import (
    ScenarioManifest,
    ScenarioRunner,
    CairoTerminalRenderer,
    TerminalState,
    ANSIParser,
    TmuxSupervisor,
    FFmpegPipe,
)

# 1. Run a scenario
manifest = ScenarioManifest.from_yaml_file("examples/git_workflow.yaml")
runner = ScenarioRunner(manifest=manifest, backend="tmux")
report = runner.run()
print(f"Recorded {report.duration_sec:.1f}s video -> {report.output_file}")

# 2. Custom headless rendering pipeline
renderer = CairoTerminalRenderer(width=1280, height=720, theme="tokyo-night")
state = TerminalState(rows=renderer.rows, cols=renderer.cols)
parser = ANSIParser(state)

parser.feed("Hello \x1b[32mWorld\x1b[0m!\r\n")

with FFmpegPipe("output/custom.mp4", width=1280, height=720, fps=30) as pipe:
    for _ in range(60):
        frame = renderer.draw_frame(state, status_left="Custom Python Session")
        pipe.write_frame(frame)
```

---

## 6. Antigravity (`agy`) Workshop Automation Guide

When recording interactive sessions with `agy`:

1. **Workspace Trust Prompts:** `agy` prompts `Do you trust the contents of this project?` on initial launch in new folders. TermReel triggers auto-detect this prompt and inject `Enter`.
2. **Permissions:** Use `--dangerously-skip-permissions` or register auto-approval triggers (`Approve change? [y/N]` -> `y` + `Enter`).
3. **Model Latency Handling:** Always use `wait_for_idle` instead of hardcoded sleeps. TermReel monitors the PTY buffer for busy spinners (`⡿ Generating...`, `⠋ Thinking...`) and proceeds only when the `? for shortcuts` idle prompt returns.

---

## 7. Controlling & Observing `agy` with Lifecycle Hooks

Antigravity CLI supports declarative lifecycle hooks defined in `<workspace>/.agents/hooks.json` (see [Antigravity Hooks Specification](https://antigravity.google/docs/hooks.md)). TermReel seamlessly integrates with this mechanism to provide **deterministic control and observability without YOLO flags**:

### Why Use Hooks in TermReel?
- **Zero-Friction Tool Auto-Approval:** By handling `PreToolUse` hooks, TermReel automatically approves agent actions (such as reading, writing, and executing code) in full interactive TUI mode—without requiring `--dangerously-skip-permissions` or `-p` (headless mode).
- **Dynamic Video Chrome Badging:** TermReel listens to `PreToolUse` and `PostToolUse` events to update the video statusbar with real-time badges (e.g. `● RUNNING READ_FILE`, `● GENERATING`).
- **Deterministic Synchronization:** Instead of relying purely on screen OCR or polling, scenario timelines can use `wait_for_hook_event` and `assert_hook_event` for instant synchronization.

### Scenario Environment Options
```yaml
environment:
  agy_hooks: true          # Automatically deploy .agents/hooks.json and hook script
  agy_auto_approve: true   # Auto-approve PreToolUse tool invocations
  agy_event_bridge: true   # Stream lifecycle events into TermReel event bus
  agy_custom_policy:       # Optional granular allow/deny tool overrides
    dangerous_tool: deny
    write_file: allow
```

### Hook Timeline Steps
```yaml
timeline:
  - launch:
      command: "agy"
      wait_for_idle: true

  # Wait deterministically for the model to finish its turn
  - wait_for_hook_event:
      event: "PostInvocation"
      timeout: 30.0
      pause: 1.0

  # Assert that the agent called the expected tool
  - assert_hook_event:
      event: "PreToolUse"
      tool: "write_file"
```

