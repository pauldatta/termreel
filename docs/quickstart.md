# Quickstart Guide

Get up and running with TermReel in under 60 seconds.

---

## 1. Installation

Choose your preferred installation method:

=== "One-Line Binary (Zero Python Required)"

    The standalone pre-compiled binary bundles all dependencies and installs directly into `~/.local/bin/termreel`:

    ```bash
    curl -fsSL https://raw.githubusercontent.com/pauldatta/termreel/main/install.sh | bash
    ```

=== "uv tool (Recommended for Developers)"

    If you have [uv](https://astral.sh/uv) installed, install TermReel into a lightning-fast isolated environment:

    ```bash
    uv tool install git+https://github.com/pauldatta/termreel.git
    ```

    Or run directly without installing:

    ```bash
    uvx --from git+https://github.com/pauldatta/termreel.git termreel probe git
    ```

=== "pipx"

    ```bash
    pipx install git+https://github.com/pauldatta/termreel.git
    ```

=== "From Source"

    ```bash
    git clone https://github.com/pauldatta/termreel.git
    cd termreel
    uv pip install -e .
    # or: pip install -e .
    ```

---

## 2. Pre-Flight Verification

TermReel requires `ffmpeg` for H.264 video transcoding. Verify your environment:

```bash
termreel info
```

Output checks:
```
[termreel] Environment & Subsystems Status:
  Python Version:     3.11.x
  FFmpeg Detected:    /usr/bin/ffmpeg (libx264 enabled)
  PyCairo Backend:    libcairo 1.18.0 (vector rasterizer ready)
  Tmux Available:     /usr/bin/tmux
  Default Themes:     9 calibrated palettes loaded
```

If `ffmpeg` is missing:
- **macOS**: `brew install ffmpeg`
- **Ubuntu / Debian**: `sudo apt update && sudo apt install -y ffmpeg`
- **Fedora / RHEL**: `sudo dnf install -y ffmpeg`

---

## 3. Your First Recording in 3 Steps

### Step 1: Probe the Target CLI
Discover subcommands, usage, and permissions for any command-line tool:

```bash
termreel probe git
```

### Step 2: Auto-Generate a Scenario Manifest
Generate a customized YAML manifest with chapter cards, typing cadences, and color themes:

```bash
termreel generate git -o my_git_demo.yaml --theme tokyo-night
```

### Step 3: Record the High-Definition Video
Execute the scenario in a headless pseudo-terminal:

```bash
termreel record my_git_demo.yaml -o output/git_demo.mp4
```

---

## 4. One-Shot Quick Commands

Need a 5-second video recording of a single command without writing YAML?

```bash
# Record command execution directly to MP4
termreel exec "git status" -o output/status.mp4 --theme nord

# Convert an existing Asciinema (.cast) log to MP4
termreel cast2video session.cast -o output/session.mp4 --theme catppuccin-mocha --speed 2.0
```

---

## 5. Next Steps

- Explore [Scenario Manifest Specifications](scenarios.md) for custom chapter cards and delays.
- Learn how to [Record Interactive AI Agents](interactive-agents.md) with modal trust and `[y/N]` prompt handling.
- Browse [Example Scenarios](examples/antigravity.md) for full reference YAMLs.
