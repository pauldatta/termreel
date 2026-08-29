# TermReel Architecture

TermReel is engineered around modular subsystems designed for determinism, performance, and thread safety.

---

## Subsystem Overview

```
termreel/
├── emulator/          # 2D character matrix, ANSI CSI/SGR/OSC parser, 256/TrueColor palettes
├── supervisor/        # Native POSIX PTY and Tmux session controllers
├── reactor/           # Live screen monitor, reactive triggers, async key dispatch
├── renderer/          # PyCairo vector rasterizer, chrome, chapter cards, 9 themes
├── transcoder/        # In-memory FFmpeg stdin pipe and animated GIF encoder
├── scenario/          # Declarative YAML manifest schema and execution runner
├── generator/         # CLI discovery, subcommand parser, and scenario scaffolder
├── hooks/             # Agent lifecycle hooks, auto-approval, and bridge tailing
├── utils/             # Keystroke cadence, token redactions, Asciinema v2 codec
├── testing.py         # High-speed parallel/async test runner
└── cli.py             # Command-line interface entrypoint
```

---

## 1. Terminal State & ANSI Parser (`termreel.emulator`)

- **`CharCell` Matrix**: Stores character glyphs, foreground/background RGB colors, bold, dim, italic, underline, strikethrough, reverse video, and blinking states.
- **TrueColor & 256-Color Palettes**: Full 24-bit RGB and 256-palette support with gamma/brightness balancing.
- **Escape Parser**: Supports cursor positioning (`H`, `f`, `A`, `B`, `C`, `D`), line insertions/deletions (`L`, `M`), scrolling margins (`r`, `S`, `T`), and alternate screen buffers (`?1049h`/`?1049l`).
- **Thread Safety**: Protected via re-entrant locking (`RLock`) and snapshot cloning.

---

## 2. PTY & Tmux Supervision (`termreel.supervisor`)

- **`TmuxSupervisor`**: Controls isolated tmux sessions via `send-keys` and `capture-pane -p -e`.
- **`PtySupervisor`**: Native POSIX pseudo-terminal allocator (`pty.openpty()`) with non-blocking I/O reader threads.
- **Factory**: Auto-detects the optimal backend based on system capabilities.

---

## 3. Screen Monitor & Event Reactor (`termreel.reactor`)

- **Non-blocking Reactive Triggers**: Evaluates screen state regexes (e.g. workspace trust confirmations, permission approvals `[y/N]`).
- **Asynchronous Key Dispatch**: Dispatches actions in a worker thread so modal dialogs remain visible on screen for natural reading pauses without pausing video frame rasterization.
- **Idle Detection (`wait_for_idle`)**: Monitors ready prompts and generation indicators (`⠋ Thinking...`, `⡿ Generating...`) to eliminate brittle sleep timers.

---

## 4. Vector Frame Renderer (`termreel.renderer`)

- **PyCairo Rasterization**: Sub-pixel accurate text rendering with monospace font metrics.
- **Window Chrome**: macOS-style traffic light buttons, titlebar badges (`● LIVE TTY`), and bottom metadata status bars.
- **Chapter Cards**: Floating vector overlay cards with tag pills and descriptions.
- **9 Built-in Themes**: `catppuccin-mocha`, `catppuccin-latte`, `tokyo-night`, `dracula`, `nord`, `one-dark`, `monokai`, `github-dark`, `matrix`.

---

## 5. Streaming Transcoder (`termreel.transcoder`)

- **Zero Intermediate Disk I/O**: Direct streaming of raw BGRA bytes into FFmpeg standard input pipe.
- **Faststart H.264 MP4**: `-movflags +faststart` ensures instant web playback.
- **Multi-Format**: Generates MP4, VP9 WebM, animated GIFs, and PNG poster frames simultaneously with Asciinema v2 (`.cast`) event streams.
