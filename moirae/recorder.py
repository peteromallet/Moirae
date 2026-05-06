"""Subprocess wrappers for asciinema recording and agg rendering."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import List, Optional


def check_tool(name: str) -> Optional[str]:
    """Return the path to a CLI tool, or None if not found."""
    return shutil.which(name)


def require_tool(name: str) -> str:
    """Return the path to a CLI tool, or raise if not found."""
    path = check_tool(name)
    if path is None:
        raise RuntimeError(
            f"'{name}' not found on PATH. Install it first:\n"
            f"  asciinema: pip install asciinema\n"
            f"  agg: cargo install agg (or brew install agg)\n"
            f"  ffmpeg: brew install ffmpeg"
        )
    return path


# ── asciinema recording ─────────────────────────────────────────────────

def record_asciinema(
    command: List[str],
    cast_path: Path,
    cols: int = 200,
    rows: int = 50,
    env: Optional[dict] = None,
) -> None:
    """Record a command with asciinema, producing a .cast file.

    Args:
        command: The command to record (e.g. ["python", "-m", "demo", "script.yaml", "--play"])
        cast_path: Output path for the .cast file
        cols: Terminal columns
        rows: Terminal rows
        env: Additional environment variables
    """
    asciinema = require_tool("asciinema")

    cmd = [
        asciinema, "rec",
        "--overwrite",
        f"--cols={cols}",
        f"--rows={rows}",
        "--command", " ".join(command),
        str(cast_path),
    ]

    import os
    run_env = dict(os.environ)
    run_env["TERM"] = "xterm-256color"
    if env:
        run_env.update(env)

    result = subprocess.run(
        cmd,
        env=run_env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"asciinema recording failed (exit {result.returncode}):\n{result.stderr}"
        )
    if not cast_path.exists():
        raise RuntimeError(f"asciinema did not produce output file: {cast_path}")


# ── agg rendering ──────────────────────────────────────────────────────

def render_agg(
    cast_path: Path,
    gif_path: Path,
    font_size: int = 22,
    font_family: str = "Menlo",
    cols: Optional[int] = None,
    rows: Optional[int] = None,
    fps: int = 30,
    theme: Optional[str] = None,
) -> None:
    """Render a .cast file to a GIF using agg.

    Note: agg produces GIF. For high-quality video we use agg's raw frame
    output or convert GIF → MP4 with ffmpeg.
    """
    agg = require_tool("agg")

    # First-run: ensure the bundled Nerd Font is discoverable so the
    # fallback chain below actually has a font with Braille + dingbat coverage.
    from moirae.fonts import ensure_bundled_font_installed
    ensure_bundled_font_installed()

    # agg's --font-family is a comma-separated fallback chain (per-codepoint).
    # Append broadly-covering families so glyphs missing in the primary
    # (Braille, dingbats, box drawing) fall back to a font that has them.
    fallback_chain = [
        font_family,
        "JetBrainsMono Nerd Font Mono",
        "JetBrainsMono Nerd Font",
        "Symbols Nerd Font Mono",
        "Apple Symbols",
        "DejaVu Sans",
    ]
    seen, dedup = set(), []
    for f in fallback_chain:
        if f and f not in seen:
            seen.add(f)
            dedup.append(f)

    cmd = [
        agg,
        f"--font-size={font_size}",
        f"--font-family={','.join(dedup)}",
        f"--fps-cap={fps}",
    ]
    if theme:
        cmd.append(f"--theme={theme}")
    if cols:
        cmd.append(f"--cols={cols}")
    if rows:
        cmd.append(f"--rows={rows}")

    cmd.extend([str(cast_path), str(gif_path)])

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"agg rendering failed (exit {result.returncode}):\n{result.stderr}"
        )