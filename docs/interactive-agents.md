# Recording Interactive AI Agents

TermReel provides purpose-built support for recording autonomous AI coding agents (such as Google Antigravity `agy`, Gemini CLI, Claude Code, Aider) in full, authentic interactive terminal mode without relying on print mode (`-p` / `--print`) or YOLO flags (`--dangerously-skip-permissions`).

---

## 1. Workspace Trust Dialogs

When an agent starts in a new workspace, it frequently displays a trust confirmation modal:
```
Do you trust the contents of this project? [Yes/No]
```

TermReel auto-confirms this modal cleanly via reactive screen triggers:
```yaml
triggers:
  - on_match: "Do you trust the contents of this project\\?|Yes, I trust|Trust project"
    action: "Enter"
    once: true
```

---

## 2. Human-In-The-Loop Permission Interception

When an interactive agent requests permission to execute tools (such as running shell commands or modifying files), it renders an interactive choice menu:

```
Requesting permission for:
  python3 app.py

Do you want to proceed?
> 1. Yes
  2. Yes, and always allow in this conversation for commands that start with 'python3 app.py'
  3. Yes, and always allow for commands that start with 'python3 app.py' (Persist to settings.json)
  4. No
```

### Two-Tier Handling Strategy:

1. **Declarative Permissions in YAML**: Pre-configures approved policies in `.agents/settings.json`:
   ```yaml
   permissions:
     auto_approve: true
     allow_commands: ["python3", "python3 app.py", "git", "pytest"]
     allow_tools: ["run_command", "write_to_file", "read_file", "grep_search"]
   ```

2. **Reactive UI Prompt Interception**:
   The screen reactor detects the modal dialog, holds the frame for a natural reading pause (e.g. 0.8s), and dispatches the selection (`Enter` or arrow navigation) in an asynchronous worker thread **without stalling the 30 FPS video frame rasterizer**:
   ```yaml
   triggers:
     - on_match: "Requesting permission for:|Do you want to proceed\\?|\\[y/N\\]"
       action:
         type: "send_key"
         value: "Enter"
         delay_before: 0.8
         delay_after: 0.3
       once: false
       cooldown: 1.5
       max_firings: 15
   ```

---

## 3. Dynamic Idle Detection (`wait_for_idle`)

AI agents take variable time to stream tokens and invoke tools. Rather than guessing with arbitrary sleeps, TermReel dynamically observes the screen buffer:
```yaml
- type:
    text: "Refactor calculator.py to support exponentiation"
    send_key: "Enter"

- wait_for_idle:
    timeout: 45.0
    reading_pause: 3.0   # Pause after generation finishes so the audience can read
```

---

## 4. Session Resumption & Multi-Stage Checkpointing

When recording agents across multi-stage workflows or resuming after previous steps:

```yaml
version: "1.0"
environment:
  resume: true                                # Automatically passes -c / --continue to agy
  conversation_id: "conv-abcdef-123456"       # Or resume a specific conversation ID
  workspace_path: "/tmp/termreel_ws_staging"  # Attach to existing workspace
  preserve_workspace: true                    # Preserve workspace for subsequent recordings

timeline:
  - launch:
      command: "agy"
      resume: true
  - type:
      text: "Now implement unit tests in test_calculator.py and run pytest"
      send_key: "Enter"
  - wait_for_idle:
      timeout: 45.0
      reading_pause: 2.0
```

