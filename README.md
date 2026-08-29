# TermReel: Universal Terminal Recording & Video Synthesis Harness

**TermReel** (`termreel`) is a standalone, headless CLI recording harness and deterministic video synthesis engine. It drives any interactive CLI or AI coding agent (`agy`, `gemini`, `gh`, `kubectl`, `vim`, Bubbletea/Textual TUIs) inside a real pseudo-terminal (PTY), simulates natural keystrokes, reacts to live screen events, and streams pixel-perfect H.264 MP4/WebM videos and GIF animations with custom window chrome, chapter cards, and token redaction.

---

## Key Capabilities

1. **True PTY & Tmux Supervision:** Runs real CLI binaries in pseudo-terminals with full ANSI 256-color and 24-bit TrueColor RGB support.
2. **Antigravity Hooks Integration:** Provisions `.agents/hooks.json` to auto-approve interactive tool actions without requiring `--dangerously-skip-permissions` or `-p`, bridging lifecycle events (`PreToolUse`, `PostInvocation`) into live status badges.
3. **Event-Driven Screen Reactor:** Auto-confirms modal dialogs (like workspace trust prompts or permission approvals) and detects model idle states without brittle sleep timers.
4. **Zero Intermediate Disk I/O:** Renders vector frames directly with PyCairo into an FFmpeg stdin pipe.
5. **Declarative Scenario Automation:** Drives multi-step interactive workflows via YAML scenario manifests or Python scripts.
6. **Secret Redaction:** Built-in regex masking for API keys, OAuth tokens, and private hostnames.
7. **Dual Telemetry Export:** Synthesizes standard MP4 videos alongside Asciinema v2 (`.cast`) event logs and PNG poster thumbnails.

---

## Quick Start

### 1. View System Diagnostics & Themes
```bash
termreel info
termreel themes
```

### 2. Record an Interactive Scenario
```bash
termreel record examples/agy_quickstart.yaml -o output/agy_quickstart.mp4
```

### 3. Record Any Command Directly
```bash
termreel exec "git status" --output output/git_status.mp4 --theme tokyo-night
```

### 4. Transcode Asciinema Cast to Video
```bash
termreel cast2video session.cast -o session.mp4 --theme dracula
```

---

## Project Structure

```
termreel/
├── pyproject.toml                     # Package definition & CLI entry points
├── README.md                          # Project overview & quickstart
├── docs/
│   ├── SDD_TERMREEL.md                # Full Software Design Document (RFC)
│   └── USAGE.md                       # Comprehensive User & Developer Guide
├── examples/
│   ├── agy_quickstart.yaml            # Antigravity CLI scenario with trust triggers
│   ├── git_workflow.yaml              # Interactive Git staging & log workflow
│   └── python_repl.yaml               # Interactive Python REPL session
├── termreel/
│   ├── emulator/                      # ANSI parser, 2D grid state, 256/TrueColor palettes
│   ├── supervisor/                    # Native PTY and Tmux session controllers
│   ├── reactor/                       # Screen monitor, triggers, and idle detector
│   ├── renderer/                      # PyCairo vector rasterizer, chrome, cards, 9 themes
│   ├── transcoder/                    # Zero-disk FFmpeg stdin pipe and GIF encoder
│   ├── scenario/                      # Declarative YAML manifest schema & runner
│   ├── utils/                         # Natural keystroke cadence, redaction, .cast parser
│   └── cli.py                         # Unified command-line interface
└── tests/                             # Unit and integration test suite
```

---

## Running Tests

Run the complete test suite (including live `agy` CLI integration test):

```bash
python3 -m unittest discover -s tests -v
```

---

## License

Apache-2.0
