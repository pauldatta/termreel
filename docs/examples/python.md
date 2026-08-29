# Example: Python Interactive REPL

This scenario demonstrates typing expressions, evaluating list comprehensions, and simultaneous Asciinema v2 `.cast` export in the `dracula` theme.

---

## Scenario Manifest (`examples/python_repl.yaml`)

```yaml
version: "1.0"

metadata:
  title: "Python 3.11 Interactive REPL"
  subtitle: "Live Algorithm Evaluation"
  output: "output/python_repl.mp4"
  poster_output: "output/python_repl_poster.png"
  cast_output: "output/python_repl.cast"
  resolution: [1280, 720]
  fps: 30
  theme: "dracula"
  statusbar_left: "Python 3.11 | CPython | UTF-8"
  statusbar_right: "TermReel HD"

timeline:
  - show_card:
      tag: "Interactive REPL"
      title: "Python 3 Interpreter"
      desc: "Live code evaluation with dual Asciinema v2 telemetry export"
      duration: 2.0

  - launch:
      command: "python3"

  - type:
      text: "import math, sys"
      speed: 0.04
      send_key: "Enter"
      pause: 0.8

  - type:
      text: "primes = [p for p in range(2, 30) if all(p % d != 0 for d in range(2, int(math.isqrt(p)) + 1))]"
      speed: 0.03
      send_key: "Enter"
      pause: 1.0

  - type:
      text: "print(f'Discovered primes: {primes}')"
      speed: 0.035
      send_key: "Enter"
      pause: 2.0

  - type:
      text: "exit()"
      speed: 0.04
      send_key: "Enter"
      pause: 1.0

  - show_card:
      tag: "Finished"
      title: "REPL Evaluation Complete"
      duration: 1.5
```

---

## How to Record

```bash
termreel record examples/python_repl.yaml
```
