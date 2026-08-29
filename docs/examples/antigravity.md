# Example: Antigravity AI Agent Interactive Session

This scenario records an interactive session of Google Antigravity (`agy`), executing in pure interactive TUI mode with automated project trust confirmation, permission handling, and code refactoring.

---

## Scenario Manifest (`examples/agy_hooks_demo.yaml`)

```yaml
version: "1.0"

metadata:
  title: "Antigravity Agent - Interactive TUI & Lifecycle Hooks"
  subtitle: "Recorded via TermReel with Live Hooks Auto-Approval"
  output: "output/agy_hooks_session.mp4"
  cast_output: "output/agy_hooks_session.cast"
  poster_output: "output/agy_hooks_session_poster.png"
  resolution: [1280, 720]
  fps: 30
  theme: "catppuccin-mocha"
  statusbar_left: "Google Antigravity CLI"
  statusbar_right: "TermReel Hooks Engine"

environment:
  create_temp_workspace: true
  temp_workspace_prefix: "termreel_agy_hooks_"
  setup_commands:
    - "git init"
    - "git config user.name 'TermReel Demo'"
    - "git config user.email 'demo@termreel.dev'"
    - |
      cat << 'EOF' > app.py
      def calculate_factorial(n: int) -> int:
          """Computes the factorial of a non-negative integer."""
          if n < 0:
              raise ValueError("Factorial is not defined for negative numbers.")
          if n in (0, 1):
              return 1
          result = 1
          for i in range(2, n + 1):
              result *= i
          return result

      if __name__ == "__main__":
          print(f"5! = {calculate_factorial(5)}")
      EOF
    - "git add app.py && git commit -m 'Initial commit with factorial implementation'"

permissions:
  auto_approve: true
  allow_commands: ["python3", "python3 app.py", "git", "pytest"]
  allow_tools: ["run_command", "write_to_file", "read_file", "grep_search"]

triggers:
  - on_match: "Do you trust the contents of this project\\?|Yes, I trust"
    action: "Enter"
    once: true
  - on_match: "Requesting permission for:|Do you want to proceed\\?|\\[y/N\\]"
    action:
      type: "send_key"
      value: "Enter"
      delay_before: 0.8
      delay_after: 0.3
    once: false
    cooldown: 1.5
    max_firings: 15

timeline:
  - show_card:
      tag: "Agent Showcase"
      title: "Interactive Antigravity (agy) with Hooks"
      desc: "Live pseudo-terminal interaction with deterministic PreToolUse auto-approval"
      duration: 3.0

  - launch:
      command: "agy"
      wait_for_idle: true
      timeout: 25.0

  - type:
      text: "Add a function calculate_fibonacci(n: int) -> int to app.py and add a test in main"
      speed: 0.035
      jitter: 0.015
      send_key: "Enter"

  - wait_for_idle:
      timeout: 45.0
      reading_pause: 3.0

  - type:
      text: "/exit"
      speed: 0.04
      send_key: "Enter"
      pause: 1.5

  - run_shell:
      command: "git diff"
      speed: 0.03
      pause: 2.5

  - show_card:
      tag: "Completed"
      title: "Clean Recording Finished"
      desc: "Captured full interactive UI without permission blocks or YOLO flags"
      duration: 2.5
```

---

## How to Record

```bash
termreel record examples/agy_hooks_demo.yaml
```
