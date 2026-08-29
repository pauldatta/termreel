# Example: Google Cloud SDK (`gcloud`)

This scenario demonstrates exploring Google Cloud configuration and diagnostics using the `nord` theme.

---

## Scenario Manifest

```yaml
version: "1.0"

metadata:
  title: "Google Cloud SDK Inspection"
  subtitle: "Configuration & Project Diagnostics"
  output: "output/gcloud_demo.mp4"
  poster_output: "output/gcloud_demo_poster.png"
  resolution: [1280, 720]
  fps: 30
  theme: "nord"
  statusbar_left: "Google Cloud SDK | UTF-8"
  statusbar_right: "TermReel HD"

environment:
  create_temp_workspace: true

timeline:
  - show_card:
      tag: "Cloud Tools"
      title: "Google Cloud SDK"
      desc: "Inspecting environment properties and component versions"
      duration: 2.0

  - launch:
      command: "bash"

  - run_shell:
      command: "gcloud version"
      speed: 0.03
      pause: 1.5

  - run_shell:
      command: "gcloud config list --all | head -n 12"
      speed: 0.03
      pause: 1.5

  - show_card:
      tag: "Complete"
      title: "Cloud Inspection Finished"
      duration: 1.5
```
