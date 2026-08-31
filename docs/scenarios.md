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
  resume: false                 # Auto-resume latest conversation in workspace
  conversation_id: null         # Or specify exact conversation ID
  preserve_workspace: false     # Keep workspace for subsequent scenario runs
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
| `launch` | Launch process in PTY/tmux | `command`, `wait_for_idle`, `wait_for_prompt`, `prompt_pattern`, `timeout` |
| `type` | Natural keystroke cadence | `text`, `speed`, `jitter`, `send_key`, `pause`, `collapse_newlines`, `multiline` |
| `send_key` | Send control/arrow key (string or dict) | String (`"Escape"`) or Dict (`{key: "Escape", delay_before: 0.5, pause_after: 1.0}`) |
| `inspect_modal` | Open, inspect, and dismiss TUI popup | `open_command`, `open_key`, `wait_for_render`, `display_duration`, `dismiss_key`, `pause_after` |
| `paste` | Bracketed paste multiline text | `text`, `pause` |
| `run_shell` | Types command, presses Enter, waits | `command`, `speed`, `pause` |
| `wait_for_idle` | Non-blocking wait for ready state | `timeout`, `reading_pause`, `idle_pattern`, `wait_for_prompt`, `prompt_pattern` |
| `wait_for_text` | Wait for text pattern on screen | `pattern`, `timeout` |
| `assert` | Fail scenario if text pattern missing | `pattern`, `timeout` |
| `select_choice` | Navigates down and selects menu choice | `choice` (int), `delay` |
| `set_statusbar` | Dynamically update status bar | `left`, `right` |
| `pause` | Freeze stream for duration | `duration` (float seconds) |

---

## Action Deep Dives

### 1. Polymorphic `send_key`
Supports both simple string format and structured timing dictionaries:

```yaml
# Simple string format
- send_key: "Escape"

# Structured format with timing controls
- send_key:
    key: "Escape"
    delay_before: 0.5   # Pause before pressing key
    pause_after: 1.0    # Pause after pressing key
```

### 2. Newline Collapsing in `type.text`
By default (`collapse_newlines: true`), soft line wraps inserted by YAML formatters are collapsed into single spaces to avoid premature command submission:

```yaml
# Soft line wraps are safely collapsed into a single space:
- type:
    text: >
      agy "Analyze the repository architecture and create a comprehensive
      system diagram"
    send_key: "Enter"

# If intentional multiline text is needed (e.g. heredocs):
- type:
    text: "line 1\nline 2"
    multiline: true
```

### 3. Shell Prompt Synchronization (`wait_for_prompt`)
Prevents typing collisions when launching shells:

```yaml
- launch:
    command: "bash"
    wait_for_prompt: true
    prompt_pattern: "[$#>]\\s*$"
```

### 4. TUI Modal Inspection (`inspect_modal`)
Declaratively opens a TUI popup, waits for its content, pauses for reading, and dismisses it:

```yaml
- inspect_modal:
    open_command: "/context"
    wait_for_render: "Token Usage"
    display_duration: 3.0
    dismiss_key: "Escape"
    pause_after: 1.0
```

