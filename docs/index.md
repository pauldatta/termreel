# TermReel: Universal Terminal Recording & Video Synthesis Engine

**TermReel** (`termreel` / `reccli`) is a standalone, headless CLI recording harness and deterministic video synthesis engine. It drives interactive command-line interfaces, terminal user interfaces (TUIs), and autonomous AI coding agents inside isolated pseudo-terminals (PTY/tmux), injects natural human-like keystrokes, reacts to live screen events, and streams pixel-perfect H.264 MP4 videos, animated GIFs, and Asciinema v2 (`.cast`) event streams directly into FFmpeg with **zero intermediate disk I/O**.

---

## Key Highlights

- **True Pseudo-Terminal (PTY) & Tmux Supervision**: Executes real binaries in authentic PTY environments with complete ANSI 256-color and 24-bit TrueColor RGB support.
- **Reactive UI & Modal Interception**: Intercepts workspace trust dialogs, model thinking spinners, and human-in-the-loop permission prompts without requiring YOLO flags or print modes.
- **Zero Intermediate Disk I/O**: Direct memory pipe streaming raw BGRA vector frames rendered by PyCairo directly into FFmpeg stdin.
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

## Quick Example

```bash
# 1. Probe a CLI tool
termreel probe git

# 2. Generate a tailored scenario YAML
termreel generate git -o scenarios/git_demo.yaml --theme tokyo-night

# 3. Record the high-fidelity video
termreel record scenarios/git_demo.yaml -o output/git_demo.mp4
```
