"""CLI entry point.

Default invocation:    python -m moirae <screenplay.yaml> [-o out.mp4] [--play] [--skin ares]

Subcommands (detected by sniffing sys.argv[1] before argparse runs, so the
default positional invocation stays backwards-compatible):

    python -m moirae fonts test [--font NAME] [--skin NAME]
    python -m moirae preview <screenplay> [--duration N] [-o PATH]
"""

from __future__ import annotations

import argparse
import os
import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path


# ── Subcommand dispatch ────────────────────────────────────────────────

_SUBCOMMANDS = {"fonts", "preview"}


def _open_path(path: Path) -> None:
    """Open a file with the platform's default viewer (best effort)."""
    sysname = platform.system()
    try:
        if sysname == "Darwin":
            subprocess.run(["open", str(path)], check=False)
        elif sysname == "Linux":
            subprocess.run(["xdg-open", str(path)], check=False)
        else:
            print(f"Output: {path}")
    except Exception as exc:
        print(f"(could not auto-open: {exc})")
        print(f"Output: {path}")


def _smoke_screenplay_path() -> Path:
    """Path to the bundled font_smoke.yaml screenplay."""
    return Path(__file__).resolve().parent / "scripts" / "font_smoke.yaml"


def _render_screenplay(
    script_path: Path,
    output_path: Path,
    skin_override: str | None,
    font_override: str | None,
    final_width: int | None = None,
    final_height: int | None = None,
) -> None:
    """Run the full pipeline for a screenplay, with optional overrides."""
    from moirae.player import load_screenplay
    from moirae.pipeline import run_pipeline

    screenplay = load_screenplay(str(script_path), skin_override=skin_override)
    if font_override:
        screenplay.output.font_family = font_override
    if final_width is not None:
        screenplay.output.final_width = final_width
    if final_height is not None:
        screenplay.output.final_height = final_height

    run_pipeline(
        screenplay=screenplay,
        output_path=output_path,
        skin_override=skin_override,
        typing_speed_override=None,
        dry_run=False,
        debug_camera=False,
    )


def _cmd_fonts(argv: list[str]) -> int:
    """`python -m moirae fonts test ...` — render the bundled smoke screenplay."""
    parser = argparse.ArgumentParser(
        prog="python -m moirae fonts",
        description="Font coverage smoke test: render every glyph category the built-in skins use.",
    )
    sub = parser.add_subparsers(dest="action", required=True)
    test = sub.add_parser("test", help="Render a 5-second clip exercising every problematic glyph.")
    test.add_argument("--font", default=None, help="Override output.font_family")
    test.add_argument("--skin", default="ares", help="Override skin (default: ares — most glyph-heavy)")
    test.add_argument("-o", "--output", type=Path, default=None,
                      help="Output mp4 path (default: /tmp/moirae-fonts-test-<timestamp>.mp4)")
    args = parser.parse_args(argv)

    if args.action != "test":
        parser.print_help()
        return 2

    script = _smoke_screenplay_path()
    if not script.exists():
        print(f"error: bundled smoke screenplay not found at {script}", file=sys.stderr)
        return 1

    if args.output is None:
        ts = int(time.time())
        out = Path(tempfile.gettempdir()) / f"moirae-fonts-test-{ts}.mp4"
    else:
        out = args.output

    label_font = args.font or "(screenplay default)"
    print(f"Moirae font smoke test")
    print(f"  screenplay: {script}")
    print(f"  skin:       {args.skin}")
    print(f"  font:       {label_font}")
    print(f"  output:     {out}")
    print()

    # Speed knobs — the screenplay already trims durations, here we tune the
    # encoder to favour speed over quality.
    env = dict(os.environ)
    env.setdefault("MOIRAE_X264_PRESET", "ultrafast")
    env.setdefault("MOIRAE_X264_CRF", "28")
    os.environ.update(env)

    _render_screenplay(
        script_path=script,
        output_path=out,
        skin_override=args.skin,
        font_override=args.font,
        final_width=960,
        final_height=540,
    )

    print()
    print(f"Smoke render at {out}.")
    print("Inspect: any tofu boxes (□ with ?) mean the font is missing those glyphs.")
    _open_path(out)
    return 0


def _cmd_preview(argv: list[str]) -> int:
    """`python -m moirae preview <script> [--duration N] [-o PATH]`."""
    parser = argparse.ArgumentParser(
        prog="python -m moirae preview",
        description="Quick low-quality render of the first N seconds of a screenplay.",
    )
    parser.add_argument("script", type=Path, help="Path to YAML screenplay")
    parser.add_argument("--duration", type=float, default=8.0,
                        help="Seconds to keep from the start of the render (default: 8)")
    parser.add_argument("-o", "--output", type=Path, default=None,
                        help="Output mp4 path (default: <input>.preview.mp4)")
    parser.add_argument("--skin", default=None, help="Override skin")
    parser.add_argument("--font", default=None, help="Override output.font_family")
    args = parser.parse_args(argv)

    script = args.script.resolve()
    if not script.exists():
        print(f"error: screenplay not found: {script}", file=sys.stderr)
        return 1

    final_out = args.output or script.with_suffix(script.suffix + ".preview.mp4")
    # Render full screenplay to a temp mp4 first, then trim — asciinema has
    # no max-duration flag (only --idle-time-limit, which is for compression
    # of dead air), so fall back to ffmpeg trim of the encoded output.
    full_tmp = final_out.with_name(final_out.stem + ".full.tmp.mp4")

    env = dict(os.environ)
    env.setdefault("MOIRAE_X264_PRESET", "ultrafast")
    env.setdefault("MOIRAE_X264_CRF", "28")
    os.environ.update(env)

    print(f"Moirae preview")
    print(f"  screenplay: {script}")
    print(f"  duration:   {args.duration}s")
    print(f"  output:     {final_out}")
    print()

    _render_screenplay(
        script_path=script,
        output_path=full_tmp,
        skin_override=args.skin,
        font_override=args.font,
    )

    # Trim with ffmpeg `-c copy` (stream copy, no re-encode → fast).
    trim_cmd = [
        "ffmpeg", "-y",
        "-ss", "0",
        "-t", str(args.duration),
        "-i", str(full_tmp),
        "-c", "copy",
        str(final_out),
    ]
    print(f"Trimming first {args.duration}s → {final_out}")
    result = subprocess.run(trim_cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"ffmpeg trim failed (exit {result.returncode}):\n{result.stderr}", file=sys.stderr)
        # Fall back to the full render so the user still gets something.
        try:
            full_tmp.replace(final_out)
        except Exception:
            pass
    else:
        try:
            full_tmp.unlink()
        except Exception:
            pass

    print(f"Preview at {final_out}")
    _open_path(final_out)
    return 0


# ── Default: original screenplay-positional CLI ────────────────────────

def _cmd_default(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m moirae",
        description="Moirae — scripted terminal demo video pipeline",
    )
    parser.add_argument(
        "script", help="Path to YAML screenplay file",
    )
    parser.add_argument(
        "-o", "--output", type=Path, default=None,
        help="Output video path (e.g. output.mp4). If omitted, defaults to --play mode.",
    )
    parser.add_argument(
        "--play", action="store_true",
        help="Preview in terminal (no recording)",
    )
    parser.add_argument(
        "--skin", default=None,
        help="Override skin name (e.g. ares, mono, slate)",
    )
    parser.add_argument(
        "--typing-speed", type=float, default=None,
        help="Override base typing speed (seconds per character)",
    )
    parser.add_argument(
        "--timing", type=Path, default=None,
        help="Write timing manifest JSON to this path (used internally by pipeline)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print pipeline commands without executing them",
    )
    parser.add_argument(
        "--debug-camera", action="store_true",
        help="Print resolved camera keyframe timeline with visible row/col ranges",
    )
    args = parser.parse_args(argv)

    # If neither --output nor --play, default to --play
    if args.output is None and not args.play:
        args.play = True

    # Load screenplay
    from moirae.player import load_screenplay
    screenplay = load_screenplay(
        args.script,
        skin_override=args.skin,
        typing_speed_override=args.typing_speed,
    )

    if args.debug_camera and args.output is None:
        # Debug-only mode: use existing timing/cast files to print camera report
        from moirae.camera import debug_camera_report
        from moirae.pipeline import _resolve_camera_keyframes
        stem = Path(args.script).stem
        timing_path = args.timing or Path(f"{stem}_timing.json")
        cast_path = Path(f"{stem}.cast")
        if not timing_path.exists():
            print(f"No timing file found at {timing_path}")
            print("Run with -o first to generate timing data, or pass --timing <path>")
            return 1
        rows, cols = 80, 200
        gif_path = Path(f"{stem}.gif")
        out = screenplay.output
        keyframes = _resolve_camera_keyframes(screenplay, timing_path, total_rows=rows)
        print(debug_camera_report(
            keyframes, total_rows=rows, total_cols=cols,
            cast_path=cast_path, gif_path=gif_path,
            output_w=out.final_width, output_h=out.final_height,
        ))
        return 0

    if args.play:
        # Terminal preview mode
        from moirae.player import play
        try:
            play(screenplay, timing_path=args.timing)
        except KeyboardInterrupt:
            sys.stdout.write("\033[0m\n")
            return 0
    else:
        # Full pipeline mode
        from moirae.pipeline import run_pipeline
        run_pipeline(
            screenplay=screenplay,
            output_path=args.output,
            skin_override=args.skin,
            typing_speed_override=args.typing_speed,
            dry_run=args.dry_run,
            debug_camera=args.debug_camera,
        )
    return 0


def main():
    argv = sys.argv[1:]
    if argv and argv[0] in _SUBCOMMANDS:
        sub = argv[0]
        rest = argv[1:]
        if sub == "fonts":
            sys.exit(_cmd_fonts(rest))
        if sub == "preview":
            sys.exit(_cmd_preview(rest))
    sys.exit(_cmd_default(argv))


if __name__ == "__main__":
    main()
