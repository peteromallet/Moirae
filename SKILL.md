---
name: demo-video
description: Create scripted terminal demo videos of Hermes Agent using the screenplay YAML pipeline. Record, render, and composite polished MP4s with camera zoom/pan, skin theming, and background compositing.
version: 1.0.0
author: Hermes Agent
license: MIT
prerequisites:
  commands: [python3, agg, ffmpeg, asciinema]
metadata:
  hermes:
    tags: [demo, video, recording, terminal, media]
---

# Demo Video Pipeline

Create polished terminal demo videos from YAML screenplays. The pipeline records a scripted terminal session, renders it to GIF via agg, then composites to MP4 with camera zoom/pan effects.

## Prerequisites

- `asciinema` — terminal session recording
- `agg` — asciinema GIF renderer ([github.com/asciinema/agg](https://github.com/asciinema/agg))
- `ffmpeg` — video encoding
- Python packages: `pillow`, `numpy`, `pyyaml`, `pydantic`

## Quick Start

```bash
# Preview in terminal (no recording)
python -m demo demo/scripts/example.yaml

# Record and render to MP4
python -m demo demo/scripts/example.yaml -o demo.mp4

# With a specific skin
python -m demo demo/scripts/example.yaml -o demo.mp4 --skin ares

# Dry run (print commands without executing)
python -m demo demo/scripts/example.yaml -o demo.mp4 --dry-run
```

## Verify your font setup before authoring

Skins use Braille, dingbats, and box-drawing glyphs that not every monospace font covers. Before authoring a screenplay or picking a custom `font_family` / `skin`, run:

```
python -m moirae fonts test
```

This renders a 5-second smoke clip exercising every glyph category the built-in skins reach for. Inspect the result for tofu boxes (□ with ?). If any glyph is broken, swap the font or skin before you spend minutes on a full render.

```
python -m moirae fonts test --font "Menlo"   # check a specific font
python -m moirae fonts test --skin ares      # check a specific skin's glyphs
```

For a quick preview of a real screenplay's first few seconds (avoids waiting for a full render to spot layout issues):

```
python -m moirae preview my-screenplay.yaml --duration 8
```

## CLI Options

| Flag | Description |
|------|-------------|
| `script` | Path to YAML screenplay file (required) |
| `-o, --output PATH` | Output MP4 path. If omitted, defaults to `--play` mode |
| `--play` | Preview in terminal without recording |
| `--skin NAME` | Override skin (e.g. `ares`, `mono`, `slate`, `poseidon`) |
| `--typing-speed FLOAT` | Override typing speed (seconds per character) |
| `--dry-run` | Print pipeline commands without executing |
| `--debug-camera` | Print resolved camera keyframe timeline |

## Screenplay YAML Schema

```yaml
title: "My Demo"
skin: "default"              # Skin name (default, ares, mono, slate, poseidon, sisyphus, charizard)
typing_speed: 0.04           # Seconds per character
pause_between: 1.0           # Default pause between scenes

output:
  width: 2560                # Render resolution (2x for sharp text)
  height: 1440
  final_width: 1280          # Output resolution
  final_height: 720
  fps: 30
  font_size: 22
  font_family: "Menlo"
  theme: "github-light"      # agg theme override (omit to use skin default)
  bg_image: "path/to/bg.jpg" # Background image (optional)
  bg_color: "#fdf1de"        # Background fill color (optional)
  bg_opacity: 0.85           # Terminal opacity over background (0.0–1.0)
  terminal_cols: 200         # Terminal grid width  (>= 80, <= 400)
  terminal_rows: 120         # Terminal grid height (>= 40, <= 500)

scenes:
  # ... scene list (see Scene Types below)
```

### Terminal grid sizing

`output.terminal_rows` controls how tall the underlying asciinema buffer is. The default is 120 rows — enough for ~5–10 Q+A exchanges before older content scrolls off the top. If your screenplay has many scenes, bump `terminal_rows` so the conversation doesn't overflow the buffer (each Q+A is roughly 12–15 rows). Hitting the buffer top causes the terminal to scroll, which pins the auto-tracking camera at the bottom of the visible viewport. `terminal_cols` defaults to 200; widen it only if a single line of content needs more horizontal room than the default.

### Theme and Skin Interaction

The terminal rendering theme comes from the skin's `terminal_theme` field by default. All built-in skins use `github-light`. To override for a specific video, set `output.theme` in the YAML — this takes precedence over the skin.

The compositor's background detection color is derived automatically from the theme via `AGG_THEME_BG` in `skin_engine.py`. Available agg themes: `asciinema`, `dracula`, `github-dark`, `github-light`, `kanagawa`, `kanagawa-dragon`, `kanagawa-light`, `monokai`, `nord`, `solarized-dark`, `solarized-light`, `gruvbox-dark`.

## Scene Types

### Action Scenes

```yaml
# Clear terminal
- action: clear

# Type a shell command with output
- action: type_command
  prefix: "~ $ "
  command: "hermes"
  output: "Starting Hermes Agent..."   # optional

# Show the agent banner
- action: banner
  model: "deephermes-3-llama-3.1-8b"
  context: "128K"
  session_id: "d8f2a1c4"
  tools_count: 24
  skills_count: 42

# Pause
- action: pause
  duration: 1.5

# Print styled text
- action: print
  text: "Hello world"
  color: "#FFD700"

# Standalone camera move
- action: camera
  zoom: 1.0
  duration: 0.8
  ease: "ease-out"
```

### Conversation Scenes

```yaml
- user: "What are the latest developments in AI agents?"
  thinking_time: 3.5
  typing_speed: 0.03          # override per-scene
  pre_pause: 0.5
  post_pause: 0.5
  tools:
    - icon: "🔍"
      verb: "search"
      detail: '"AI agents 2026"'
      duration: "2.1s"
      delay: 0.3
  response: |
    Here are the key developments...
  response_label: " ⚕ Hermes "

  # Camera directives (optional)
  camera:
    zoom: 1.8
    x: 0.5                    # Normalized 0.0–1.0
    y: 0.15
    at: "user_start"          # Timing marker
    duration: 0.8
    ease: "ease-in-out"
  camera_response:
    zoom: 1.4
    y: 0.65
    at: "response_start"
    duration: 0.5
    ease: "ease-in-out"
```

### Camera Directives

Camera directives control zoom and pan during the video. They can be:
- Attached to conversation scenes (`camera:` and `camera_response:`)
- Standalone action scenes (`action: camera`)

| Field | Default | Description |
|-------|---------|-------------|
| `zoom` | 1.0 | Zoom multiplier (1.0 = full frame, 2.0 = 2x zoom) |
| `x` | 0.5 | Horizontal center (0.0 = left, 1.0 = right) |
| `y` | 0.5 | Vertical center (0.0 = top, 1.0 = bottom) |
| `auto_y` | false | Compute y from cursor position at marker time |
| `at` | "scene_start" | Timing marker (`user_start`, `response_start`, `scene_start`) |
| `duration` | 0.5 | Transition time in seconds |
| `ease` | "ease-in-out" | Easing: `linear`, `ease-in`, `ease-out`, `ease-in-out` |

## Pipeline Stages

1. **Record** — `asciinema rec` runs the player in `--play` mode inside a PTY
2. **Render** — `agg` converts the `.cast` recording to a high-res GIF
3. **Composite** — Python reads GIF frames, applies camera crop per-frame, blends background, pipes to `ffmpeg` for MP4 encoding

## Example Screenplays

- `demo/scripts/example.yaml` — Basic conversation demo
- `demo/scripts/example_with_camera.yaml` — Camera zoom/pan effects
- `demo/scripts/hermes_capabilities.yaml` — Full production demo with background image

## Key Files

| File | Role |
|------|------|
| `demo/__main__.py` | CLI entry point |
| `demo/schema.py` | Pydantic models for screenplay YAML |
| `demo/player.py` | Terminal playback engine |
| `demo/pipeline.py` | Orchestrates record → render → composite |
| `demo/recorder.py` | asciinema + agg subprocess wrappers |
| `demo/compositor.py` | GIF frame reader, camera crop, ffmpeg encoding |
| `demo/camera.py` | Keyframe resolution and interpolation |
| `demo/scenes/` | Scene handlers (conversation, action) |
