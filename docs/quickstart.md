# Quickstart Guide

Get up and running with TermReel in minutes.

---

## 1. System Requirements

- **Linux / macOS** (POSIX environment)
- **Python 3.10+**
- **PyCairo** (`libcairo2-dev` / `cairo`)
- **FFmpeg** & **FFprobe**
- **Tmux** (recommended for advanced TUI capture)

---

## 2. Installation

Clone and install TermReel in editable mode:

```bash
git clone https://github.com/pauldatta/termreel.git
cd termreel
pip install -e .
```

Verify your installation:

```bash
termreel info
```

---

## 3. Basic Usage Patterns

### A. Record a Declarative Scenario
```bash
termreel record examples/git_workflow.yaml -o output/git_demo.mp4
```

### B. Quick One-Shot Command Recording
```bash
termreel exec "git status" --output output/git_status.mp4 --theme tokyo-night
```

### C. Replay Asciinema Cast to Video
```bash
termreel cast2video session.cast -o session.mp4 --theme dracula
```

### D. Probe a CLI & Scaffold a Scenario
```bash
# Explore CLI subcommands and capabilities
termreel probe gcloud

# Scaffold a new YAML scenario
termreel generate gcloud -o scenarios/gcloud_demo.yaml --theme nord
```

### E. Run Test Suite Concurrently
```bash
termreel test -w 8
```
