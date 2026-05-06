"""Pydantic models for screenplay YAML and camera directives."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field


# ── Camera ──────────────────────────────────────────────────────────────

class CameraDirective(BaseModel):
    """Camera position/zoom keyframe attached to a scene phase or standalone."""

    zoom: float = 1.0
    x: float = 0.5  # Normalized 0.0–1.0, viewport center
    y: float = 0.5
    auto_y: bool = False  # If true, compute y from cursor position at marker time
    at: str = "scene_start"  # Timing marker reference
    duration: float = 0.5  # Transition time in seconds
    ease: Literal["linear", "ease-in", "ease-out", "ease-in-out"] = "ease-in-out"


# ── Tool entry ──────────────────────────────────────────────────────────

class ToolEntry(BaseModel):
    """A single tool progress line in a conversation scene."""

    icon: str = "\u26a1"
    verb: str = "tool"
    detail: str = ""
    duration: str = "0.5s"
    delay: float = 0.15


# ── Scene types ─────────────────────────────────────────────────────────

class ConversationScene(BaseModel):
    """A user→thinking→tools→response exchange."""

    user: str = ""
    tools: List[ToolEntry] = Field(default_factory=list)
    response: str = ""
    response_label: str = " \u2695 Hermes "
    thinking_time: float = 2.0
    typing_speed: Optional[float] = None
    pause_between: Optional[float] = None
    pre_pause: float = 0.5
    post_pause: float = 0.5
    # Per-line delay (seconds) when streaming the agent response.
    # Smaller = faster reveal. None = use the default (0.06s/line).
    # Setting this to 0 effectively restores the old "all at once" behaviour.
    response_speed: Optional[float] = None

    # Camera directives (Phase 3)
    camera: Optional[CameraDirective] = None
    camera_response: Optional[CameraDirective] = None


class ActionScene(BaseModel):
    """A non-conversation action (clear, pause, print, banner, type_command, camera)."""

    action: str
    # pause
    duration: Optional[float] = None
    # print
    text: Optional[str] = None
    color: Optional[str] = None
    # banner
    model: Optional[str] = None
    context: Optional[str] = None
    session_id: Optional[str] = None
    tools_count: Optional[int] = None
    skills_count: Optional[int] = None
    # type_command
    command: Optional[str] = None
    prefix: Optional[str] = None
    output: Optional[str] = None
    # camera (standalone)
    zoom: Optional[float] = None
    x: Optional[float] = None
    y: Optional[float] = None
    auto_y: Optional[bool] = None
    ease: Optional[str] = None


Scene = Union[ConversationScene, ActionScene]


# ── Output config ───────────────────────────────────────────────────────

class OutputConfig(BaseModel):
    """Rendering and encoding settings."""

    width: int = 2560
    height: int = 1440
    final_width: int = 1280
    final_height: int = 720
    fps: int = 30
    font_size: int = 22
    font_family: str = "Menlo"
    theme: Optional[str] = None  # None = use skin's terminal_theme
    bg_image: Optional[str] = None       # Path to background image
    bg_opacity: float = 1.0              # Terminal opacity (0.0–1.0) over background
    bg_color: Optional[str] = None       # Background fill color (hex, e.g. "#fdf1de")


# ── Top-level screenplay ───────────────────────────────────────────────

class Screenplay(BaseModel):
    """Complete screenplay document parsed from YAML."""

    title: str = "Hermes Agent Demo"
    skin: str = "default"
    output: OutputConfig = Field(default_factory=OutputConfig)
    typing_speed: float = 0.04
    pause_between: float = 1.0
    scenes: List[Dict[str, Any]] = Field(default_factory=list)

    # ── helpers ──────────────────────────────────────────────────────

    def parsed_scenes(self) -> List[Scene]:
        """Parse raw scene dicts into typed Scene models."""
        result: List[Scene] = []
        for raw in self.scenes:
            if "action" in raw:
                result.append(ActionScene(**raw))
            else:
                # Convert nested tool dicts to ToolEntry models
                tools_raw = raw.get("tools", [])
                tools = [ToolEntry(**t) if isinstance(t, dict) else t for t in tools_raw]
                data = {**raw, "tools": tools}
                # Parse camera sub-objects
                if "camera" in data and isinstance(data["camera"], dict):
                    data["camera"] = CameraDirective(**data["camera"])
                if "camera_response" in data and isinstance(data["camera_response"], dict):
                    data["camera_response"] = CameraDirective(**data["camera_response"])
                result.append(ConversationScene(**data))
        return result
