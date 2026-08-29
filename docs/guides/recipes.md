# Scenario Recipes & Templates

A collection of battle-tested, copy-pasteable TermReel YAML scenario templates for common developer tools and workflows.

---

## 1. Git Interactive Workflow

Simulates an authentic Git branch management and staging workflow with clean TrueColor logs:

```yaml
version: "1.0"
metadata:
  title: "Git Staging & Branch Hygiene"
  output: "output/git_demo.mp4"
  theme: "tokyo-night"
  fps: 30

environment:
  create_temp_workspace: true
  setup_commands:
    - "git init"
    - "echo 'print(1)' > app.py"
    - "git add app.py && git commit -m 'Initial commit'"
    - "git checkout -b feature/auth"
    - "echo 'print(2)' >> app.py"

timeline:
  - show_card:
      tag: "Git"
      title: "Branch Inspection"
      desc: "Checking status and visual commit tree"
      duration: 1.5
  - launch:
      command: "bash"
  - run_shell:
      command: "git status"
      pause: 1.2
  - run_shell:
      command: "git diff"
      pause: 1.5
  - run_shell:
      command: "git log --graph --oneline --all"
      pause: 2.0
```

---

## 2. Python REPL & Live Computation

Demonstrates Python interactive REPL sessions with syntax coloring:

```yaml
version: "1.0"
metadata:
  title: "Python 3.11 Interactive REPL"
  output: "output/python_repl.mp4"
  theme: "dracula"
  fps: 30

timeline:
  - show_card:
      tag: "Python"
      title: "Interactive REPL Session"
      duration: 1.5
  - launch:
      command: "python3"
  - type:
      text: "import math"
      speed: 0.04
      send_key: "Enter"
      pause: 0.5
  - type:
      text: "[math.sqrt(x) for x in range(1, 6)]"
      speed: 0.03
      send_key: "Enter"
      pause: 1.5
  - type:
      text: "exit()"
      send_key: "Enter"
      pause: 0.5
```

---

## 3. Docker & Container Build Simulation

Demonstrates container image builds with step-by-step layer caching output:

```yaml
version: "1.0"
metadata:
  title: "Docker Container Build"
  output: "output/docker_build.mp4"
  theme: "nord"
  fps: 30

environment:
  create_temp_workspace: true
  setup_commands:
    - "echo 'FROM alpine:latest' > Dockerfile"
    - "echo 'RUN apk add --no-cache curl' >> Dockerfile"

timeline:
  - show_card:
      tag: "Docker"
      title: "Multi-Stage Build"
      duration: 1.5
  - launch:
      command: "bash"
  - run_shell:
      command: "cat Dockerfile"
      pause: 1.5
  - type:
      text: "docker build -t my-app:latest ."
      speed: 0.03
      send_key: "Enter"
      pause: 2.5
```

---

## 4. Antigravity Agent Session Resumption

Demonstrates continuing a previous multi-step AI coding session:

```yaml
version: "1.0"
metadata:
  title: "Antigravity - Session Resumption"
  output: "output/agy_resume.mp4"
  theme: "catppuccin-mocha"

environment:
  resume: true
  preserve_workspace: true

permissions:
  auto_approve: true
  allow_commands: ["python3", "pytest", "git"]
  allow_tools: ["run_command", "write_to_file", "read_file"]

triggers:
  - on_match: "Do you trust the contents of this project\\?"
    action: "Enter"
    once: true

timeline:
  - launch:
      command: "agy"
      resume: true
      wait_for_idle: true
  - type:
      text: "Now run the test suite and verify all tests pass"
      speed: 0.03
      send_key: "Enter"
  - wait_for_idle:
      timeout: 30.0
      reading_pause: 2.0
  - type:
      text: "/exit"
      send_key: "Enter"
      pause: 1.0
```
