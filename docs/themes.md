# Visual Themes & Chrome Styling

TermReel features 9 built-in visual themes with calibrated contrast for vector rendering, alongside customizable macOS window chrome and dynamic status bars.

---

## Available Themes

| Theme Name | Style | Terminal Background | Accent Color |
| :--- | :--- | :--- | :--- |
| **`catppuccin-mocha`** | Modern dark pastel (Default) | `#1e1e2e` | `#cba6f7` (Mauve) |
| **`catppuccin-latte`** | Clean light mode | `#eff1f5` | `#8839ef` (Mauve) |
| **`tokyo-night`** | Deep midnight blue & cyan | `#1a1b26` | `#7aa2f7` (Blue) |
| **`dracula`** | High-contrast purple & pink | `#282a36` | `#bd93f9` (Purple) |
| **`nord`** | Arctic pastel blue & frost | `#2e3440` | `#88c0d0` (Frost Cyan) |
| **`one-dark`** | Atom One Dark | `#282c34` | `#61afef` (Blue) |
| **`monokai`** | Vibrant green & yellow | `#272822` | `#a6e22e` (Green) |
| **`github-dark`** | Minimalist GitHub dark | `#0d1117` | `#58a6ff` (Blue) |
| **`matrix`** | High-contrast phosphor green HUD | `#0d1117` | `#00ff41` (Green) |

---

## Listing Themes from CLI

```bash
termreel themes
```

---

## Custom Status Bar Metadata

Scenario manifests allow custom left and right status bar labels:

```yaml
metadata:
  statusbar_left: "Antigravity CLI | Real PTY | UTF-8"
  statusbar_right: "TermReel HD (1280x720@30fps)"
```

You can also dynamically update the status bar during timeline execution:
```yaml
- set_statusbar:
    left: "Compiling artifacts..."
    right: "Phase 2/3"
```
