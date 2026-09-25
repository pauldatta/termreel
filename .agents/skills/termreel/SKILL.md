---
name: termreel
description: Automate, record, and synthesize high-fidelity terminal videos (MP4/WebM/GIF) and Asciinema (.cast) logs from interactive CLIs, TUIs, and AI coding agents (such as agy, git, gcloud, gh) using pseudo-terminals (PTY/tmux), natural typing simulation, reactive triggers, and vector rendering. Also supports hand-driven live capture (`termreel live`) where a human types and TermReel records, with hotkey pause/resume that cuts paused time out of the video. Use when creating CLI walkthroughs, recording TUI demos, capturing interactive AI agent sessions, handling long-running multi-step workflows, or generating deterministic video proof artifacts.
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

### 4. Authenticity & Video Quality Guidelines (Student-Ready Demos)
When authoring video scenarios for tutorials, walkthroughs, or evaluations:
- **No pre-staged file copies (`cp -r /tmp/staged_*/* .`)**: Fabrication hides the work the exercise teaches. Files must be created visibly on camera.
- **No `cat << 'EOF'` heredocs**: Avoid raw bash heredocs in the shell timeline or in agent prompts. It makes AI agent pairing look like an old-fashioned shell tutorial. Use `edit_file` to write code cleanly on camera, or prompt the coding agent in natural language.
- **No `--help` stand-ins**: Showing `cli --help` instead of executing the actual command skips the lesson. Run the real command and let viewers see the authentic tool output.
- **The "Successful Run" Brief**: A video is only valid if a student could pause on any frame, type what is on screen, and land where the video lands.

### 5. Provide Visual Context with Status Bars
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

### 6. Always Redact Secrets and Tokens

Built-in redactors mask Google OAuth tokens (`ya29.`) and API keys (`AIza`), GitHub tokens (`ghp_`, `github_pat_`, `gho_`/`ghu_`/`ghs_`/`ghr_`), Anthropic (`sk-ant-`) and OpenAI (`sk-proj-`, `sk-svcacct-`, `sk-admin-`, `sk-`) keys, Stripe `sk_`/`rk_` live/test keys, Slack `xox?-` tokens, GitLab `glpat-`, AWS `AKIA` key IDs, JWTs, `Bearer` headers, and `-----BEGIN ... PRIVATE KEY-----` lines. A token that wraps across screen rows is still matched.

Masking fails closed. An invalid regex in `mask.patterns` or `redactions`, or an anchor whose `before`/`after` cannot be compiled, is rejected when the manifest is validated (`ScenarioValidationError`). A broken `~/.termreel/config.yaml` raises `MaskConfigError`. Recording never starts with a mask rule silently dropped.

For environment-specific secrets, project identifiers, and hostnames, use **Screen Masking and Value Substitution** under `mask:` or `redactions:`:

#### 1. Realistic Value Substitution
Mask sensitive values by substituting a realistic fake string rather than blacking them out with `•••`, so recorded code and commands remain copyable and natural:

```yaml
mask:
  values:
    elevate-security-2026: acme-demo-42
    internal-prod-cluster.corp: cluster-demo-01
```

Or explicit match/replace objects:

```yaml
mask:
  values:
    - match: "elevate-security-2026"
      replace: "acme-demo-42"
```

#### 2. Contextual Landmark Anchors
When values are dynamic or positional, anchor on surrounding text:

```yaml
mask:
  anchors:
    - after: "project = "
      replace: "acme-demo-42"
    - after: "export SECRET_KEY="
      span: "rest_of_line"
      replace: "dummy-key-xyz"
    - after: 'client_id: "'
      before: '"'
      replace: "fake-client-id"
```

#### 3. Custom Regex Patterns
```yaml
mask:
  patterns:
    - pattern: "ghp_[a-zA-Z0-9]{30,45}"
      replace: "ghp_mocktoken12345678901234567890"
    - "internal-host-[0-9]+\\.example\\.corp"  # default bullet mask
```

#### 4. Global Configuration (`~/.termreel/config.yaml`)
Define machine-wide mask rules once in `~/.termreel/config.yaml`. TermReel automatically merges global rules into all scenario and live recordings:

```yaml
# ~/.termreel/config.yaml
mask:
  values:
    my-company-gcp-prod-2026: acme-demo-42
    alice-internal-ldap: demo-user
  anchors:
    - after: "project = "
      replace: "acme-demo-42"
```

Scenario-specific rules override global rules on key collision.

> [!IMPORTANT]
> **Identifier-shaped secrets cannot be caught by pattern matching.** GCP project IDs, usernames, hostnames, and internal service names are just kebab-case words — any regex broad enough to match `my-gcp-project-2026` will also destroy `us-central1-a` and `docs-site-config`. You **must** list these under `values` or `anchors`.

> [!NOTE]
> **`.cast` exports are redacted with the same rules as the video.** The cast writer redacts the output stream, not each read on its own. A secret split across two PTY reads or broken up by colour codes (SGR) is still caught. To do that it holds back a possible partial match at the end of a chunk, for at most 0.25 s of idle time. One limit remains: text that trickles out slower than that, such as a token typed one character at a time with pauses over 0.25 s, can land in the cast in pieces the pattern no longer matches. Run `termreel mask --verify` on the cast before publishing.
>
> Telemetry follows the same rules. The session directory is created `0700` and its files `0600`. `GET_SCREEN`, `GET_RAW` and `screen.ansi` return masked text.

#### 5. Verification & Typo Protection
Run mask verification before publishing. Typo protection warns when a configured rule has 0 matches, and `--strict` fails CI:

```bash
termreel mask --verify output/session.cast --strict
termreel mask --test "gcloud config set project elevate-security-2026"
termreel mask --list
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
  auto_approve_dialogs: true      # opt-in (default false): built-in permission + [y/N] handlers

# Written to .agents/settings.json for agy. allow_commands/allow_tools are aliases of
# allowed_commands/allowed_tools. There is no permissions.auto_approve switch; it is ignored.
permissions:
  allow_commands: ["python3", "pytest", "git"]
  allow_tools: ["run_command", "write_to_file", "read_file"]

triggers:
  - on_match: "Do you trust the contents of this project\\?|Yes, I trust"
    action: "Enter"
    once: true
  - on_match: "Requesting permission for:|Do you want to proceed\\?"
    action:
      type: "send_key"
      value: "Enter"
      delay_before: 0.8
      delay_after: 0.3
    once: false
    edge: presence                # one Enter per dialog (see "Trigger edge" below)
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

  # Authentic on-screen code editing without raw vim choreography fragility
  - edit_file:
      path: "cache.py"
      action: "replace"
      content: |
        class Cache:
            def __init__(self):
                self._data = {}
      pause_after: 1.0

  # Dilation via frame decimation to compress long test runs into fast-forward sequences
  - run_shell:
      command: "pytest -v"
      pause: 1.0
      speedup:
        factor: 8.0
        indicator: "⏩ 8x"
      assert_output:
        contains: "passed"
        not_contains: "FAIL"
        scope: "all"

  - show_card:
      tag: "Complete"
      title: "Verification Succeeded"
      duration: 2.0
```

**Manifest rules worth knowing:**
- **Strict keys.** When a manifest is loaded from YAML (`termreel run`, `termreel validate`), an unknown key in `metadata`, `environment`, a trigger or a step fails with a suggestion (`Unknown key 'auto_aprove_dialogs' in 'environment'. Did you mean 'auto_approve_dialogs'?`), instead of being ignored.
- **Keys and regexes are checked up front.** A bad key name in `send_key`, or an invalid regex in a trigger or mask rule, fails `termreel validate` and aborts before anything is recorded.
- **Dialog answering is opt-in.** `environment.auto_approve_dialogs` (alias `auto_approve`) defaults to `false`. `agy_auto_approve` controls only the agy PreToolUse hook policy.
- **Trigger `edge`.**
  - `presence`: fire once when the pattern appears. Re-arm when it leaves the screen, or when a new match appears on a lower line than the answered one after the answer was sent (the next dialog printed under an answered one in scrolling output). A dialog that stays up because the key did not dismiss it is not answered again. Not handled: a dialog replaced in place by a different one at the same rows with no frame in between where the pattern is gone.
  - `line`: fire once per prompt still waiting for input. The match must be on the cursor row or the last non-blank line, with only whitespace/punctuation after it. An answered prompt that stays visible is not answered again, and a new identical prompt on a later line (or after the screen scrolls) is.
  - Omitted, `none` or `level`: the trigger re-fires every `cooldown` seconds while the text is visible.

  Everything any trigger typed is printed at the end of the run and returned in `ScenarioReport.injections`.
- **Outputs are atomic.** Video is written to `<name>.partial.<ext>` (for example `demo.partial.mp4`) and renamed on success. Odd canvas sizes are padded evenly to the even size H.264 needs, not cropped.

---

## 5. Parallel Batch Rendering & Multimodal Auditing

### Batch Rendering (`termreel batch`)
Render dozens of scenarios concurrently with automatic poster extraction and structured reporting:
```bash
# Render all scenario files using 4 concurrent workers
termreel batch scenarios/*.yaml \
  --concurrency 4 \
  --output-dir output/videos/ \
  --generate-posters \
  --poster-time 0.5 \
  --report BATCH_REPORT.json
```

### Multimodal Video Verification (`termreel audit`)
Verify recorded videos against specifications using `gemini-3.1-pro-preview` with an automated 100-point rubric. Supports both Gemini Developer API (`GEMINI_API_KEY`) and Google Cloud Vertex AI via Ambient Application Default Credentials (ADC):
```bash
# Audit video output via Developer API
termreel audit output/service_demo.mp4 \
  --spec scenarios/service_demo.yaml \
  --model gemini-3.1-pro-preview \
  --threshold 80 \
  --chunk-duration 300.0 \
  --report AUDIT_REPORT.md

# Audit video output natively via Vertex AI & Ambient ADC
termreel audit output/service_demo.mp4 \
  --spec scenarios/service_demo.yaml \
  --vertexai \
  --project elevate-security-2026 \
  --location global
```

> [!TIP]
> **Ambient ADC on Google Cloud & Cloudtop**:
> On corporate Google Cloudtops or GCP environments where credentials come from `gcert` or Application Default Credentials (ADC), pass `--vertexai --project <id> --location <region>` (or set `GOOGLE_GENAI_USE_VERTEXAI=1`). You do **not** need to create or export an external `GEMINI_API_KEY`. Video frames and keyframes are streamed directly to Vertex AI.

#### Automated Windowed Chunking for Long Videos (1M Context Limit)
Gemini video input samples at ~1 FPS (~258 tokens per second). Videos longer than 30–45 minutes quickly approach or exceed the 1M token context limit when combined with large PRDs and manifests.

TermReel solves this with **automated windowed map-reduce auditing**:
- When duration exceeds `--chunk-duration` (default: 300s / 5 minutes), TermReel instantaneously slices the video into lossless segments using FFmpeg stream copy (`-c copy`).
- Each segment is independently verified in context without context dilution or overflow.
- Scores and findings are rolled up into a unified 100-point scorecard with a `Windowed Segment Breakdown` table and globally re-mapped timestamps.

TermReel grades:
1. **Visual Stability**: Resolution, framerate, and container health.
2. **TUI Formatting**: Color contrast, dark mode window chrome, aspect ratio.
3. **Execution Completion**: Progression of commands and resting prompt state.
4. **Error-Free Output**: Absence of uncaught stack traces or abrupt exits.


---

---

## 6. Real-Time Observation & Peek (`termreel peek`)

When TermReel runs background renders or long-running agent workflows, use `termreel peek` to non-invasively inspect the live terminal without stopping or slowing down the recording:

```bash
# 1. Take an instantaneous snapshot of the latest active recording session
termreel peek

# 2. Target a specific session by ID prefix or process PID
termreel peek 12345
termreel peek tr_a1b2c3d4

# 3. Follow the live terminal screen at 10 FPS (press 'q' or 'Ctrl+C' to exit)
termreel peek -f

# 4. Follow with a custom refresh interval (e.g. 20 FPS / 50ms)
termreel peek -f --interval 0.05

# 5. List all active and recent recording sessions
termreel peek --list

# 6. Capture a high-resolution PNG screenshot of the current live vector frame
termreel peek --image /tmp/live_screen.png

# 7. Launch a local web dashboard for browser observation
termreel peek --web 8989
# Access via http://pauldatta.c.googlers.com:8989 or http://localhost:8989

# 8. Output raw plain text without HUD borders (for pipes or automated agent checks)
termreel peek --raw
```

---

## 7. Hand-Driven Capture (`termreel live`)

`termreel record` replays a scripted manifest. `termreel live` inverts that: a human drives the terminal and TermReel encodes what happens. Reach for it when the session cannot be scripted, such as exploratory debugging, an unpredictable TUI, or a walkthrough the operator wants to narrate by hand.

```bash
# Record the default shell to output/live.mp4
termreel live

# Record a specific command at 1080p, with an Asciinema log
termreel live "agy" -o output/session.mp4 --resolution 1920x1080 --cast output/session.cast
```

### Hotkeys

Every control is a prefix keystroke followed by one key, the same shape as tmux. The default prefix is `C-t`, shown in banners as `^T`.

| Keys | Effect |
| :--- | :--- |
| `^T p` or `^T Space` | Pause / resume recording |
| `^T r` | Resume |
| `^T m` | Mark the current position (reported when the run finishes) |
| `^T q` | Stop and finalise the video |
| `^T ?` | Print the hotkey reminder |
| `^T ^T` | Send one literal prefix byte to the shell |

Everything else is forwarded to the child verbatim. Bracketed pastes suppress the state machine entirely, so pasted text containing `^T` cannot fire a control.

### Pausing Cuts Time Out of the Video

Pausing stops the **recording**, not the process. The child keeps running and still accepts input, but TermReel writes zero frames, so the paused interval never reaches the video. Use it to run `gcloud auth login`, fix a typo, or wait out a slow build without dead air in the output.

On resume the last pre-pause frame crossfades into the first post-resume frame over 0.25s. That fade is the only cut indicator, because a `PAUSED` caption is structurally impossible in a file that contains no paused frames. Use `--hard-cuts` for a straight jump or `--crossfade 0.5` for a slower one.

Output duration equals recorded time, not wall time.

### Rebinding the Prefix

```
--prefix  ->  $TERMREEL_PREFIX  ->  ~/.termreel/config.yaml  ->  C-t
```

```yaml
# ~/.termreel/config.yaml
live:
  prefix: "C-a"
```

Accepted spellings: `C-t`, `ctrl+t`, `ctrl-t`, `^T`, `C-]`, `0x14`.

`C-c`, `C-d`, `C-s`, `C-q`, `C-z`, `C-\`, `Escape` (`C-[`), Enter and newline are refused with an explanation. The recorded shell needs those, and `C-s` freezes the terminal with no way back.

> [!IMPORTANT]
> On macOS the usable key pool is small. Command never reaches the terminal, Option produces accented characters unless "Use Option as Meta" is enabled, and the shell already owns most of Control. Single-letter Ctrl combinations are the only class that transmits identically across every Mac keyboard layout, which is why the default is one and why rebinding is first-class.

Probe the operator's actual keyboard before recommending a binding:

```bash
termreel live --keys
#   -> 0x14         C-t          OK  safe. Suggested config: prefix: "C-t"
#   -> 0x01         C-a          !!  readline beginning-of-line; also the tmux prefix on many setups
```

### Driving a Live Session from Another Terminal

A live run registers with the telemetry socket like any other session, so `termreel peek` and `termreel peek -f` work against it. The socket also accepts `PAUSE`, `RESUME`, `TOGGLE_PAUSE` and `STOP`. That is the escape hatch when a full-screen TUI has already claimed the prefix key.

### Constraints

- Requires an interactive tty on stdin. It exits with an error under a pipe or in CI.
- The canvas is locked at startup, but the recorded shell follows the window: a mid-session resize is passed through to the child, clamped to the locked grid. Shrinking letterboxes; growing past it warns once and the extra area is not captured.
- Prefer `.mp4`. A `.gif` target is encoded in two passes: frames stream losslessly to a temporary file, and the palette and GIF are built when the run stops, so the GIF appears only at the end (and that step takes a while for long runs). GIFs are capped at 15 fps and 960 px wide. Every output is written to `<name>.partial.<ext>` and renamed only after ffmpeg succeeds, so a failed encode never leaves a truncated file under the real name.
- Above roughly 1920x1080 the renderer warns: frame production alone eats a large share of the frame budget and the encoder may push back. Pick a smaller grid with `--cols` / `--rows`.
- `^T q` is the ordinary way out, since raw mode leaves TermReel without its own Ctrl-C. `SIGTERM` / `SIGHUP` also stop it cleanly — video finalised, `.cast` flushed, recorded shell terminated, terminal restored. A second signal force-exits. However the recording stops (`^T q`, a signal, or the shell exiting), TermReel then kills the other processes in the recorded shell's session (background jobs such as `sleep 999 &`): `SIGTERM`, then `SIGKILL` after 1 s. Processes that called `setsid` (daemons, `nohup setsid ...`) left that session and keep running.
- While paused, output still reaches the screen but is left out of the `.cast` too. On resume the cast gets a full redraw of the current screen, so replaying the cast matches what the video shows.

---

## 8. Field Ergonomics & Timeline Primitives

### 1. Hermetic Vim Editor (`edit_file` / `edit`)
Shows code being written authentically on screen without the fragility of manual keystroke macros or indentation staircasing.
```yaml
- edit_file:
    path: "app/agent.py"
    action: "replace"           # "replace" (default), "append", "insert"
    content: |
      from google.adk import Agent
      root_agent = Agent(name="assistant")
    pause_after: 1.0
```
- Spawns `vim -u NONE -i NONE -n -c "set noswapfile nocompatible syntax=on autoindent paste"`.
- Automatically checks file size; skips `:%d` on empty or newly created files, preventing Vim's `E16: Invalid range` error.
- Transmits content via bracketed paste (`\x1b[200~...\x1b[201~`) to preserve exact formatting, then cleanly saves and exits with `:wq`.

### 2. Dynamic Video Speedup & Timelapse (`speedup` / `timelapse`)
Compresses long-running commands (package builds, evaluations, multi-model grading) into fast-forward sequences using producer-side frame decimation. Keystrokes, child processes, and `.cast` timestamps remain synchronized to video elapsed time.
```yaml
# Inline on run_shell (speedup applies during execution and automatically restores to 1.0x after)
- run_shell: "agents-cli eval grade"
  speedup:
    factor: 8.0                 # 8x playback speedup
    indicator: "⏩ 8x"           # Optional corner status pill badge during fast-forward

# Standalone timeline control
- speedup:
    factor: 4.0
    indicator: "⏩ 4x"
# ... subsequent steps execute at 4x speed ...
- speedup: 1.0                  # Reset back to real-time
```

### 3. Semantic Assertion Gates (`assert_output` / `assert` / `assert_screen`)
Catches broken states (Python exceptions, syntax errors, failed test suites) immediately during recording rather than discovering failures post-render.
```yaml
# Inline on run_shell (defaults to scope: "all" to inspect scrollback)
- run_shell: "pytest -v"
  assert_output:
    contains: "10 passed"       # String or list of strings that must be present
    not_contains: "FAIL"        # String or list of strings that must NOT be present
    scope: "all"                # "all" (inspects full scrollback) or "visible"
    timeout: 5.0
    on_fail: "abort"            # "abort" (raises error immediately) or "warn"

# Standalone assertion step
- assert:
    contains: "Ready for input"
    scope: "visible"
    timeout: 5.0
```

### 4. Multi-Pane Tmux Layouts (`split_pane`, `select_pane`, `close_pane`)
Enables split-screen layouts (e.g. agent pairing on the left, live logs or sidecar streaming on the right). Requires `--backend tmux`.
The tmux backend runs its own private tmux server (`tmux -L <socket>` with a minimal generated config). Your `~/.tmux.conf` and an enclosing `$TMUX` do not affect it, and stopping a recording kills only that private server. Every visible pane goes into the video, composited with pane borders, and the cursor is drawn where tmux reports it. Pane numbers in `select_pane` / `close_pane` are tmux pane indexes (`#{pane_index}`, 0 = the original pane).
```yaml
- launch: "bash"
- run_shell: "echo 'Main console ready'"

# Split pane horizontally (side-by-side)
- split_pane:
    direction: "horizontal"     # "horizontal" (-h) or "vertical" (-v)
    percent: 40                 # Width percentage
    command: "tail -f server.log"

# Switch focus between panes
- select_pane: 1
- run_shell: "curl -s http://localhost:8000"

# Return focus to primary pane
- select_pane: 0

# Close auxiliary pane when done
- close_pane: 1
```

### 5. Structured `send_key`
Use dictionaries when you need precise pauses around control keys:
```yaml
- send_key:
    key: "Escape"
    delay_before: 0.5
    pause_after: 1.0
- send_keys: ["C-r", "M-f", "Enter"]  # presses each key in order
```
Both backends share one key vocabulary (case-insensitive):
- Named keys: `Enter`, `Escape`, `Tab`, `Backspace`, `Space`, arrows, `Home`, `End`, `PageUp`/`PageDown`, `Insert`, `Delete`, `F1`–`F12`.
- Modifiers: `C-r` / `ctrl+r` / `^R`, `M-f` / `alt+f`, `S-Tab` / `shift+tab` / `BTab`, `ctrl+left`, and Ctrl punctuation such as `C-]`.
- A raw byte (`0x14`), any single printable character, or `none`.

An unknown key or a combination with no standard encoding (such as `C-Enter`) raises `KeySpecError`. Manifests are checked when validated, so the key is never typed into the session as literal text. With `--backend tmux`, tmux sends `Home`/`End` as `\e[1~`/`\e[4~`, not the `\e[H`/`\e[F` the PTY backend uses.

### 6. TUI Modal Inspection (`inspect_modal`)
Cleanly demo popup dialogs (`/context`, `/stats`, `/diff`, `/agents`):
```yaml
- inspect_modal:
    open_command: "/context"
    wait_for_render: "Token Usage"
    display_duration: 3.0
    dismiss_key: "Escape"
    pause_after: 1.0
```

### 7. Shell Prompt Synchronization (`wait_for_prompt`)
Prevent keystroke collisions with shell initialization prompts:
```yaml
- launch:
    command: "bash"
    wait_for_prompt: true
    prompt_pattern: '([$#>]\s*$|%\s*$)'
```

### 8. Soft Newline Collapsing
TermReel automatically collapses YAML multiline string wraps into single spaces so accidental line breaks don't submit premature commands. Use `multiline: true` only when literal line breaks are intentional.

---

## 9. Parallel Test Execution & Environment

TermReel includes an accelerated parallel test runner auto-scaling up to 16 workers:
```bash
# 1. Fast mode: 410 tests in about 60 s with 8 workers (skips tests whose names
#    contain "slow" or "pure_interactive", including the 65 s agy E2E test)
python3 -m termreel.cli test -f

# 2. Filter mode: run specific test cases matching a pattern
python3 -m termreel.cli test -k test_edit_file

# 3. Full suite: all 433 tests, about 85 s with 8 workers
python3 -m termreel.cli test -w 8
```
Many tests compare TermReel against a real tmux and a real PTY (emulator differential and fuzz, auto-approve, paste, DSR, cast replay), so `tmux` and `ffmpeg` must be installed. The fuzz seed count is set by `TERMREEL_FUZZ_SEEDS`.

> [!IMPORTANT]
> **Python Environment Note**:
> The renderer needs PyCairo (system package `python3-cairo`, or `pip install pycairo` with `libcairo2-dev`), and the YAML schema uses pydantic when it is installed. A venv built with `--system-site-packages` on top of a system Python that has PyCairo works. CI (`.github/workflows/ci.yml`) installs `ffmpeg tmux libcairo2-dev` and runs `termreel test -w 8`.


