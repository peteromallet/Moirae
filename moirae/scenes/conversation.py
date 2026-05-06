"""Conversation scene: user prompt -> spinner -> tools -> response."""

from __future__ import annotations

import random
import sys
import threading
import time

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich import box as rich_box

from moirae.schema import ConversationScene
from moirae.scenes import (
    PlaybackContext, SceneTiming, _BOLD, _DIM, _RST,
    hex_to_ansi, register,
)

# ── Spinner frames ──────────────────────────────────────────────────────

SPINNER_FRAMES = ["\u280b", "\u2819", "\u2839", "\u2838", "\u283c", "\u2834", "\u2826", "\u2827", "\u2807", "\u280f"]

# Defaults (used when skin has none)
_DEFAULT_THINKING_FACES = [
    "(｡•́︿•̀｡)", "(◔_◔)", "(¬‿¬)", "( •_•)>⌐■-■", "(⌐■_■)",
    "(´･_･`)", "◉_◉", "(°ロ°)", "( ˘⌣˘)♡", "ヽ(>∀<☆)☆",
    "٩(๑❛ᴗ❛๑)۶", "(⊙_⊙)", "(¬_¬)", "( ͡° ͜ʖ ͡°)", "ಠ_ಠ",
]

_DEFAULT_THINKING_VERBS = [
    "pondering", "contemplating", "musing", "cogitating", "ruminating",
    "deliberating", "mulling", "reflecting", "processing", "reasoning",
    "analyzing", "computing", "synthesizing", "formulating", "brainstorming",
]


def _get_faces(ctx: PlaybackContext) -> list:
    faces = ctx.skin.get_spinner_list("thinking_faces")
    return faces if faces else _DEFAULT_THINKING_FACES


def _get_verbs(ctx: PlaybackContext) -> list:
    verbs = ctx.skin.get_spinner_list("thinking_verbs")
    return verbs if verbs else _DEFAULT_THINKING_VERBS


# ── Typing simulation ──────────────────────────────────────────────────

def type_text(text: str, speed: float, color: str):
    """Simulate typing character by character."""
    for ch in text:
        sys.stdout.write(f"{color}{ch}{_RST}")
        sys.stdout.flush()
        if ch in " \t":
            time.sleep(speed * 0.5)
        elif ch in ".,!?;:":
            time.sleep(speed * 2.5)
        else:
            time.sleep(speed * (0.7 + random.random() * 0.6))


# ── Demo spinner ────────────────────────────────────────────────────────

class DemoSpinner:
    """Animated braille spinner with kawaii faces."""

    def __init__(self, ctx: PlaybackContext):
        self.ctx = ctx
        self.running = False
        self.thread = None
        self.start_time = 0.0
        self.frame_idx = 0
        self.message = ""
        self.last_len = 0
        self._wings = ctx.skin.get_spinner_wings()

    def start(self, message: str = ""):
        self.message = message
        self.running = True
        self.start_time = time.time()
        self.frame_idx = 0
        self.last_len = 0
        self.thread = threading.Thread(target=self._animate, daemon=True)
        self.thread.start()

    def _animate(self):
        while self.running:
            frame = SPINNER_FRAMES[self.frame_idx % len(SPINNER_FRAMES)]
            elapsed = time.time() - self.start_time
            if self._wings:
                left, right = self._wings[self.frame_idx % len(self._wings)]
                line = f"  {left} {frame} {self.message} {right} ({elapsed:.1f}s)"
            else:
                line = f"  {frame} {self.message} ({elapsed:.1f}s)"
            pad = max(self.last_len - len(line), 0)
            sys.stdout.write(f"\r{line}{' ' * pad}")
            sys.stdout.flush()
            self.last_len = len(line)
            self.frame_idx += 1
            time.sleep(0.12)

    def update(self, message: str):
        self.message = message

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=0.5)
        blanks = " " * max(self.last_len + 5, 40)
        sys.stdout.write(f"\r{blanks}\r")
        sys.stdout.flush()


# ── Tool progress lines ────────────────────────────────────────────────

def show_tool_line(tool: dict, ctx: PlaybackContext):
    """Print a tool progress line."""
    icon = tool.get("icon", "\u26a1")
    verb = tool.get("verb", "tool")
    detail = tool.get("detail", "")
    duration = tool.get("duration", "0.5s")
    prefix = ctx.skin.tool_prefix

    line = f"  {prefix} {icon} {verb:9} {detail}  {duration}"
    sys.stdout.write(f"{_DIM}{line}{_RST}\n")
    ctx.newline()
    sys.stdout.flush()


# ── Response rendering ──────────────────────────────────────────────────

def show_response(text: str, label: str, ctx: PlaybackContext, line_delay: float = 0.06):
    """Show agent response in a Rich panel using skin colors.

    Renders the Panel(Markdown(...)) once into a recording Console, then
    streams the captured ANSI lines to stdout one at a time with a small
    sleep between lines. This makes the cursor advance gradually so the
    auto-tracking camera in camera.py can interpolate smoothly downward
    instead of teleporting once the response appears all at once.
    """
    border_color = ctx.skin.get_color("response_border", "#FFD700")
    panel = Panel(
        Markdown(text),
        title=f"[bold]{label}[/bold]",
        title_align="left",
        border_style=border_color,
        box=rich_box.HORIZONTALS,
        padding=(1, 2),
    )

    # Render the panel into a recording console at the same width as the
    # live console so wrapping matches. We then replay the styled output
    # line-by-line. This preserves all Rich styling (bold, code blocks,
    # bullet markers, panel border) while letting the cursor advance
    # gradually as each row is emitted.
    cols = ctx.console.width or 200
    rec = Console(
        record=True,
        force_terminal=True,
        color_system=ctx.console.color_system or "truecolor",
        width=cols,
        file=open("/dev/null", "w"),  # don't print to real stdout
    )
    try:
        rec.print(panel)
        rendered = rec.export_text(styles=True, clear=True)
    finally:
        try:
            rec.file.close()
        except Exception:
            pass

    # Rich tends to terminate the rendered block with a trailing newline;
    # split and drop a single empty trailing element so we don't emit an
    # extra blank row beyond the panel's bottom border.
    lines = rendered.split("\n")
    if lines and lines[-1] == "":
        lines.pop()

    # Cap line_delay sanely; very long responses with very long delays
    # would drag the recording out forever.
    delay = max(0.0, float(line_delay))

    for line in lines:
        sys.stdout.write(line)
        sys.stdout.write("\n")
        sys.stdout.flush()
        ctx.newline(1)
        if delay:
            time.sleep(delay)


# ── Scene handler ──────────────────────────────────────────────────────

@register("conversation")
def play_conversation(scene: ConversationScene, ctx: PlaybackContext):
    """Play a conversation scene: user → spinner → tools → response."""
    typing_speed = scene.typing_speed or ctx.typing_speed
    prompt_color = ctx.ansi_prompt
    prompt_symbol = ctx.skin.get_branding("prompt_symbol", "\u276f ")
    response_label = scene.response_label or ctx.skin.get_branding("response_label", " \u2695 Hermes ")

    # Timing
    scene_timing = SceneTiming(
        index=ctx.scene_index, scene_type="conversation"
    )
    scene_timing.start_t = ctx.now()

    # --- User message ---
    if scene.user:
        time.sleep(scene.pre_pause)
        ctx.mark(scene_timing, "user_start")

        # Show prompt
        sys.stdout.write(f"\n{prompt_color}{prompt_symbol}{_RST}")
        ctx.newline()
        sys.stdout.flush()
        time.sleep(0.3)

        # Type the message
        type_text(scene.user, speed=typing_speed, color=ctx.ansi_banner_text)
        time.sleep(0.6)

        # "Submit" — show as gold bullet
        gold = hex_to_ansi(ctx.skin.get_color("banner_title", "#FFD700"))
        sys.stdout.write("\r\033[K")
        submit_text = f"● {scene.user}"
        sys.stdout.write(f"{gold}{_BOLD}{submit_text}{_RST}\n")
        # Account for line wrapping: estimate how many terminal lines
        # the submitted text occupies (prompt chars + message length)
        cols = ctx.console.width or 200
        wrapped_lines = max(1, (len(submit_text) + cols - 1) // cols)
        ctx.newline(wrapped_lines)
        sys.stdout.flush()
        time.sleep(0.3)
        ctx.mark(scene_timing, "user_end")

    # --- Spinner (thinking) ---
    ctx.mark(scene_timing, "thinking_start")
    spinner = DemoSpinner(ctx)
    face = random.choice(_get_faces(ctx))
    verb = random.choice(_get_verbs(ctx))
    spinner.start(f"{face} {verb}...")

    thinking_time = scene.thinking_time
    if thinking_time > 2.0:
        time.sleep(thinking_time * 0.4)
        face2 = random.choice(_get_faces(ctx))
        verb2 = random.choice(_get_verbs(ctx))
        spinner.update(f"{face2} {verb2}...")
        time.sleep(thinking_time * 0.6)
    else:
        time.sleep(thinking_time)

    spinner.stop()
    ctx.mark(scene_timing, "thinking_end")

    # --- Tool progress lines ---
    if scene.tools:
        ctx.mark(scene_timing, "tools_start")
        for tool in scene.tools:
            tool_dict = tool.model_dump() if hasattr(tool, "model_dump") else tool
            show_tool_line(tool_dict, ctx)
            tool_delay = tool_dict.get("delay", 0.15)
            time.sleep(tool_delay)
        ctx.mark(scene_timing, "tools_end")

    # --- Response ---
    if scene.response:
        ctx.mark(scene_timing, "response_start")
        response_speed = scene.response_speed if scene.response_speed is not None else 0.06
        show_response(
            scene.response,
            label=response_label,
            ctx=ctx,
            line_delay=response_speed,
        )
        ctx.mark(scene_timing, "response_end")

    time.sleep(scene.post_pause)
    scene_timing.end_t = ctx.now()

    if ctx.timing:
        ctx.timing.add_scene(scene_timing)
