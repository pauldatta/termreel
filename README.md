# TermReel: Universal Terminal Recording & Video Synthesis Harness

**TermReel** (`termreel`) is a standalone, headless CLI recording harness and deterministic video synthesis engine. It drives any interactive CLI or AI coding agent (`agy`, `gemini`, `gh`, `kubectl`, `vim`, Bubbletea/Textual TUIs) inside a real pseudo-terminal (PTY), simulates natural keystrokes, reacts to live screen events, and streams pixel-perfect H.264 MP4/WebM videos and GIF animations with custom window chrome and chapter cards.

---

## Project Structure

```
termreel/
├── README.md                          # Project overview and quickstart
├── docs/
│   └── SDD_TERMREEL.md                # Full Software Design Document (RFC)
└── prototype/
    └── record_terminal.py             # Reference PTY supervisor & vector renderer
```

---

## Key Capabilities

1. **True PTY Execution:** Runs real CLI binaries in pseudo-terminals with full ANSI 256 / 24-bit TrueColor support.
2. **Event-Driven Screen Reactor:** Auto-confirms modal dialogs (like workspace trust prompts) and detects model idle states.
3. **Zero Intermediate Disk I/O:** Renders vector frames with PyCairo/Skia directly into an FFmpeg stdin pipe.
4. **Declarative Automation:** Drives multi-step interactive workflows via YAML scenario manifests or Python scripts.
