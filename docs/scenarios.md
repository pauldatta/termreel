# Scenario Manifest Specification

TermReel scenario manifests are YAML documents defining recording parameters, environment lifecycles, permissions, triggers, and timeline actions.

---

## Schema Overview

```yaml
version: "1.0"

metadata:
  title: "Session Title"
  subtitle: "Subtitle description"
  output: "output/session.mp4"
  poster_output: "output/session_poster.png"
  cast_output: "output/session.cast"
  resolution: [1280, 720]
  fps: 30
  theme: "catppuccin-mocha"
  font: "DejaVu Sans Mono"
  font_size: 14.5
  statusbar_left: "CLI Title | UTF-8"
  statusbar_right: "TermReel HD"

environment:
  create_temp_workspace: true
  temp_workspace_prefix: "termreel_ws_"
  setup_commands:
    - "git init"
    - "echo 'hello' > app.py"

permissions:
  auto_approve: true
  allow_commands: ["python3", "git", "pytest"]
  allow_tools: ["run_command", "write_to_file", "read_file"]

triggers:
  - on_match: "Do you trust the contents of this project\\?"
    action: "Enter"
    once: true

timeline:
  - show_card:
      tag: "Module 1"
      title: "Interactive Workflow"
      desc: "Step-by-step walkthrough"
      duration: 2.0
  - launch:
      command: "bash"
  - run_shell:
      command: "git status"
      pause: 1.5
  - show_card:
      tag: "Done"
      title: "Completed"
      duration: 1.5
```

---

## Timeline Actions Reference

| Action | Description | Parameters |
| :--- | :--- | :--- |
| `show_card` | Overlay vector announcement card | `tag`, `title`, `desc`, `duration` |
| `launch` | Launch process in PTY/tmux | `command`, `wait_for_idle`, `timeout` |
| `type` | Natural keystroke cadence | `text`, `speed`, `jitter`, `send_key`, `pause` |
| `send_key` | Send control/arrow key | `key` (`Enter`, `Up`, `Down`, `C-c`, `Escape`, `Tab`) |
| `paste` | Bracketed paste multiline text | `text`, `pause` |
| `run_shell` | Types command, presses Enter, waits | `command`, `speed`, `pause` |
| `wait_for_idle` | Non-blocking wait for ready state | `timeout`, `reading_pause`, `idle_pattern` |
| `wait_for_text` | Wait for text pattern on screen | `pattern`, `timeout` |
| `assert` | Fail scenario if text pattern missing | `pattern`, `timeout` |
| `select_choice` | Navigates down and selects menu choice | `choice` (int), `delay` |
| `set_statusbar` | Dynamically update status bar | `left`, `right` |
| `pause` | Freeze stream for duration | `duration` (float seconds) |
