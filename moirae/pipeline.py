"""Orchestrates the full demo video pipeline: record → render → composite."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from moirae.schema import Screenplay


def run_pipeline(
    screenplay: Screenplay,
    output_path: Path,
    skin_override: Optional[str] = None,
    typing_speed_override: Optional[float] = None,
    dry_run: bool = False,
    debug_camera: bool = False,
) -> None:
    """Run the full recording + rendering pipeline.

    Steps:
    1. Record terminal session with asciinema (running player in --play mode)
    2. Render .cast → GIF with agg at high resolution
    3. Composite: read GIF frames, apply camera crop in Python, encode to MP4
    """
    from moirae.compositor import composite_frames
    from moirae.recorder import render_agg, record_asciinema
    from moirae.skin_engine import load_skin, AGG_THEME_BG

    out = screenplay.output

    # Resolve terminal theme from skin (screenplay YAML can override)
    skin = load_skin(skin_override or screenplay.skin)
    terminal_theme = out.theme if out.theme is not None else skin.terminal_theme
    terminal_bg_rgb = AGG_THEME_BG.get(terminal_theme, skin.terminal_bg_rgb)
    work_dir = output_path.parent
    stem = output_path.stem

    cast_path = work_dir / f"{stem}.cast"
    gif_path = work_dir / f"{stem}.gif"
    timing_path = work_dir / f"{stem}_timing.json"

    # Build the player command
    player_cmd = [
        sys.executable, "-m", "moirae",
        "--play",  # Terminal playback mode
        "--timing", str(timing_path),
    ]
    if skin_override:
        player_cmd.extend(["--skin", skin_override])
    elif screenplay.skin != "default":
        player_cmd.extend(["--skin", screenplay.skin])
    if typing_speed_override:
        player_cmd.extend(["--typing-speed", str(typing_speed_override)])

    # We need the screenplay path — it's set by __main__ on the screenplay object
    script_path = getattr(screenplay, "_source_path", None)
    if script_path:
        player_cmd.append(str(script_path))
    else:
        raise RuntimeError("Screenplay has no _source_path — cannot record")

    # Terminal grid dimensions for asciinema recording. Configurable via the
    # screenplay's `output.terminal_cols` / `output.terminal_rows`. The
    # default 200×120 grid leaves enough vertical room for ~5–10 Q&A
    # exchanges before the scrollback buffer starts dropping rows (which
    # would otherwise pin the camera to the bottom).
    cols = out.terminal_cols
    rows = out.terminal_rows

    if dry_run:
        _print_dry_run(
            player_cmd, cast_path, gif_path, timing_path,
            out, screenplay, output_path,
        )
        return

    # Step 1: Record with asciinema
    print(f"[1/3] Recording terminal session → {cast_path}")
    record_asciinema(
        command=player_cmd,
        cast_path=cast_path,
        cols=cols,
        rows=rows,
    )

    # Step 2: Render to GIF with agg
    print(f"[2/3] Rendering → {gif_path}")
    render_agg(
        cast_path=cast_path,
        gif_path=gif_path,
        font_size=out.font_size,
        font_family=out.font_family,
        fps=out.fps,
        theme=terminal_theme,
    )

    # Step 3: Resolve camera keyframes and composite
    keyframes = _resolve_camera_keyframes(screenplay, timing_path, total_rows=rows)

    if debug_camera:
        from moirae.camera import debug_camera_report
        print()
        print(debug_camera_report(
            keyframes, total_rows=rows, total_cols=cols,
            cast_path=cast_path, gif_path=gif_path,
            output_w=out.final_width, output_h=out.final_height,
        ))

    n_camera = sum(1 for kf in keyframes if kf.zoom != 1.0 or kf.x != 0.5 or kf.y != 0.5)
    print(f"[3/3] Compositing → {output_path} ({n_camera} camera keyframe(s))")

    composite_frames(
        gif_path=gif_path,
        output_path=output_path,
        keyframes=keyframes,
        output_w=out.final_width,
        output_h=out.final_height,
        fps=out.fps,
        bg_image=out.bg_image,
        bg_opacity=out.bg_opacity,
        bg_color=out.bg_color,
        theme_bg_color=terminal_bg_rgb,
    )

    print(f"Done! Output: {output_path}")


def _resolve_camera_keyframes(screenplay, timing_path, total_rows=80):
    """Load timing manifest and resolve camera keyframes."""
    from moirae.camera import CameraKeyframe, resolve_keyframes
    from moirae.scenes import TimingManifest, SceneTiming

    if not timing_path.exists():
        # No timing data — return a single default keyframe (no zoom)
        return [CameraKeyframe(t=0.0, zoom=1.0, x=0.5, y=0.5, duration=0.0, ease="linear")]

    import json
    timing_data = json.loads(timing_path.read_text())
    manifest = TimingManifest(total_duration=timing_data.get("total_duration", 0))
    for sd in timing_data.get("scenes", []):
        st = SceneTiming(
            index=sd["index"],
            scene_type=sd["type"],
            action=sd.get("action"),
            start_t=sd["start_t"],
            end_t=sd["end_t"],
            markers=sd.get("markers", {}),
        )
        manifest.scenes.append(st)

    return resolve_keyframes(screenplay, manifest, total_rows=total_rows)


def _print_dry_run(
    player_cmd, cast_path, gif_path, timing_path,
    out, screenplay, output_path,
):
    """Print the commands that would be run without executing them."""
    print("=== DRY RUN ===\n")

    print("Step 1: Record with asciinema")
    print(
        f"  asciinema rec --overwrite "
        f"--cols={out.terminal_cols} --rows={out.terminal_rows} \\"
    )
    print(f"    --command '{' '.join(player_cmd)}' \\")
    print(f"    {cast_path}\n")

    print("Step 2: Render with agg")
    print(f"  agg --font-size={out.font_size} --font-family='{out.font_family}' \\")
    print(f"    --fps-cap={out.fps} {cast_path} {gif_path}\n")

    scenes = screenplay.parsed_scenes()
    camera_count = sum(
        1 for s in scenes
        if hasattr(s, "camera") and s.camera
        or hasattr(s, "camera_response") and s.camera_response
        or (hasattr(s, "action") and s.action == "camera")
    )

    print("Step 3: Composite (Python frame-by-frame crop + ffmpeg encode)")
    print(f"  {camera_count} camera directive(s) in screenplay")
    print("  Read GIF frames → crop per camera keyframe → pipe to ffmpeg")
    print(f"  Output: {out.final_width}x{out.final_height} @ {out.fps}fps → {output_path}")
