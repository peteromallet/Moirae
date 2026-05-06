"""Frame-by-frame compositor: read GIF frames, apply camera crop, encode to MP4.

Replaces the broken ffmpeg-expression approach. Camera interpolation happens
in Python (PIL crop) where we have full control, then raw frames are piped
to ffmpeg for encoding.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
from PIL import Image

from moirae.camera import CameraKeyframe, interpolate_keyframes


def _parse_hex_color(hex_str: str) -> Tuple[int, int, int]:
    """Parse '#rrggbb' to (r, g, b)."""
    hex_str = hex_str.lstrip("#")
    return (int(hex_str[0:2], 16), int(hex_str[2:4], 16), int(hex_str[4:6], 16))


def _load_background(
    bg_image: Optional[str],
    bg_color: Optional[str],
    bg_opacity: float,
    width: int,
    height: int,
) -> Optional[Image.Image]:
    """Build the background layer: image with color overlay at bg_opacity.

    bg_opacity controls how much the color covers the image.
    0.9 = 90% color, 10% image (image barely visible).
    """
    if not bg_image and not bg_color:
        return None

    color = _parse_hex_color(bg_color) if bg_color else (0, 0, 0)

    if bg_image:
        img = Image.open(bg_image).convert("RGB")
        # Auto-crop black letterbox bars (common in video thumbnails)
        img_arr = np.array(img)
        top_crop, bot_crop = 0, img_arr.shape[0]
        for r in range(img_arr.shape[0]):
            if img_arr[r].mean() > 30:
                top_crop = r
                break
        for r in range(img_arr.shape[0] - 1, 0, -1):
            if img_arr[r].mean() > 30:
                bot_crop = r + 1
                break
        if top_crop > 0 or bot_crop < img_arr.shape[0]:
            img = img.crop((0, top_crop, img.width, bot_crop))
        # Resize to cover (crop-to-fill) maintaining aspect ratio,
        # instead of stretching to fit the GIF dimensions
        img_ar = img.width / img.height
        target_ar = width / height
        if img_ar > target_ar:
            # Image is wider — scale by height, crop width
            new_h = height
            new_w = int(height * img_ar)
        else:
            # Image is taller — scale by width, crop height
            new_w = width
            new_h = int(width / img_ar)
        img = img.resize((new_w, new_h), Image.LANCZOS)
        # Center-crop to target dimensions
        left = (new_w - width) // 2
        top = (new_h - height) // 2
        img = img.crop((left, top, left + width, top + height))
        # Blend: color at bg_opacity over the image
        img_arr = np.array(img, dtype=np.float32)
        color_arr = np.array(color, dtype=np.float32)
        blended = img_arr * (1.0 - bg_opacity) + color_arr * bg_opacity
        return Image.fromarray(blended.clip(0, 255).astype(np.uint8))
    else:
        return Image.new("RGB", (width, height), color)


def _blend_over_background(
    frame: Image.Image,
    bg: Image.Image,
    theme_bg_color: Tuple[int, int, int] = (255, 255, 255),
) -> Image.Image:
    """Replace terminal background pixels with the composited background.

    Detects terminal background color and swaps those pixels for the bg layer.
    Text/content pixels are kept as-is.
    """
    fg = np.array(frame, dtype=np.float32)
    bg_arr = np.array(bg.resize(frame.size, Image.LANCZOS), dtype=np.float32)

    # Mask: pixels close to the terminal theme background
    r, g, b = theme_bg_color
    diff = np.abs(fg - np.array([r, g, b], dtype=np.float32))
    is_bg = np.all(diff < 30, axis=2)[..., np.newaxis]

    # Swap: bg pixels → composited background, content pixels → keep
    blended = np.where(is_bg, bg_arr, fg)
    return Image.fromarray(blended.clip(0, 255).astype(np.uint8))


def composite_frames(
    gif_path: Path,
    output_path: Path,
    keyframes: List[CameraKeyframe],
    output_w: int,
    output_h: int,
    fps: int = 30,
    bg_image: Optional[str] = None,
    bg_opacity: float = 1.0,
    bg_color: Optional[str] = None,
    theme_bg_color: Tuple[int, int, int] = (255, 255, 255),
) -> None:
    """Read GIF frames, apply camera crop per-frame, encode to MP4.

    Streams frames through ffmpeg via stdin pipe — memory usage is O(1 frame).
    """
    # Ensure even dimensions for H.264
    output_w &= ~1
    output_h &= ~1

    # Quality preset: env-var hook so smoke / preview subcommands can speed
    # this stage up without changing the public API.
    import os
    preset = os.environ.get("MOIRAE_X264_PRESET", "fast")
    crf = os.environ.get("MOIRAE_X264_CRF", "20")

    ffmpeg_cmd = [
        "ffmpeg", "-y",
        "-f", "rawvideo",
        "-pix_fmt", "rgb24",
        "-s", f"{output_w}x{output_h}",
        "-r", str(fps),
        "-i", "pipe:0",
        "-c:v", "libx264",
        "-preset", preset,
        "-crf", crf,
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(output_path),
    ]

    stderr_log = open(str(output_path) + ".ffmpeg.log", "wb")
    proc = subprocess.Popen(
        ffmpeg_cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=stderr_log,
    )

    # Load background at GIF resolution so it zooms with the terminal
    gif_probe = Image.open(gif_path)
    gif_w, gif_h = gif_probe.size
    gif_probe.close()
    bg = _load_background(bg_image, bg_color, bg_opacity, gif_w, gif_h)
    _process_gif(proc, gif_path, keyframes, output_w, output_h, fps, bg, theme_bg_color)
    proc.stdin.close()
    proc.wait()
    stderr_log.close()

    if proc.returncode != 0:
        try:
            with open(str(output_path) + ".ffmpeg.log", "rb") as f:
                stderr = f.read()
        except Exception:
            stderr = b""
        raise RuntimeError(
            f"ffmpeg encoding failed (exit {proc.returncode}):\n"
            f"{stderr.decode(errors='replace')}"
        )


def _process_gif(
    proc: subprocess.Popen,
    gif_path: Path,
    keyframes: List[CameraKeyframe],
    output_w: int,
    output_h: int,
    fps: int,
    bg: Optional[Image.Image] = None,
    theme_bg_color: Tuple[int, int, int] = (255, 255, 255),
) -> None:
    """Iterate GIF frames, crop, and write to ffmpeg stdin."""
    img = Image.open(gif_path)
    t = 0.0

    while True:
        frame = img.copy().convert("RGB")
        duration_ms = img.info.get("duration", 33)  # fallback ~30fps

        # Blend bg before camera crop so bg zooms with terminal
        if bg is not None:
            frame = _blend_over_background(frame, bg, theme_bg_color)

        zoom, cx, cy = interpolate_keyframes(keyframes, t)
        cropped = _apply_camera(frame, zoom, cx, cy, output_w, output_h)

        # Write this frame enough times to match its display duration at target fps
        n_repeats = max(1, round(duration_ms / 1000.0 * fps))
        raw = cropped.tobytes()
        for _ in range(n_repeats):
            proc.stdin.write(raw)

        t += duration_ms / 1000.0

        try:
            img.seek(img.tell() + 1)
        except EOFError:
            break


def _apply_camera(
    frame: Image.Image,
    zoom: float,
    cx: float,
    cy: float,
    output_w: int,
    output_h: int,
) -> Image.Image:
    """Crop and scale a single frame based on camera state.

    The crop region matches the output aspect ratio so resizing never stretches.
    """
    input_w, input_h = frame.size
    output_ar = output_w / output_h

    # Start with full-zoom crop, then adjust to match output aspect ratio
    crop_h = input_h / zoom
    crop_w = crop_h * output_ar

    # If crop_w exceeds input width, constrain by width instead
    if crop_w > input_w / zoom:
        crop_w = input_w / zoom
        crop_h = crop_w / output_ar

    # Center the crop on (cx, cy) in normalized coordinates
    crop_x = cx * input_w - crop_w / 2
    crop_y = cy * input_h - crop_h / 2

    # Clamp to image bounds
    crop_x = max(0, min(crop_x, input_w - crop_w))
    crop_y = max(0, min(crop_y, input_h - crop_h))

    cropped = frame.crop((
        int(crop_x),
        int(crop_y),
        int(crop_x + crop_w),
        int(crop_y + crop_h),
    ))

    return cropped.resize((output_w, output_h), Image.LANCZOS)
