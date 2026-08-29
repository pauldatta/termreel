# CLI Explorer & Scenario Scaffolding

TermReel includes an intelligent discovery engine that probes target CLI tools and scaffolds scenario manifests automatically.

---

## 1. Probing a CLI Binary (`termreel probe`)

The `termreel probe` command analyzes an executable by running `--version`, `--help`, and inspecting its subcommands and options:

```bash
termreel probe agy
```

### Sample Output:
```
🔍 Discovered CLI Specification for: agy

  • Path:        /usr/local/bin/agy
  • Version:     1.1.22
  • Category:    AGENT
  • Usage:       agy [options] [command]
  • Summary:     agy command line interface

  📦 Detected Subcommands (11):
     - agent              List available agents
     - agents             List available agents
     - changelog          Show changelog and release notes
     - help               Show help for subcommands
     - mcp                Manage MCP servers
     - models             List available models
     - plugin             Manage plugins

  🚩 Detected Flags:
     --add-dir --agent -c --continue --conversation --dangerously-skip-permissions

  🛡️  Recommended Permissions:
     python3, git, pytest, npm, cat, ls
```

---

## 2. Generating Scenario Manifests (`termreel generate`)

Generate a scenario YAML manifest tailored to the probed tool:

```bash
termreel generate agy -o scenarios/agy_workshop.yaml --theme catppuccin-mocha
termreel generate git -o scenarios/git_demo.yaml --theme tokyo-night
termreel generate gcloud -o scenarios/gcloud_demo.yaml --theme nord
```

### Printing to Stdout
```bash
termreel generate git --print
```
