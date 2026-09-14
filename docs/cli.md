# CLI Reference

TermReel provides a unified command-line tool `termreel` (aliased as `reccli`).

---

## Commands Summary

| Command | Purpose | Example |
| :--- | :--- | :--- |
| `termreel record` | Record video from declarative scenario YAML | `termreel record scenario.yaml -o out.mp4` |
| `termreel live` | Record your own terminal session, driven by hand | `termreel live -o demo.mp4 --theme tokyo-night` |
| `termreel exec` | Record a single command directly to video | `termreel exec "git log" -o log.mp4` |
| `termreel cast2video` | Convert Asciinema `.cast` file to MP4/GIF | `termreel cast2video log.cast -o replay.mp4` |
| `termreel validate` | Validate scenario YAML syntax & schema | `termreel validate scenario.yaml` |
| `termreel mask` | Inspect, test, and verify screen masking & redactions | `termreel mask --verify output.cast --strict` |
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

### `termreel live`
```bash
termreel live [command] [options]
```
Records **your own** interactive terminal session. You drive the shell by hand;
TermReel encodes what happens. Unlike `record`, there is no manifest and no
keystroke simulation.

```bash
termreel live -o demo.mp4 --theme tokyo-night
#  ● Recording — drive your terminal normally.  ^T p pause  ^T q stop
```

- `command`: Command to run (default: `$SHELL`, falling back to `bash`).
- `-o, --output <path>`: Output video path (default: `output/live.mp4`).
- `--fps <int>`: Frames per second (default: 15 — hand typing does not need 30).
- `--theme <name>`: Visual theme.
- `--title <str>` / `--subtitle <str>`: Window chrome text.
- `--cols <int>` / `--rows <int>`: Lock the recording grid; the canvas size is derived from it.
- `--resolution <WxH>`: Canvas size in pixels (default: `1280x720`, which is a 131x29 grid).
- `--crossfade <float>`: Crossfade duration across a cut, in seconds (default: 0.25).
- `--hard-cuts`: Jump straight from pause to resume with no crossfade.
- `--prefix <key>`: Hotkey prefix (default: `C-t`).
- `--cast <path>`: Also write an Asciinema v2 `.cast` log.
- `--cwd <path>`: Working directory for the recorded command.
- `--preset <name>` / `--crf <int>`: x264 encoder settings (defaults: `veryfast`, 20).
- `--keys`: Report what bytes your terminal sends for each key, then exit.
- `-q, --quiet`: Suppress status logging.

#### Hotkeys

All controls are a prefix keystroke followed by one key.

| Keys | Effect |
| :--- | :--- |
| `^T p` or `^T Space` | Pause / resume recording |
| `^T r` | Resume |
| `^T m` | Mark the current position |
| `^T q` | Stop and finalise the video |
| `^T ?` | Print the hotkey reminder |
| `^T ^T` | Send a literal prefix keystroke to the shell |

Pausing stops **recording**, not the shell. The child process keeps running;
TermReel simply stops writing frames, so the paused interval is cut out of the
finished video. On resume the last pre-pause frame is crossfaded into the first
post-resume frame, which is what signals the cut to the viewer — a `PAUSED`
caption can never appear in the output, because paused frames are never written.

Anything typed inside a bracketed paste is forwarded verbatim, prefix byte
included, so pasting text that happens to contain `^T` cannot trigger a control.

#### Rebinding the prefix

Resolution order, highest priority first:

```
--prefix  ->  $TERMREEL_PREFIX  ->  ~/.termreel/config.yaml  ->  C-t
```

```yaml
# ~/.termreel/config.yaml
live:
  prefix: "C-a"
```

Accepted forms are `C-t`, `ctrl+t`, `ctrl-t`, `^T`, `C-]`, and `0x14`.

The default is `C-t` (`0x14`). On a Mac the usable key pool is small: Command
never reaches the terminal, Option emits accented characters unless you enable
"Use Option as Meta", and the shell owns most of Control. Single-letter Ctrl
combinations are the only class that transmits identically across every Mac
keyboard layout.

`C-c`, `C-d`, `C-s`, `C-q`, `C-z`, `C-\` and `Escape` are **refused** with an
explanation rather than accepted — the recorded shell needs them, and `C-s`
would freeze your terminal with no way out.

To find out what your own keyboard actually sends:

```bash
termreel live --keys
# Press any key to see what your terminal sends.  Ctrl-C to exit.
#   -> 0x14         C-t          OK  safe. Suggested config: prefix: "C-t"
#   -> 0x01         C-a          !!  readline beginning-of-line; also the tmux prefix on many setups
```

#### Control from another terminal

The session registers with the telemetry socket, so `termreel peek` works
during a live recording and the socket also accepts `PAUSE`, `RESUME`,
`TOGGLE_PAUSE` and `STOP`. That is the fallback for when a hotkey collides
with something your shell or TUI has already claimed.

#### Notes

- `termreel live` requires an interactive terminal on stdin and exits with an error otherwise.
- The canvas is locked for the whole recording, but the recorded shell follows your window: resize mid-session and the child is resized with you, clamped to the locked grid. Shrinking letterboxes; growing past the locked grid logs a warning once and the extra area is not captured.
- `.gif` output buffers the entire stream through a palette filtergraph, so nothing lands on disk until you stop. Prefer `.mp4`.
- Above roughly 1920x1080 the renderer warns: frame production alone consumes a large share of the frame budget at that size, and the encoder may apply backpressure. Use `--cols/--rows` to pick a smaller grid.
- Because raw mode means TermReel has no Ctrl-C of its own, `^T q` is the ordinary way out. `SIGTERM` and `SIGHUP` — closing the window, or a plain `kill` — also stop the recording cleanly: the video is finalised, the `.cast` is flushed, the recorded shell is terminated and the terminal is restored. A second signal force-exits. Background jobs you started inside the recorded shell have their own process groups and are left running, the same as when the recording ends normally.

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
- `--chunk-duration <float>`: Maximum segment window in seconds for long video auditing (default: 300.0s / 5 mins). Automatically prevents exceeding the 1M token context limit on long recordings.
- `--no-chunk`: Disable automated windowed chunking for long videos.
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

### `termreel peek`
```bash
termreel peek [session_id] [options]
```
Non-invasively inspects a running render or background recording task in real time.
- `session_id`: Optional session ID, prefix, or PID. Defaults to the latest active recording session.
- `-f, --follow, --watch`: Follow live terminal updates in real-time (10 FPS TUI stream).
- `--list`: List all currently active and recent recording sessions.
- `--image <path>`: Capture an instant high-resolution PNG vector screenshot of the live terminal frame.
- `--web [port]`: Launch a local web dashboard (default: `http://localhost:8989` or `http://pauldatta.c.googlers.com:8989`) with auto-refreshing live terminal view.
- `--raw`: Output raw plain screen text without HUD borders (ideal for piping or automated checks).
- `--interval <float>`: Screen polling/refresh interval in seconds (default: 0.1s).

### `termreel mask`
```bash
termreel mask [options]
```
Inspects, tests, and validates screen masking, secret redaction, and realistic value substitution rules against recordings (`.cast`), scenario YAML manifests, or plain text logs.

- `--verify, -v <file>`: Verify configured mask rules against a target recording (`.cast`), scenario (`.yaml`), or log file. Reports match counts per rule and warns on 0 matches (typo protection).
- `--strict`: Fails with exit code 1 if any configured custom mask rule has 0 matches.
- `--config <path>`: Override path to global configuration file (default: `~/.termreel/config.yaml`).
- `--test <string>`: Test mask rules against an inline text string and display substitutions.
- `--list`: List all active masking rules loaded from global configuration.
- `--json`: Output verification reports or rule telemetry in JSON format.

```bash
# Verify all secrets in a .cast file were masked, with typo protection
termreel mask --verify output/session.cast --strict

# Test value substitution interactively
termreel mask --test "gcloud config set project elevate-security-2026"
# Output: gcloud config set project acme-demo-42
```


