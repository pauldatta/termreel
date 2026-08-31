# CLI Reference

TermReel provides a unified command-line tool `termreel` (aliased as `reccli`).

---

## Commands Summary

| Command | Purpose | Example |
| :--- | :--- | :--- |
| `termreel record` | Record video from declarative scenario YAML | `termreel record scenario.yaml -o out.mp4` |
| `termreel exec` | Record a single command directly to video | `termreel exec "git log" -o log.mp4` |
| `termreel cast2video` | Convert Asciinema `.cast` file to MP4/GIF | `termreel cast2video log.cast -o replay.mp4` |
| `termreel validate` | Validate scenario YAML syntax & schema | `termreel validate scenario.yaml` |
| `termreel probe` | Explore CLI binary metadata and subcommands | `termreel probe agy` |
| `termreel generate` | Scaffold tailored YAML scenario for a CLI | `termreel generate git -o git.yaml` |
| `termreel batch` | Concurrently render batches of scenarios | `termreel batch scenarios/*.yaml -c 4` |
| `termreel audit` | Multimodal video verification with Gemini | `termreel audit demo.mp4 --spec spec.yaml` |
| `termreel themes` | List all 9 visual themes and palettes | `termreel themes` |
| `termreel test` | Run test suite concurrently across async workers | `termreel test -w 8` |
| `termreel info` | Display environment and dependency status | `termreel info` |

---

## Subcommand Details

### `termreel record`
```bash
termreel record <scenario.yaml> [options]
```
- `-o, --output <path>`: Override output video path (.mp4, .webm, .gif).
- `--fps <int>`: Override frame rate (default: 30).
- `--theme <name>`: Override visual theme.
- `--backend <auto|tmux|pty>`: PTY backend (default: auto).
- `--cast <path>`: Export Asciinema v2 `.cast` log.
- `--poster <path>`: Export PNG poster thumbnail.
- `-c, --continue, --resume`: Resume latest conversation/session in the workspace.
- `--conversation <id>`: Resume a specific previous conversation by ID.
- `--workspace <path>`: Attach to an existing workspace directory.
- `--preserve-workspace`: Keep temporary workspace directory intact after recording.
- `-q, --quiet`: Suppress verbose logging.

### `termreel batch`
```bash
termreel batch <scenarios...> [options]
```
Executes multiple scenario recordings concurrently using a worker pool.
- `-c, --concurrency <int>`: Number of concurrent workers (default: 4).
- `-o, --output-dir <path>`: Destination directory for all output videos.
- `--generate-posters` / `--no-posters`: Automatically extract PNG poster frame for each recording.
- `--poster-time <float>`: Timestamp in seconds for poster frame capture (default: 0.5).
- `--report <path>`: Path to output structured batch report (JSON or Markdown).
- `--theme <name>`: Override theme across all scenarios.
- `--fps <int>`: Override framerate across all scenarios.
- `-q, --quiet`: Suppress verbose logging.

### `termreel audit`
```bash
termreel audit <video.mp4> [options]
```
Performs automated multimodal video verification and scoring against a specification.
- `--spec <path>`: Path to original scenario YAML manifest, PRD, or test document.
- `--model <name>`: Multimodal AI model to use (default: `gemini-3.1-pro-preview`).
- `--threshold <int>`: Minimum passing score out of 100 (default: 80).
- `--report <path>`: Path to save the audit report scorecard (Markdown or JSON).
- `--json`: Output raw JSON scorecard to stdout.

### `termreel exec`
```bash
termreel exec "<command>" [options]
```
- `-o, --output <path>`: Output video path.
- `--title <str>`: Window title.
- `--subtitle <str>`: Window subtitle.
- `--cwd <path>`: Working directory.
- `--theme <name>`: Visual theme.
- `--timeout <float>`: Max duration in seconds (default: 60.0).

### `termreel probe`
```bash
termreel probe <binary>
```
Inspects binary version, usage, detected subcommands, flags, and recommended permission rules.

### `termreel generate` (or `init`)
```bash
termreel generate <binary> [options]
```
- `-o, --output <path>`: Destination YAML file path.
- `--title <str>`: Custom window title.
- `--theme <name>`: Target visual theme (default: catppuccin-mocha).
- `--fps <int>`: Target recording FPS (default: 30).
- `-p, --print`: Print generated YAML to stdout.

### `termreel test`
```bash
termreel test [-w <workers>] [-d <test_dir>]
```
Runs unit and integration tests in parallel (e.g. `termreel test -w 8`).

