# Example: Git Interactive Workflow

This scenario demonstrates staging files, committing with clean messages, and inspecting branch graphs in the `tokyo-night` theme.

---

## Scenario Manifest (`examples/git_workflow.yaml`)

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
  temp_workspace_prefix: "termreel_git_demo_"
  setup_commands:
    - "git init"
    - "git config user.name 'Paul Datta'"
    - "git config user.email 'pkdatta2000@gmail.com'"
    - "echo 'def init(): pass' > app.py"

timeline:
  - show_card:
      tag: "Module 1"
      title: "Git Staging & Branching"
      desc: "Interactive Git commands with ANSI TrueColor graph logs"
      duration: 2.0

  - launch:
      command: "bash"

  - run_shell:
      command: "git status"
      speed: 0.03
      pause: 1.2

  - run_shell:
      command: "git add app.py"
      speed: 0.03
      pause: 1.0

  - run_shell:
      command: "git commit -m 'feat: Initial application entrypoint'"
      speed: 0.03
      pause: 1.5

  - run_shell:
      command: "git log --graph --oneline --decorate"
      speed: 0.03
      pause: 2.0

  - show_card:
      tag: "Complete"
      title: "Git Workflow Completed"
      duration: 1.5
```

---

## How to Record

```bash
termreel record examples/git_workflow.yaml
```
