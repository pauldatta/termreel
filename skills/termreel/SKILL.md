---
name: termreel
description: Automate, record, and synthesize high-fidelity terminal videos (MP4/WebM/GIF) and Asciinema (.cast) logs from interactive CLIs, TUIs, and AI coding agents (such as agy, git, gcloud, gh) using pseudo-terminals (PTY/tmux), natural typing simulation, reactive triggers, and vector rendering. Use when creating CLI walkthroughs, recording TUI demos, capturing interactive AI agent sessions, or generating deterministic video proof artifacts.
---

# TermReel: Universal Terminal Recording & Video Synthesis Engine

TermReel (`termreel` / `reccli`) is a headless recording harness and deterministic video synthesis engine. It runs CLI tools and interactive agents in real pseudo-terminals (PTY/tmux), injects human-like keystrokes, reacts to live screen events (such as workspace trust modals and permission requests), and streams pixel-perfect H.264 MP4 videos, animated GIFs, and Asciinema v2 (`.cast`) event streams directly into FFmpeg with zero intermediate disk I/O.

---

## 1. Quick Workflow for Agents

When tasked with recording a CLI or creating a video demonstration:

```
┌─────────────────────────┐     ┌──────────────────────────┐     ┌─────────────────────────┐
│ 1. Probe Target CLI     │ ──► │ 2. Scaffold Scenario     │ ──► │ 3. Record & Verify      │
│ `termreel probe <cli>`  │     │ `termreel generate <cli>`│     │ `termreel record <yml>` │
└─────────────────────────┘     └──────────────────────────┘     └─────────────────────────┘
```

### Step 1: Probe the Target CLI
Inspect the binary's version, usage, category, flags, and available subcommands:
```bash
termreel probe git
termreel probe agy
termreel probe gcloud
```

### Step 2: Generate a Scenario YAML Manifest
Automatically generate a tailored scenario YAML file:
```bash
termreel generate agy -o scenarios/agy_workshop.yaml --theme catppuccin-mocha
termreel generate git -o scenarios/git_demo.yaml --theme tokyo-night
```

### Step 3: Record the Video
Synthesize the video and optional Asciinema cast / poster thumbnail:
```bash
termreel record scenarios/agy_workshop.yaml -o output/agy_workshop.mp4 --cast output/agy_workshop.cast --poster output/agy_workshop_poster.png
```

---

## 2. CLI Reference & Subcommands

| Subcommand | Description | Example Usage |
| :--- | :--- | :--- |
| `termreel probe <cli>` | Discover CLI version, commands, and permissions | `termreel probe agy` |
| `termreel generate <cli>` | Scaffold a validated YAML scenario manifest | `termreel generate agy -o demo.yaml` |
| `termreel record <yml>` | Synthesize video from a declarative scenario YAML | `termreel record demo.yaml -o out.mp4` |
| `termreel exec "<cmd>"` | Quick one-shot command recording | `termreel exec "git status" -o status.mp4` |
| `termreel cast2video <.cast>` | Transcode Asciinema v2 `.cast` file to MP4/GIF | `termreel cast2video log.cast -o replay.mp4` |
| `termreel validate <yml>` | Validate YAML manifest schema before recording | `termreel validate demo.yaml` |
| `termreel themes` | List all 9 visual themes and color palettes | `termreel themes` |
| `termreel info` | Check PyCairo, Tmux, FFmpeg, and environment status | `termreel info` |

---

## 3. Scenario Manifest Specification (`scenario.yaml`)

TermReel manifests are declarative YAML documents defining the recording metadata, environment sandbox, permissions, reactive triggers, and step-by-step timeline.

```yaml
version: "1.0"

metadata:
  title: "Antigravity CLI Interactive Session"
  subtitle: "Module 1: Code Refactoring & Testing"
  output: "output/agy_demo.mp4"
  poster_output: "output/agy_demo_poster.png"
  cast_output: "output/agy_demo.cast"
  resolution: [1280, 720]       # 720p or 1080p [1920, 1080]
  fps: 30                       # 24, 30, or 60
  theme: "catppuccin-mocha"     # catppuccin-mocha, dracula, tokyo-night, nord, matrix, etc.
  font: "DejaVu Sans Mono"
  font_size: 14.5
  statusbar_left: "Antigravity CLI | UTF-8 | Real PTY"
  statusbar_right: "TermReel HD"

environment:
  create_temp_workspace: true   # Isolates execution in a temporary Git workspace
  temp_workspace_prefix: "termreel_agy_demo_"
  auto_trust: true              # Automatically trusts the workspace for agy
  setup_commands:
    - "git init"
    - "git config user.name 'Paul Datta'"
    - "git config user.email 'pkdatta2000@gmail.com'"
    - "echo 'def calculate_total(prices): return sum(prices)' > app.py"
    - "git add . && git commit -m 'Initial commit'"

permissions:
  auto_approve: true
  allow_commands:
    - "python3"
    - "git"
    - "pytest"
  allow_tools:
    - "run_command"
    - "write_to_file"
    - "read_file"
    - "grep_search"

triggers:
  # Auto-confirm workspace trust dialogs
  - on_match: "Do you trust the contents of this project\\?|Yes, I trust"
    action: "Enter"
    once: true

  # Intercept interactive permission requests and confirm cleanly
  - on_match: "Requesting permission for:|Do you want to proceed\\?|\\[y/N\\]"
    action:
      type: "send_key"
      value: "Enter"
      delay_before: 0.8         # Keeps dialog visible on screen for natural viewing
      delay_after: 0.3
    once: false
    cooldown: 1.5
    max_firings: 15

timeline:
  # 1. Opening Chapter Card
  - show_card:
      tag: "Module 1"
      title: "Interactive Codebase Refactoring"
      desc: "Demonstrating Antigravity CLI in pure interactive TUI mode"
      duration: 2.5

  # 2. Launch Interactive Tool
  - launch:
      command: "agy"
      wait_for_idle: true
      timeout: 25.0

  # 3. Simulate Human Typing
  - type:
      text: "Add calculate_tax(amount, rate) to app.py and write a test in main"
      speed: 0.035              # Typing speed in seconds per char (with realistic micro-jitter)
      jitter: 0.015
      send_key: "Enter"

  # 4. Wait for Model Completion Without Brittle Sleeps
  - wait_for_idle:
      timeout: 45.0
      reading_pause: 3.0        # Pause after generation completes so viewer can read

  # 5. Exit Interactive Tool
  - type:
      text: "/exit"
      speed: 0.04
      send_key: "Enter"
      pause: 1.5

  # 6. Run Shell Verification Command
  - run_shell:
      command: "git diff"
      speed: 0.03
      pause: 2.5

  # 7. Closing Chapter Card
  - show_card:
      tag: "Complete"
      title: "Session Finished"
      desc: "Pixel-perfect MP4 rendered via TermReel zero-disk pipeline"
      duration: 2.5
```

---

## 4. Timeline Action Reference

| Timeline Action | Description | Parameters |
| :--- | :--- | :--- |
| `show_card` | Renders a styled vector overlay card | `tag`, `title`, `desc`, `duration` |
| `launch` | Spawns a process in the PTY/tmux session | `command`, `wait_for_idle`, `timeout` |
| `type` | Types text with natural keystroke cadence | `text`, `speed`, `jitter`, `typos`, `send_key`, `pause` |
| `send_key` | Sends a control or navigation key | `key` (`Enter`, `Up`, `Down`, `C-c`, `Escape`, `Tab`) |
| `paste` | Bracketed paste for large multiline snippets | `text`, `pause` |
| `run_shell` | Types a command, presses Enter, and waits | `command`, `speed`, `pause` |
| `wait_for_idle` | Non-blocking wait until CLI returns to ready prompt | `timeout`, `idle_pattern`, `reading_pause` |
| `wait_for_text` | Waits until specific text appears on screen | `pattern`, `timeout` |
| `assert` | Asserts text presence (fails scenario if absent) | `pattern`, `timeout` |
| `select_choice` | Navigates down and selects a choice in a menu | `choice` (1-indexed int), `delay` |
| `set_statusbar` | Dynamically updates bottom status bar metadata | `left`, `right` |
| `pause` | Freezes the recording stream for a duration | `duration` (float seconds) |

---

## 5. Visual Themes & Customization

TermReel includes 9 built-in palettes optimized for vector contrast:
- `catppuccin-mocha` (Default dark pastel theme)
- `catppuccin-latte` (Clean light theme)
- `tokyo-night` (Modern deep blue/cyan theme)
- `dracula` (High-contrast purple/magenta theme)
- `nord` (Arctic blue pastel theme)
- `one-dark` (Atom One Dark theme)
- `monokai` (Vibrant yellow/green theme)
- `github-dark` (Minimalist GitHub dark theme)
- `matrix` (Phosphor green HUD theme)

To inspect theme colors in your terminal:
```bash
termreel themes
```

---

## 6. Best Practices for Interactive AI Agents (`agy`, `gemini`, etc.)

1. **Avoid YOLO / Print Mode**: Do NOT use `-p` or `--dangerously-skip-permissions` if you want to showcase the authentic TUI. TermReel's reactive triggers and lifecycle hooks handle permissions smoothly.
2. **Use `wait_for_idle`**: AI models take variable time to stream tokens. Never use arbitrary `pause: 15.0`. Always use `wait_for_idle: { timeout: 45.0, reading_pause: 3.0 }`.
3. **Use Temporary Workspaces**: Set `environment.create_temp_workspace: true` so tests, Git operations, and agent edits run in a safe, disposable directory without dirtying the host workspace.
4. **Redact Secrets**: Built-in regex filters mask API keys (`AIza...`), OAuth tokens (`ya29...`), GitHub PATs (`ghp_...`), and Bearer authorization headers automatically.
