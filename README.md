# TermReel

[![CI](https://github.com/pauldatta/termreel/actions/workflows/ci.yml/badge.svg)](https://github.com/pauldatta/termreel/actions/workflows/ci.yml)
[![Docs](https://github.com/pauldatta/termreel/actions/workflows/docs.yml/badge.svg)](https://pauldatta.github.io/termreel/)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

**TermReel** (`termreel` / `reccli`) is a standalone, headless CLI recording harness and deterministic video synthesis engine. It drives interactive CLI tools, TUIs, and autonomous AI coding agents (`agy`, `git`, `gcloud`, `gh`, `kubectl`, `vim`, etc.) inside real pseudo-terminals (PTY/tmux), injects natural human keystrokes, reacts to live screen events, and streams pixel-perfect H.264 MP4/WebM videos, animated GIFs, and Asciinema v2 (`.cast`) event streams directly into FFmpeg with **zero intermediate disk I/O**.

---

## Key Features

- **True Pseudo-Terminal (PTY) & Tmux Supervision**: Executes real binaries in authentic PTY environments with complete ANSI 256-color and 24-bit TrueColor RGB support.
- **Reactive UI & Permission Interception**: Intercepts modal trust dialogs, model thinking spinners, and human-in-the-loop permission prompts without requiring YOLO flags or print modes.
- **Zero Intermediate Disk I/O**: Direct memory pipe streaming raw BGRA vector frames rendered by PyCairo into FFmpeg stdin.
- **Declarative YAML Scenarios**: Multi-step interactive workflows, chapter cards, dynamic status bars, and keystroke cadence simulation.
- **Automated CLI Exploration & Scaffolding**: `termreel probe` and `termreel generate` inspect target CLIs to auto-craft validated scenario manifests.
- **Dual Telemetry Export**: Simultaneously outputs standard MP4 videos, animated GIFs, PNG poster thumbnails, and Asciinema v2 (`.cast`) telemetry logs.
- **Token & Secret Redaction**: In-place regular expression masking for API keys, OAuth credentials, and private tokens.
- **Parallel Async Test Runner**: Fast test suite execution (`termreel test -w 8`).

---

## 🤖 Using TermReel with AI Agents (`agy`, Claude Code, Gemini CLI)

TermReel includes an **Agent Skill** (`skills/termreel/SKILL.md` / `.agents/skills/termreel/SKILL.md`) that allows AI coding assistants to automatically script, record, and verify terminal videos directly from your high-level instructions.

```
┌──────────────────────────────────────┐
│  User Plain Text Prompt / Doc Link   │
│  "Record a demo of refactoring auth" │
└──────────────────┬───────────────────┘
                   │
                   ▼
┌──────────────────────────────────────┐
│  AI Agent with TermReel Skill        │
│  • Probes target CLI (`probe`)       │
│  • Reads requirements from docs      │
│  • Crafts scenario YAML manifest     │
└──────────────────┬───────────────────┘
                   │
                   ▼
┌──────────────────────────────────────┐
│  TermReel Headless Recording Harness │
│  • Spawns PTY / Tmux session         │
│  • Handles permissions & trust dialog│
│  • Outputs pixel-perfect MP4 + .cast │
└──────────────────────────────────────┘
```

### 1. Zero-YAML Prompting: Prompt in Plain English
You do not need to write YAML by hand. You can instruct your AI assistant in natural language:

> *"Record a high-quality video showing how to initialize a Git repo, create a python script, and inspect the branch graph using TermReel in the tokyo-night theme."*

### 2. Document & PR-Driven Video Generation
Point your agent to a Google Doc, Markdown design spec, or GitHub pull request:

> *"Read `@docs/feature_spec.md` and record a 720p interactive walkthrough demonstrating the new CLI flags and test verification."*

The agent reads the document using its file/search tools, references the TermReel Skill, maps the workflow into structured timeline steps (`show_card`, `launch`, `type`, `wait_for_idle`, `run_shell`), executes `termreel record`, and returns the completed video artifact.

---

## Quickstart

### 1. Installation

```bash
git clone https://github.com/pauldatta/termreel.git
cd termreel
pip install -e .
```

### 2. Basic Commands

```bash
# Explore CLI subcommands and capabilities
termreel probe git

# Scaffold a tailored scenario YAML
termreel generate git -o scenarios/git_demo.yaml --theme tokyo-night

# Record the high-fidelity video
termreel record scenarios/git_demo.yaml -o output/git_demo.mp4

# Direct one-shot recording
termreel exec "git status" -o output/status.mp4 --theme nord

# Transcode Asciinema cast to MP4
termreel cast2video session.cast -o session.mp4 --theme dracula

# Resume an ongoing session or conversation
termreel record scenarios/git_demo.yaml --resume

# Run full test suite in parallel
termreel test -w 8
```

---

## Sample Scenario Manifest

```yaml
version: "1.0"

metadata:
  title: "Git Workflow Masterclass"
  subtitle: "Staging, Commit Hygiene & Graph Inspection"
  output: "output/git_workflow.mp4"
  poster_output: "output/git_workflow_poster.png"
  resolution: [1280, 720]
  fps: 30
  theme: "tokyo-night"
  statusbar_left: "Git v2.55 | main | clean"
  statusbar_right: "TermReel HD"

environment:
  create_temp_workspace: true

timeline:
  - show_card:
      tag: "Module 1"
      title: "Interactive Git Workflow"
      desc: "Simulating human typing cadence with TrueColor logs"
      duration: 2.0

  - launch:
      command: "bash"

  - run_shell:
      command: "git status"
      pause: 1.2

  - run_shell:
      command: "git log --graph --oneline --decorate"
      pause: 2.0

  - show_card:
      tag: "Complete"
      title: "Workflow Mastered"
      duration: 1.5
```

---

## Documentation

Full documentation, architecture specs, and scenario guides are available at [https://pauldatta.github.io/termreel/](https://pauldatta.github.io/termreel/) or in the [`docs/`](docs/) directory:

- [Architecture & Design](docs/architecture.md)
- [CLI Reference](docs/cli.md)
- [Scenario Manifest Specification](docs/scenarios.md)
- [Recording Interactive AI Agents](docs/interactive-agents.md)
- [CLI Explorer & Scaffolding](docs/generator.md)
- [Visual Themes & Custom Chrome](docs/themes.md)
- [Example Scenarios](docs/examples/antigravity.md)

---

## Contributing

Contributions are welcome! Please review our [Contribution Guide](CONTRIBUTING.md) for development workflows, testing guidelines, and code standards.

---

## License

TermReel is open-source software licensed under the [Apache-2.0 License](LICENSE).
