---
name: termreel
description: Automate, record, and synthesize high-fidelity terminal videos (MP4/WebM/GIF) and Asciinema (.cast) logs from interactive CLIs, TUIs, and AI coding agents (such as agy, git, gcloud, gh) using pseudo-terminals (PTY/tmux), natural typing simulation, reactive triggers, and vector rendering. Use when creating CLI walkthroughs, recording TUI demos, capturing interactive AI agent sessions, handling long-running multi-step workflows, or generating deterministic video proof artifacts.
---

# TermReel: Universal Terminal Recording & Video Synthesis Engine

TermReel (`termreel` / `reccli`) is a headless recording harness and deterministic video synthesis engine. It runs CLI tools and autonomous AI agents in real pseudo-terminals (PTY/tmux), injects human-like keystrokes, reacts to live screen events (such as workspace trust modals and permission requests), and streams pixel-perfect H.264 MP4 videos, animated GIFs, and Asciinema v2 (`.cast`) event streams directly into FFmpeg with zero intermediate disk I/O.

---

## 1. Quick Workflow for Agents

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

## 2. Long-Running & Multi-Step Agent Workflows

When recording agents that operate over long horizons (15 minutes to 4+ hours) with many sequential steps:

### A. Session Resumption & Conversation Checkpointing
Instead of re-running an entire workflow from step 0 when adding a new recording chapter, attach to existing conversations:

```yaml
version: "1.0"
environment:
  resume: true                                # Automatically passes -c / --continue to agy
  conversation_id: "conv-123456"              # Or resume a specific conversation ID
  workspace_path: "/tmp/termreel_ws_staging"  # Attach to existing workspace
  preserve_workspace: true                    # Do not delete workspace on teardown

timeline:
  - launch:
      command: "agy"
      resume: true
  - type:
      text: "Now run the full integration test suite and generate a summary report"
      send_key: "Enter"
  - wait_for_idle:
      timeout: 300.0
      reading_pause: 3.0
```

CLI flags for session resumption:
```bash
# Resume latest conversation in workspace
termreel record scenario.yaml --resume

# Resume specific conversation ID and attach to workspace
termreel record scenario.yaml --conversation <id> --workspace /path/to/ws
```

---

### B. Adaptive Watchdog & Agent Anti-Freeze Nudging
Agents sometimes hang on unhandled pagers (`less`), prompt confirmations, or stalled reasoning loops. Configure heartbeat timeouts to automatically nudge the agent:

1. **Level 1 (Soft Enter)**: Injects `\r` after inactivity timeout (e.g. 180s) to submit uncommitted readline buffers.
2. **Level 2 (Pager Escape)**: Injects `q` followed by `Escape` to break out of accidental `git log` or `less` traps.
3. **Level 3 (AI Continuation)**: Injects `"Please continue with the remaining task steps"` to re-engage stalled reasoning turns.

---

### C. Telemetry-First Strategy for Multi-Hour Sessions
For jobs running 1+ hours, avoid encoding hundreds of gigabytes of static thinking frames:

1. **Capture Lightweight Telemetry First**:
   ```bash
   termreel record scenario.yaml --cast output/long_session.cast --quiet
   ```
2. **Synthesize Video Post-Hoc with Hyperlapse**:
   ```bash
   # Re-render with 4x-10x playback speed and condensed idle frames
   termreel cast2video output/long_session.cast -o output/fast_summary.mp4 --speed 4.0 --theme tokyo-night
   ```

---

## 3. Best Practices from Real-World Experiments

### 1. Avoid Print / YOLO Modes for Demonstrations
- **Don't** use `-p` or `--dangerously-skip-permissions` when recording UI demos.
- **Do** run in pure interactive TUI mode with TermReel's declarative permissions and reactive prompt interceptors. This produces authentic videos showing the real CLI badges, progress indicators, and interactive choices without stalling.

### 2. Use Dynamic Idle Detection Instead of Static Sleep
- **Don't** hardcode `sleep: 30.0` (it causes either premature cutoffs or wasted dead air).
- **Do** use `wait_for_idle: { timeout: 45.0, reading_pause: 2.5 }`. TermReel observes the terminal screen and hook events, waking up immediately when the agent finishes, and adds a natural reading pause for human viewers.

### 3. Structure Multi-Step Demos with Chapter Cards
Overlay vector announcement cards (`show_card`) before each major phase:
```yaml
timeline:
  - show_card:
      tag: "Phase 1 / 3"
      title: "Discovery & Analysis"
      desc: "Agent scans codebase architecture and identifies target functions"
      duration: 2.5
```

### 4. Provide Visual Context with Status Bars
Set informative status bar metadata so viewers know the active tool, branch, and encoding resolution:
```yaml
metadata:
  statusbar_left: "Google Antigravity | Refactoring app.py"
  statusbar_right: "TermReel HD (1280x720@30fps)"
```
Dynamically update it during execution with `set_statusbar`:
```yaml
- set_statusbar:
    left: "Running Pytest Suite..."
    right: "Phase 3/3"
```

### 5. Always Redact Secrets and Tokens
Built-in redactors automatically mask Google API keys, OAuth tokens (`ya29...`), and GitHub PATs (`ghp_...`). Add custom domain or IP regexes under `redactions`:
```yaml
redactions:
  - "internal-host-[0-9]+\\.example\\.corp"
  - "SECRET_KEY=[a-zA-Z0-9]+"
```

---

## 4. Full Scenario Manifest Reference

```yaml
version: "1.0"

metadata:
  title: "Interactive Agent Workflow"
  subtitle: "Full-Stack Refactoring & Automated Verification"
  output: "output/agent_session.mp4"
  cast_output: "output/agent_session.cast"
  poster_output: "output/agent_session_poster.png"
  resolution: [1280, 720]
  fps: 30
  theme: "catppuccin-mocha"
  font: "DejaVu Sans Mono"
  font_size: 14.5
  statusbar_left: "Antigravity CLI | UTF-8"
  statusbar_right: "TermReel HD"

environment:
  create_temp_workspace: true
  temp_workspace_prefix: "termreel_ws_"
  auto_trust: true
  preserve_workspace: false
  setup_commands:
    - "git init"
    - "git config user.name 'Paul Datta'"
    - "git config user.email 'pkdatta2000@gmail.com'"
    - "echo 'def run(): pass' > main.py"
    - "git add . && git commit -m 'Initial commit'"

permissions:
  auto_approve: true
  allow_commands: ["python3", "pytest", "git"]
  allow_tools: ["run_command", "write_to_file", "read_file"]

triggers:
  - on_match: "Do you trust the contents of this project\\?|Yes, I trust"
    action: "Enter"
    once: true
  - on_match: "Requesting permission for:|Do you want to proceed\\?|\\[y/N\\]"
    action:
      type: "send_key"
      value: "Enter"
      delay_before: 0.8
      delay_after: 0.3
    once: false
    cooldown: 1.5
    max_firings: 15

timeline:
  - show_card:
      tag: "Demo"
      title: "Interactive Agent Session"
      duration: 2.0

  - launch:
      command: "agy"
      wait_for_idle: true
      timeout: 20.0

  - type:
      text: "Implement a robust caching layer in cache.py and write unit tests"
      speed: 0.035
      send_key: "Enter"

  - wait_for_idle:
      timeout: 60.0
      reading_pause: 3.0

  - type:
      text: "/exit"
      send_key: "Enter"
      pause: 1.0

  - run_shell:
      command: "pytest -v"
      pause: 2.0

  - show_card:
      tag: "Complete"
      title: "Verification Succeeded"
      duration: 2.0
```

---

## 5. Parallel Test Execution

Run the complete test suite concurrently:
```bash
# Run all tests across 8 async worker threads
termreel test -w 8
```
