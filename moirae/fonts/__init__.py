"""Bundled fonts + a one-time install-or-warn helper.

Moirae ships a Nerd Font (JetBrains Mono Nerd Font Mono — OFL) so renders
that hit non-ASCII glyphs (Braille caduceus, dingbats, box drawing) don't
tofu out for fresh installs. agg uses fontconfig to resolve font families,
so the font has to live in a directory fontconfig watches. The cleanest
cross-platform path is to copy it into the user's standard font directory
on first use.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

BUNDLED_FONT_NAME = "JetBrainsMono Nerd Font Mono"
BUNDLED_FONT_FILE = "JetBrainsMonoNerdFontMono-Regular.ttf"
_FONT_DIR = Path(__file__).resolve().parent
_BUNDLED_PATH = _FONT_DIR / BUNDLED_FONT_FILE


def _user_font_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Fonts"
    if sys.platform.startswith("win"):
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "Microsoft/Windows/Fonts"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "fonts"


def _font_resolved_via_fontconfig(family: str) -> bool:
    try:
        out = subprocess.run(
            ["fc-match", "--format=%{family}", family],
            capture_output=True, text=True, timeout=5,
        )
        # fc-match always returns success and falls back silently to a different
        # family if the requested one isn't installed. So the only signal is
        # whether the resolved family contains the requested name.
        return out.returncode == 0 and family.lower() in out.stdout.lower()
    except (FileNotFoundError, subprocess.SubprocessError):
        return False


def _refresh_fontconfig_cache() -> None:
    try:
        subprocess.run(["fc-cache", "-f"], capture_output=True, timeout=30)
    except (FileNotFoundError, subprocess.SubprocessError):
        pass


_ENSURE_DONE = False


def ensure_bundled_font_installed(*, verbose: bool = True) -> bool:
    """Make the bundled Nerd Font discoverable by fontconfig.

    Idempotent and cheap to call: bails out as soon as the font is already
    resolvable. On first run, copies the bundled TTF into the platform's
    user fonts directory and refreshes the fontconfig cache.

    Returns True if the font is available after this call, False otherwise.
    """
    global _ENSURE_DONE
    if _ENSURE_DONE:
        return True

    if _font_resolved_via_fontconfig(BUNDLED_FONT_NAME):
        _ENSURE_DONE = True
        return True

    if not _BUNDLED_PATH.exists():
        if verbose:
            print(
                f"[moirae] WARNING: bundled font missing at {_BUNDLED_PATH}; "
                f"renders may show tofu boxes for Braille/symbol glyphs.",
                file=sys.stderr,
            )
        return False

    target_dir = _user_font_dir()
    target = target_dir / _BUNDLED_PATH.name
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            shutil.copy2(_BUNDLED_PATH, target)
            if verbose:
                print(
                    f"[moirae] Installed bundled Nerd Font to {target} "
                    f"(needed for full glyph coverage in renders).",
                    file=sys.stderr,
                )
        _refresh_fontconfig_cache()
    except Exception as exc:
        if verbose:
            print(
                f"[moirae] WARNING: could not install bundled Nerd Font "
                f"to {target_dir}: {exc}. Renders may show tofu boxes for "
                f"Braille/symbol glyphs. Manual install: copy "
                f"{_BUNDLED_PATH} into your system fonts directory.",
                file=sys.stderr,
            )
        return False

    if _font_resolved_via_fontconfig(BUNDLED_FONT_NAME):
        _ENSURE_DONE = True
        return True

    if verbose:
        print(
            f"[moirae] WARNING: bundled font copied to {target} but "
            f"fontconfig still does not resolve '{BUNDLED_FONT_NAME}'. "
            f"You may need to log out / log back in or run `fc-cache -f` "
            f"manually.",
            file=sys.stderr,
        )
    return False
