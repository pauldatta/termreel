# Contributing to TermReel

Thank you for your interest in contributing to **TermReel**! We welcome contributions to our terminal emulator, PTY supervisors, vector renderer, transcoding engine, and CLI tools.

---

## 1. Development Setup

### Prerequisites
- Python 3.10+
- `libcairo2-dev` (PyCairo C bindings)
- `ffmpeg` & `ffprobe`
- `tmux`

### Clone & Install
```bash
git clone https://github.com/pauldatta/termreel.git
cd termreel
pip install -e ".[dev]"
```

---

## 2. Running Tests

We provide a parallel test runner that executes unit and integration tests across async workers:

```bash
# Run all tests concurrently across 8 worker threads
termreel test -w 8

# Or run standard unittest discovery
python3 -m unittest discover -s tests -v
```

---

## 3. Code Standards & Architecture Guidelines

1. **Zero Intermediate Disk I/O**: The video pipeline must stream frames directly to FFmpeg stdin via memory buffers. Do not write temporary frame PNGs to disk.
2. **Thread Safety**: All state mutations in `TerminalState`, `ScreenMonitor`, `PtySupervisor`, and `FFmpegPipe` must be synchronized via appropriate locks (`RLock` / `Lock`).
3. **Secret Redaction**: Always run new screen capture utilities through `Redactor` to prevent credential leakage.
4. **Clean Commits**: Write clear, descriptive commit messages following the Conventional Commits format (`feat:`, `fix:`, `docs:`, `test:`).

---

## 4. Submitting a Pull Request

1. Fork the repository and create a feature branch (`git checkout -b feat/my-feature`).
2. Implement your changes with corresponding unit tests in `tests/`.
3. Ensure all tests pass (`termreel test -w 8`).
4. Commit your changes and open a Pull Request against `main`.

---

## License

TermReel is open source software licensed under the [Apache-2.0 License](LICENSE).
