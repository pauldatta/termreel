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
  auto_approve_dialogs: false   # Opt-in: answer permission / [y/N] dialogs (alias: auto_approve)
  setup_commands:
    - "git init"
    - "echo 'hello' > app.py"


# Written to .agents/settings.json for agy. allow_commands/allow_tools are aliases of
# allowed_commands/allowed_tools. There is no permissions.auto_approve switch.
permissions:
  allow_commands: ["python3", "git", "pytest"]
  allow_tools: ["run_command", "write_to_file", "read_file"]

# Optional screen masking & value substitution (merges with ~/.termreel/config.yaml)
mask:
  values:
    - match: "secret-project-prod-99"
      replace: "acme-demo-42"
  anchors:
    - after: "Bearer "
      replace: "eyJhbGciOi..."
  patterns:
    - pattern: "AIza[0-9A-Za-z\\-_]{35}"
      replace: "AIzaSyFakeKeyDemo0000000000000000"

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
| `edit_file` / `edit` | Hermetic Vim file editing with clean buffer clearing and exit | `path`, `content`, `editor`, `action`, `syntax`, `pause_after` |
| `speedup` | Time dilation / timelapse with synced `.cast` clock | `factor`, `indicator`, `min_duration` (or inline on `run_shell`) |
| `assert` / `assert_output` | Semantic assertion gate with scrollback inspection | `contains`, `not_contains`, `pattern`, `scope`, `on_fail` |
| `split_pane` | Split tmux window horizontally or vertically | `direction` (`horizontal` / `vertical`), `size_percent`, `command` |
| `select_pane` | Switch active tmux pane focus | `pane_index` (int) |
| `close_pane` | Terminate a tmux pane | `pane_index` (int) |
| `inspect_modal` | Open, inspect, and dismiss TUI popup | `open_command`, `open_key`, `wait_for_render`, `display_duration`, `dismiss_key`, `pause_after` |
| `paste` | Bracketed paste multiline text | `text`, `pause` |
| `run_shell` | Types command, presses Enter, waits | `command`, `speed`, `pause`, `speedup`, `assert_output` |
| `wait_for_idle` | Non-blocking wait for ready state | `timeout`, `reading_pause`, `idle_pattern`, `wait_for_prompt`, `prompt_pattern` |
| `wait_for_text` | Wait for text pattern on screen | `pattern`, `timeout` |
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

### 5. Hermetic Code Editing (`edit_file` / `edit`)
Spawns Vim in hermetic mode (`vim -u NONE -i NONE -n`), clears existing content cleanly without `E16` range errors on new files, bracketed-pastes the code with syntax highlighting active, and saves/exits cleanly:

```yaml
- edit_file:
    path: "app/agent.py"
    action: "replace"
    content: |
      from google.adk import Agent

      root_agent = Agent(
          name="AuthFixer",
          description="Audits authentication tokens"
      )
    pause_after: 1.0
```

### 6. Dynamic Video Speedup (`speedup` / `timelapse`)
Dilation via producer-side frame decimation compresses long commands (evals, model grading, builds) into high-energy timelapse sequences while keeping `.cast` clocks in exact sync:

```yaml
# Inline step speedup (automatically restored when step finishes)
- run_shell: "npm run build"
  speedup:
    factor: 8.0
    indicator: "⏩ 8x"

# Or standalone timeline speedup
- speedup: 4.0
- run_shell: "pytest -v --run-slow"
- speedup: 1.0
```

### 7. Semantic Assertion Gates (`assert_output` / `assert`)
Immediately verifies terminal output and fails the scenario if unexpected errors occur, preventing wasted render cycles:

```yaml
# Inline assertion on run_shell
- run_shell: "python3 app/agent.py"
  assert_output:
    contains: "Token generated successfully"
    not_contains: "Error:"
    scope: "all"       # Inspect full scrollback buffer
    on_fail: "abort"   # 'abort' or 'warn'

# Standalone assertion step
- assert:
    pattern: "Build successful"
    timeout: 5.0
```

### 8. Multi-Pane Tmux Layouts (`split_pane`, `select_pane`, `close_pane`)
Demonstrates client-server or agent-sidecar workflows side by side under `--backend tmux`:

```yaml
# Split terminal horizontally (left: agent, right: monitor)
- split_pane:
    direction: "horizontal"
    size_percent: 40
    command: "tail -f server.log"

- select_pane:
    pane_index: 0

- run_shell: "curl http://localhost:8080/health"
  pause: 2.0

- close_pane:
    pane_index: 1
```

