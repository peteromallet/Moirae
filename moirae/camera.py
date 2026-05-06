"""Camera system: resolve keyframes from screenplay + timing, interpolate zoom/pan.

Camera coordinates:
- x, y: normalized 0.0–1.0 (viewport center position)
- zoom: multiplier (1.0 = full frame, 2.0 = 2x zoom)

Used by compositor.py to crop each frame during encoding.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from moirae.schema import ConversationScene, ActionScene, Screenplay
from moirae.scenes import TimingManifest


@dataclass
class CameraKeyframe:
    """Resolved camera keyframe with absolute timestamp."""
    t: float       # Absolute time in seconds
    zoom: float    # 1.0 = full frame
    x: float       # 0.0–1.0
    y: float       # 0.0–1.0
    duration: float  # Transition time to reach this state
    ease: str      # "linear", "ease-in", "ease-out", "ease-in-out"


def _resolve_auto_y(marker_name: str, timing, total_rows: int) -> Optional[float]:
    """Compute y from cursor position. Centers the viewport on the cursor."""
    cursor_key = f"_cursor_{marker_name}"
    cursor_row = timing.markers.get(cursor_key)
    if cursor_row is None:
        return None
    return min(cursor_row / total_rows, 1.0) if total_rows > 0 else 0.5


def _auto_track_keyframes(
    keyframes: List[CameraKeyframe],
    cam,
    timing,
    total_rows: int,
) -> None:
    """Emit tracking keyframes that follow the cursor through the exchange.

    Three keyframes per Q+A:
      1. user_end  — pan to the cursor's row right after the user prompt is done.
      2. response_start — hold (no jump). We deliberately do NOT pre-position
         the camera at the predicted Q+A midpoint; that creates a visible
         lead where the camera moves before the response actually renders.
      3. response_end — pan smoothly to the cursor's row at response end,
         with duration spanning the entire response render. The compositor
         interpolates linearly through this window, so the active line
         stays roughly under the viewport center as it gets typed.
    """
    user_end_t = timing.markers.get("user_end")
    user_end_row = timing.markers.get("_cursor_user_end")
    response_start_t = timing.markers.get("response_start")
    response_end_t = timing.markers.get("response_end")
    response_end_row = timing.markers.get("_cursor_response_end")

    if user_end_t is not None and user_end_row is not None:
        keyframes.append(CameraKeyframe(
            t=user_end_t,
            zoom=cam.zoom, x=cam.x,
            y=min(user_end_row / total_rows, 1.0) if total_rows > 0 else 0.5,
            duration=0.3, ease="ease-out",
        ))

    if (
        response_start_t is not None
        and response_end_t is not None
        and response_end_row is not None
    ):
        track_duration = max(response_end_t - response_start_t, 0.3)
        keyframes.append(CameraKeyframe(
            t=response_end_t,
            zoom=cam.zoom, x=cam.x,
            y=min(response_end_row / total_rows, 1.0) if total_rows > 0 else 0.5,
            duration=track_duration,
            ease="linear",
        ))


def resolve_keyframes(
    screenplay: Screenplay,
    manifest: TimingManifest,
    total_rows: int = 120,
) -> List[CameraKeyframe]:
    """Resolve camera directives from screenplay scenes against timing manifest.

    Returns a sorted list of CameraKeyframes with absolute timestamps.
    """
    keyframes: List[CameraKeyframe] = []
    scenes = screenplay.parsed_scenes()

    for i, scene in enumerate(scenes):
        if i >= len(manifest.scenes):
            break
        timing = manifest.scenes[i]

        if isinstance(scene, ConversationScene):
            if scene.camera:
                cam = scene.camera
                t = _resolve_marker_time(cam.at, timing)
                if t is not None:
                    y = cam.y
                    if cam.auto_y:
                        auto = _resolve_auto_y(cam.at, timing, total_rows)
                        if auto is not None:
                            y = auto
                    keyframes.append(CameraKeyframe(
                        t=t, zoom=cam.zoom,
                        x=cam.x, y=y,
                        duration=cam.duration, ease=cam.ease,
                    ))

                    # Auto-tracking: emit a keyframe at user_end to follow
                    # cursor after submit (unless explicit camera_response)
                    if cam.auto_y and not scene.camera_response:
                        _auto_track_keyframes(
                            keyframes, cam, timing, total_rows,
                        )

            if scene.camera_response:
                t = _resolve_marker_time(scene.camera_response.at, timing)
                if t is None:
                    t = timing.markers.get("response_start", timing.end_t)
                if t is not None:
                    y = scene.camera_response.y
                    if scene.camera_response.auto_y:
                        # Use exchange midpoint so the Q+A pair is centered,
                        # not just the cursor before the response renders
                        top = timing.markers.get("_cursor_user_start")
                        bottom = timing.markers.get("_cursor_response_end")
                        if top is not None and bottom is not None:
                            y = min((top + bottom) / 2.0 / total_rows, 1.0)
                        else:
                            auto = _resolve_auto_y(
                                scene.camera_response.at, timing, total_rows,
                            )
                            if auto is not None:
                                y = auto
                    keyframes.append(CameraKeyframe(
                        t=t, zoom=scene.camera_response.zoom,
                        x=scene.camera_response.x, y=y,
                        duration=scene.camera_response.duration,
                        ease=scene.camera_response.ease,
                    ))

        elif isinstance(scene, ActionScene) and scene.action == "camera":
            t = timing.markers.get("camera", timing.start_t)
            y = timing.markers.get("_y", 0.5)
            if timing.markers.get("_auto_y"):
                auto = _resolve_auto_y("camera", timing, total_rows)
                if auto is not None:
                    y = auto
            keyframes.append(CameraKeyframe(
                t=t,
                zoom=timing.markers.get("_zoom", 1.0),
                x=timing.markers.get("_x", 0.5),
                y=y,
                duration=timing.markers.get("_duration", 0.5),
                ease=scene.ease or "ease-in-out",
            ))

    keyframes.sort(key=lambda k: k.t)

    # Cull auto-tracking keyframes that would be swallowed by the next
    # transition (their duration <= 0.3s identifies them).
    culled: List[CameraKeyframe] = []
    for i, kf in enumerate(keyframes):
        if i + 1 < len(keyframes):
            next_kf = keyframes[i + 1]
            if kf.duration <= 0.3 and (next_kf.t - next_kf.duration) < kf.t:
                continue
        culled.append(kf)
    keyframes = culled

    # Ensure there's always a starting keyframe at t=0.
    # The default y centers the viewport on the top portion of the grid so
    # the first ~visible_rows of content land on screen at zoom 1.0. With
    # the new default 120-row grid (vs the old 80) the previous y=0.34
    # would crop the banner; recompute relative to the grid size.
    if not keyframes or keyframes[0].t > 0.01:
        keyframes.insert(0, CameraKeyframe(
            t=0.0, zoom=1.0, x=0.5,
            y=_default_camera_y(total_rows),
            duration=0.0, ease="linear",
        ))

    return keyframes


def _default_camera_y(total_rows: int) -> float:
    """Default y for an unconfigured opening keyframe.

    Centers on the top portion of the grid so the first ~visible_rows of
    content (banner / first prompt) land on screen at zoom 1.0.

    Heuristic: at typical agg char metrics (~17 px wide, ~40 px tall) and a
    200-col × N-row grid, the GIF is taller than 16:9 once N ≳ 48. The
    compositor then crops to the output aspect ratio, leaving roughly 48
    rows visible at zoom 1.0. We pin the viewport center at (visible/2),
    so the topmost ~visible_rows of the grid stay on screen.

    For grids smaller than the visible window, fall back to y=0.5 (centred).
    """
    if total_rows <= 0:
        return 0.5
    visible_rows_estimate = 48
    if total_rows <= visible_rows_estimate:
        return 0.5
    return (visible_rows_estimate / 2.0) / total_rows


def _resolve_marker_time(marker: str, timing) -> Optional[float]:
    """Resolve a marker name to an absolute time from scene timing."""
    if marker == "scene_start":
        return timing.start_t
    if marker == "scene_end":
        return timing.end_t
    return timing.markers.get(marker, timing.start_t)


# ── Easing functions ────────────────────────────────────────────────────

def _ease(t: float, ease_type: str) -> float:
    """Apply easing to normalized t (0.0–1.0)."""
    t = max(0.0, min(1.0, t))
    if ease_type == "linear":
        return t
    elif ease_type == "ease-in":
        return t * t
    elif ease_type == "ease-out":
        return 1.0 - (1.0 - t) * (1.0 - t)
    elif ease_type == "ease-in-out":
        # Smoothstep
        return t * t * (3.0 - 2.0 * t)
    return t


def interpolate_keyframes(
    keyframes: List[CameraKeyframe], t: float
) -> tuple[float, float, float]:
    """Interpolate zoom, x, y at time t given sorted keyframes."""
    if not keyframes:
        return 1.0, 0.5, 0.5

    # Before first keyframe
    if t <= keyframes[0].t:
        kf = keyframes[0]
        return kf.zoom, kf.x, kf.y

    # Find the active transition
    for i in range(1, len(keyframes)):
        kf = keyframes[i]
        prev = keyframes[i - 1]
        transition_start = kf.t - kf.duration
        if t <= kf.t:
            if t < transition_start:
                return prev.zoom, prev.x, prev.y
            # In transition
            progress = (t - transition_start) / kf.duration if kf.duration > 0 else 1.0
            eased = _ease(progress, kf.ease)
            zoom = prev.zoom + (kf.zoom - prev.zoom) * eased
            x = prev.x + (kf.x - prev.x) * eased
            y = prev.y + (kf.y - prev.y) * eased
            return zoom, x, y

    # After last keyframe
    last = keyframes[-1]
    return last.zoom, last.x, last.y


# ── Debug report ───────────────────────────────────────────────────────

def _viewport_bounds(
    kf: CameraKeyframe, total_rows: int, total_cols: int,
    gif_w: int = 0, gif_h: int = 0,
    output_w: int = 1280, output_h: int = 720,
):
    """Compute visible row/col range, accounting for output aspect ratio.

    The compositor crops to the output aspect ratio, which can reduce
    the visible height when the GIF is taller than 16:9.
    """
    output_ar = output_w / output_h if output_h > 0 else 1.78

    if gif_w > 0 and gif_h > 0:
        # Replicate the compositor's aspect ratio logic
        crop_h = gif_h / kf.zoom
        crop_w = crop_h * output_ar
        if crop_w > gif_w / kf.zoom:
            crop_w = gif_w / kf.zoom
            crop_h = crop_w / output_ar
        char_h = gif_h / total_rows
        char_w = gif_w / total_cols
        vis_rows = crop_h / char_h
        vis_cols = crop_w / char_w
    else:
        # Fallback without GIF dimensions
        vis_rows = total_rows / kf.zoom
        vis_cols = total_cols / kf.zoom

    center_row = kf.y * total_rows
    center_col = kf.x * total_cols
    top = max(0, center_row - vis_rows / 2)
    bot = min(total_rows, center_row + vis_rows / 2)
    left = max(0, center_col - vis_cols / 2)
    right = min(total_cols, center_col + vis_cols / 2)
    return int(top), int(bot), int(left), int(right)


def _load_cast_screens(cast_path, timestamps: List[float], rows: int, cols: int):
    """Replay a .cast file through pyte, capturing screen state at each timestamp."""
    import json
    import pyte

    screen = pyte.Screen(cols, rows)
    stream = pyte.Stream(screen)

    events = []
    with open(cast_path) as f:
        for i, line in enumerate(f):
            if i == 0:
                continue
            try:
                ts, _type, data = json.loads(line)
                events.append((ts, data))
            except (json.JSONDecodeError, ValueError):
                continue

    sorted_ts = sorted(set(timestamps))
    ts_to_screen: dict[float, list[str]] = {}
    event_idx = 0

    for target_t in sorted_ts:
        while event_idx < len(events) and events[event_idx][0] <= target_t:
            stream.feed(events[event_idx][1])
            event_idx += 1
        ts_to_screen[target_t] = [
            screen.buffer[row_idx] for row_idx in range(rows)
        ]

    result = []
    for t in timestamps:
        closest = min(sorted_ts, key=lambda st: abs(st - t))
        result.append(ts_to_screen[closest])
    return result


def _render_row(buffer_row: dict, left: int, right: int, max_width: int) -> str:
    """Extract visible text from a pyte buffer row within column bounds."""
    chars = []
    for col in range(left, min(right, left + max_width)):
        char = buffer_row.get(col)
        if char:
            chars.append(char.data if char.data != " " or chars else " ")
        else:
            chars.append(" ")
    return "".join(chars).rstrip()


def debug_camera_report(
    keyframes: List[CameraKeyframe],
    total_rows: int = 80,
    total_cols: int = 200,
    cast_path=None,
    gif_path=None,
    output_w: int = 1280,
    output_h: int = 720,
    preview_width: int = 72,
) -> str:
    """Generate a visual camera timeline with terminal content previews."""
    screens = None
    if cast_path and cast_path.exists():
        try:
            screens = _load_cast_screens(
                cast_path, [kf.t for kf in keyframes], total_rows, total_cols
            )
        except Exception:
            screens = None

    # Load GIF dimensions for accurate aspect-ratio viewport calculation
    gif_w, gif_h = 0, 0
    if gif_path and gif_path.exists():
        try:
            from PIL import Image
            img = Image.open(gif_path)
            gif_w, gif_h = img.size
            img.close()
        except Exception:
            pass

    lines = []
    ar_info = ""
    if gif_w and gif_h:
        ar_info = f", GIF {gif_w}×{gif_h}, output {output_w}×{output_h}"
    lines.append(
        f"Camera Debug  ({len(keyframes)} keyframes, "
        f"{total_rows} rows × {total_cols} cols{ar_info})"
    )

    for i, kf in enumerate(keyframes):
        top, bot, left, right = _viewport_bounds(
            kf, total_rows, total_cols,
            gif_w=gif_w, gif_h=gif_h, output_w=output_w, output_h=output_h,
        )
        vis_rows = bot - top
        vis_cols = right - left

        lines.append("")
        lines.append("═" * (preview_width + 4))

        if i > 0:
            prev = keyframes[i - 1]
            parts = []
            dy = (kf.y - prev.y) * total_rows
            if abs(dy) > 0.5:
                parts.append(f"pan {'↓' if dy > 0 else '↑'} {abs(dy):.0f} rows")
            dx = (kf.x - prev.x) * total_cols
            if abs(dx) > 0.5:
                parts.append(f"pan {'→' if dx > 0 else '←'} {abs(dx):.0f} cols")
            if abs(kf.zoom - prev.zoom) > 0.01:
                parts.append(f"zoom {prev.zoom:.1f}x → {kf.zoom:.1f}x")
            if parts:
                lines.append(f"  ◆ {', '.join(parts)}  ({kf.duration:.1f}s {kf.ease})")
            else:
                lines.append(f"  ◆ no movement  ({kf.duration:.1f}s)")

        lines.append(
            f"  t={kf.t:.2f}s  │  {kf.zoom:.1f}x zoom  │  "
            f"rows {top}–{bot}  cols {left}–{right}  │  "
            f"showing {vis_rows}×{vis_cols} of {total_rows}×{total_cols}"
        )

        if i < len(keyframes) - 1:
            next_kf = keyframes[i + 1]
            hold = (next_kf.t - next_kf.duration) - kf.t
            if hold > 0.01:
                lines.append(f"  holds {hold:.1f}s")
            else:
                lines.append(f"  immediately transitions →")

        if screens:
            screen_buf = screens[i]
            crop_width = min(vis_cols, preview_width)
            lines.append(f"  ┌{'─' * crop_width}┐")
            for row_idx in range(top, bot):
                if row_idx >= len(screen_buf):
                    break
                row_text = _render_row(screen_buf[row_idx], left, right, crop_width)
                lines.append(f"  │{row_text:<{crop_width}}│")
            lines.append(f"  └{'─' * crop_width}┘")

    lines.append("")
    return "\n".join(lines)
