#!/usr/bin/env python3
"""Materialize and execute ComfyUI workflow templates for shot generation."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlencode

import requests
from PIL import Image, ImageDraw, ImageFont


PLACEHOLDER_RE = re.compile(r"__([A-Z0-9_]+)__")
SKILL_ROOT = Path(__file__).resolve().parent.parent
COMFYUI_TEMPLATE_DIR = SKILL_ROOT / "templates" / "comfyui"
DEFAULT_TEMPLATE_PATH = COMFYUI_TEMPLATE_DIR / "controlnet_ipadapter_api.example.json"
GENERATED_ASSET_DIR = COMFYUI_TEMPLATE_DIR / "_generated"
DEFAULT_STYLE_REFERENCE_PATH = GENERATED_ASSET_DIR / "default-style-reference.png"

ENV_COMFYUI_BASE_URL = "COMFYUI_BASE_URL"
ENV_COMFYUI_WORKFLOW_TEMPLATE = "COMFYUI_WORKFLOW_TEMPLATE"
ENV_COMFYUI_STYLE_IMAGE = "COMFYUI_STYLE_IMAGE"
ENV_COMFYUI_TIMEOUT_SECONDS = "COMFYUI_TIMEOUT_SECONDS"
ENV_COMFYUI_CHECKPOINT_NAME = "COMFYUI_CHECKPOINT_NAME"
ENV_COMFYUI_CONTROLNET_NAME = "COMFYUI_CONTROLNET_NAME"
ENV_COMFYUI_IPADAPTER_MODEL = "COMFYUI_IPADAPTER_MODEL"
ENV_COMFYUI_CLIP_VISION_MODEL = "COMFYUI_CLIP_VISION_MODEL"
ENV_COMFYUI_WIDTH = "COMFYUI_WIDTH"
ENV_COMFYUI_HEIGHT = "COMFYUI_HEIGHT"
ENV_COMFYUI_STEPS = "COMFYUI_STEPS"
ENV_COMFYUI_CFG = "COMFYUI_CFG"
ENV_COMFYUI_DENOISE = "COMFYUI_DENOISE"
ENV_COMFYUI_CONTROL_STRENGTH = "COMFYUI_CONTROL_STRENGTH"
ENV_COMFYUI_IPADAPTER_WEIGHT = "COMFYUI_IPADAPTER_WEIGHT"
ENV_COMFYUI_SAMPLER_NAME = "COMFYUI_SAMPLER_NAME"
ENV_COMFYUI_SCHEDULER = "COMFYUI_SCHEDULER"
ENV_COMFYUI_RENDER_TEXT_OVERLAY = "COMFYUI_RENDER_TEXT_OVERLAY"
ENV_COMFYUI_FONT_PATH = "COMFYUI_FONT_PATH"

DEFAULT_BASE_URL = "http://127.0.0.1:8188"
DEFAULT_TIMEOUT_SECONDS = 900
DEFAULT_WIDTH = 1536
DEFAULT_HEIGHT = 864
DEFAULT_STEPS = 28
DEFAULT_CFG = 6.5
DEFAULT_DENOISE = 1.0
DEFAULT_CONTROL_STRENGTH = 0.82
DEFAULT_IPADAPTER_WEIGHT = 0.72
DEFAULT_SAMPLER_NAME = "dpmpp_2m"
DEFAULT_SCHEDULER = "karras"
DEFAULT_RENDER_TEXT_OVERLAY = True
DEFAULT_FONT_CANDIDATES = [
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
]
MODEL_CHOICE_FIELDS = {
    "CheckpointLoaderSimple": ("ckpt_name", "checkpoint_name", ENV_COMFYUI_CHECKPOINT_NAME, "Checkpoint"),
    "ControlNetLoader": ("control_net_name", "controlnet_name", ENV_COMFYUI_CONTROLNET_NAME, "ControlNet"),
    "CLIPVisionLoader": ("clip_name", "clip_vision_model", ENV_COMFYUI_CLIP_VISION_MODEL, "CLIP Vision"),
    "IPAdapterModelLoader": ("ipadapter_file", "ipadapter_model", ENV_COMFYUI_IPADAPTER_MODEL, "IPAdapter"),
    "StyleModelLoader": ("style_model_name", None, None, "Style Model"),
    "PhotoMakerLoader": ("photomaker_model_name", None, None, "PhotoMaker"),
}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"环境变量 {name} 不是合法整数: {raw}") from exc


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise RuntimeError(f"环境变量 {name} 不是合法数字: {raw}") from exc


def _sanitize_filename(value: str, fallback: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-")
    return safe or fallback


def _stable_seed(shot_num: int, attempt: int, title: str) -> int:
    digest = hashlib.sha256(f"{shot_num}:{attempt}:{title}".encode("utf-8")).hexdigest()
    return int(digest[:12], 16)


@dataclass
class ComfyUIOptions:
    base_url: str
    workflow_template: Path
    style_image: Optional[Path]
    timeout_seconds: int
    checkpoint_name: str
    controlnet_name: str
    ipadapter_model: str
    clip_vision_model: str
    width: int
    height: int
    steps: int
    cfg: float
    denoise: float
    control_strength: float
    ipadapter_weight: float
    sampler_name: str
    scheduler: str
    render_text_overlay: bool
    font_path: Optional[Path]


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def resolve_comfyui_options(
    base_url: Optional[str] = None,
    workflow_template: Optional[str] = None,
    style_image: Optional[str] = None,
    timeout_seconds: Optional[int] = None,
) -> ComfyUIOptions:
    resolved_template = Path(
        workflow_template
        or os.environ.get(ENV_COMFYUI_WORKFLOW_TEMPLATE)
        or DEFAULT_TEMPLATE_PATH
    ).expanduser().resolve()
    style_candidate = style_image or os.environ.get(ENV_COMFYUI_STYLE_IMAGE)
    resolved_style = Path(style_candidate).expanduser().resolve() if style_candidate else None
    return ComfyUIOptions(
        base_url=(base_url or os.environ.get(ENV_COMFYUI_BASE_URL) or DEFAULT_BASE_URL).rstrip("/"),
        workflow_template=resolved_template,
        style_image=resolved_style,
        timeout_seconds=int(timeout_seconds or _env_int(ENV_COMFYUI_TIMEOUT_SECONDS, DEFAULT_TIMEOUT_SECONDS)),
        checkpoint_name=os.environ.get(ENV_COMFYUI_CHECKPOINT_NAME, "").strip(),
        controlnet_name=os.environ.get(ENV_COMFYUI_CONTROLNET_NAME, "").strip(),
        ipadapter_model=os.environ.get(ENV_COMFYUI_IPADAPTER_MODEL, "").strip(),
        clip_vision_model=os.environ.get(ENV_COMFYUI_CLIP_VISION_MODEL, "").strip(),
        width=_env_int(ENV_COMFYUI_WIDTH, DEFAULT_WIDTH),
        height=_env_int(ENV_COMFYUI_HEIGHT, DEFAULT_HEIGHT),
        steps=_env_int(ENV_COMFYUI_STEPS, DEFAULT_STEPS),
        cfg=_env_float(ENV_COMFYUI_CFG, DEFAULT_CFG),
        denoise=_env_float(ENV_COMFYUI_DENOISE, DEFAULT_DENOISE),
        control_strength=_env_float(ENV_COMFYUI_CONTROL_STRENGTH, DEFAULT_CONTROL_STRENGTH),
        ipadapter_weight=_env_float(ENV_COMFYUI_IPADAPTER_WEIGHT, DEFAULT_IPADAPTER_WEIGHT),
        sampler_name=os.environ.get(ENV_COMFYUI_SAMPLER_NAME, DEFAULT_SAMPLER_NAME).strip(),
        scheduler=os.environ.get(ENV_COMFYUI_SCHEDULER, DEFAULT_SCHEDULER).strip(),
        render_text_overlay=_env_bool(ENV_COMFYUI_RENDER_TEXT_OVERLAY, DEFAULT_RENDER_TEXT_OVERLAY),
        font_path=Path(os.environ[ENV_COMFYUI_FONT_PATH]).expanduser().resolve()
        if os.environ.get(ENV_COMFYUI_FONT_PATH)
        else None,
    )


def _rounded(draw: ImageDraw.ImageDraw, box: Tuple[int, int, int, int], fill, outline, width=4, radius=22) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def _bars(
    draw: ImageDraw.ImageDraw,
    box: Tuple[int, int, int, int],
    count: int,
    fill,
    inset: int = 22,
    line_height: int = 18,
    gap: int = 14,
) -> None:
    x1, y1, x2, y2 = box
    usable = max(80, x2 - x1 - inset * 2)
    widths = [1.0, 0.82, 0.92, 0.74, 0.88, 0.68]
    y = y1 + inset
    for idx in range(count):
        if y + line_height > y2 - inset:
            break
        width_ratio = widths[idx % len(widths)]
        line_width = max(48, int(usable * width_ratio))
        draw.rounded_rectangle(
            (x1 + inset, y, x1 + inset + line_width, y + line_height),
            radius=max(6, line_height // 2),
            fill=fill,
        )
        y += line_height + gap


def _arrow(draw: ImageDraw.ImageDraw, start: Tuple[int, int], end: Tuple[int, int], fill, width: int = 8) -> None:
    draw.line([start, end], fill=fill, width=width)
    ex, ey = end
    sx, sy = start
    dx = ex - sx
    dy = ey - sy
    length = max((dx * dx + dy * dy) ** 0.5, 1.0)
    ux = dx / length
    uy = dy / length
    head = 18
    left = (int(ex - ux * head - uy * head * 0.5), int(ey - uy * head + ux * head * 0.5))
    right = (int(ex - ux * head + uy * head * 0.5), int(ey - uy * head - ux * head * 0.5))
    draw.polygon([end, left, right], fill=fill)


def _dashed_frame(draw: ImageDraw.ImageDraw, box: Tuple[int, int, int, int], fill, dash: int = 22, width: int = 4) -> None:
    x1, y1, x2, y2 = box
    for x in range(x1, x2, dash * 2):
        draw.line((x, y1, min(x + dash, x2), y1), fill=fill, width=width)
        draw.line((x, y2, min(x + dash, x2), y2), fill=fill, width=width)
    for y in range(y1, y2, dash * 2):
        draw.line((x1, y, x1, min(y + dash, y2)), fill=fill, width=width)
        draw.line((x2, y, x2, min(y + dash, y2)), fill=fill, width=width)


def _layout_counts(slide_spec: Dict[str, Any]) -> Tuple[int, int]:
    bullets = len(slide_spec["text_policy"].get("bullets") or [])
    data_cards = len(slide_spec["text_policy"].get("data_cards") or [])
    return max(1, bullets), max(1, data_cards)


def render_layout_guide(slide_spec: Dict[str, Any], destination: Path, width: int, height: int) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (width, height), "#f5f3ee")
    draw = ImageDraw.Draw(image)
    ink = "#203040"
    muted = "#6d7a88"
    accent = "#b6542b" if slide_spec["shot_flavor"] == "contrast_tension" else "#2e7d79"
    subtle = "#d9e1e7"

    outer = (36, 36, width - 36, height - 36)
    _dashed_frame(draw, outer, subtle, dash=24, width=3)

    title_height = 78
    subtitle_height = 36 if slide_spec["text_policy"]["subtitle"] else 0
    bullets_count, data_count = _layout_counts(slide_spec)
    layout = slide_spec["layout_family"]
    shot_type = slide_spec["shot_type"]
    page_archetype = slide_spec["page_archetype"]

    title_box = (90, 86, width - 340, 86 + title_height)
    _rounded(draw, title_box, fill="#ffffff", outline=ink, width=5, radius=24)
    _bars(draw, title_box, 1, ink, line_height=28, gap=10)
    if subtitle_height:
        subtitle_box = (90, 178, width - 430, 178 + 42)
        _rounded(draw, subtitle_box, fill="#ffffff", outline=muted, width=3, radius=18)
        _bars(draw, subtitle_box, 1, muted, line_height=16, gap=8)

    if layout == "CenterLayout":
        hero = (width // 2 - 250, 250, width // 2 + 250, 660)
        _rounded(draw, hero, fill="#ffffff", outline=accent, width=8, radius=40)
        draw.ellipse((hero[0] + 85, hero[1] + 65, hero[2] - 85, hero[3] - 65), outline=ink, width=6)
        side_left = (82, 300, 350, 610)
        side_right = (width - 350, 300, width - 82, 610)
        _rounded(draw, side_left, fill="#ffffff", outline=muted, width=4)
        _rounded(draw, side_right, fill="#ffffff", outline=muted, width=4)
        _bars(draw, side_left, min(3, bullets_count + 1), muted)
        _bars(draw, side_right, min(3, data_count + 1), muted)
    elif layout == "SplitLayout":
        left = (78, 250, width // 2 - 18, 702)
        right = (width // 2 + 18, 250, width - 78, 702)
        _rounded(draw, left, fill="#ffffff", outline=ink, width=5, radius=30)
        _rounded(draw, right, fill="#ffffff", outline=accent, width=6, radius=30)
        _bars(draw, left, bullets_count + 2, ink)
        if shot_type == "comparison_frame":
            inner_left = (left[0] + 36, left[1] + 250, left[2] - 36, left[3] - 42)
            inner_right = (right[0] + 36, right[1] + 70, right[2] - 36, right[3] - 42)
            _rounded(draw, inner_left, fill="#eef2f5", outline=muted, width=4, radius=24)
            _rounded(draw, inner_right, fill="#eef2f5", outline=muted, width=4, radius=24)
            _arrow(draw, (left[2] - 24, (left[1] + left[3]) // 2), (right[0] + 24, (right[1] + right[3]) // 2), accent, width=10)
        else:
            _bars(draw, right, data_count + 3, accent)
    elif layout == "StackLayout":
        top = (82, 250, width - 82, 418)
        mid_left = (82, 454, width // 2 - 16, 688)
        mid_right = (width // 2 + 16, 454, width - 82, 688)
        _rounded(draw, top, fill="#ffffff", outline=ink, width=5, radius=28)
        _rounded(draw, mid_left, fill="#ffffff", outline=muted, width=4, radius=24)
        _rounded(draw, mid_right, fill="#ffffff", outline=muted, width=4, radius=24)
        _bars(draw, top, min(4, bullets_count + 2), ink)
        _bars(draw, mid_left, min(4, bullets_count + 1), muted)
        _bars(draw, mid_right, min(4, data_count + 1), muted)
        _arrow(draw, ((top[0] + top[2]) // 2, top[3] + 8), ((mid_left[0] + mid_left[2]) // 2, mid_left[1] - 10), accent)
        _arrow(draw, ((top[0] + top[2]) // 2, top[3] + 8), ((mid_right[0] + mid_right[2]) // 2, mid_right[1] - 10), accent)
    elif layout == "GridLayout":
        boxes = []
        cols = 2 if page_archetype == "summary_page" else 3
        rows = 2
        left = 74
        top = 254
        gap = 24
        cell_w = (width - left * 2 - gap * (cols - 1)) // cols
        cell_h = (height - top - 86 - gap * (rows - 1)) // rows
        for row in range(rows):
            for col in range(cols):
                x1 = left + col * (cell_w + gap)
                y1 = top + row * (cell_h + gap)
                boxes.append((x1, y1, x1 + cell_w, y1 + cell_h))
        for idx, box in enumerate(boxes[: 4 if page_archetype == "summary_page" else len(boxes)]):
            _rounded(draw, box, fill="#ffffff", outline=ink if idx == 0 else muted, width=5 if idx == 0 else 4, radius=24)
            _bars(draw, box, 2 + (idx % 2), ink if idx == 0 else muted)
    elif layout == "TripleLayout":
        gap = 22
        block_w = (width - 76 * 2 - gap * 2) // 3
        y1, y2 = 278, 684
        blocks = []
        for idx in range(3):
            x1 = 76 + idx * (block_w + gap)
            blocks.append((x1, y1, x1 + block_w, y2))
        for idx, box in enumerate(blocks):
            outline = accent if idx == 1 else ink
            _rounded(draw, box, fill="#ffffff", outline=outline, width=6 if idx == 1 else 5, radius=28)
            _bars(draw, box, 3 if idx == 1 else 2, outline)
            if idx < 2:
                _arrow(draw, (box[2] + 10, (box[1] + box[3]) // 2), (blocks[idx + 1][0] - 10, (blocks[idx + 1][1] + blocks[idx + 1][3]) // 2), accent)
    else:
        main = (82, 254, width - 402, 700)
        rail = (width - 358, 254, width - 82, 700)
        _rounded(draw, main, fill="#ffffff", outline=ink, width=5, radius=28)
        _rounded(draw, rail, fill="#ffffff", outline=accent, width=5, radius=28)
        _bars(draw, main, bullets_count + 3, ink)
        card_gap = 18
        card_h = (rail[3] - rail[1] - 50 - card_gap * 2) // 3
        for idx in range(3):
            top_y = rail[1] + 24 + idx * (card_h + card_gap)
            card = (rail[0] + 20, top_y, rail[2] - 20, top_y + card_h)
            _rounded(draw, card, fill="#edf2f5", outline=muted, width=3, radius=18)
            _bars(draw, card, 2, muted, inset=16, line_height=16, gap=10)

    if shot_type == "process_frame":
        _arrow(draw, (120, height - 106), (width - 120, height - 106), accent, width=10)
    elif shot_type == "comparison_frame":
        draw.line((width // 2, 250, width // 2, height - 120), fill=accent, width=6)
    elif shot_type == "concept_scene":
        draw.ellipse((width // 2 - 78, 286, width // 2 + 78, 442), outline=accent, width=8)
        draw.arc((width // 2 - 220, 250, width // 2 + 220, 650), start=220, end=320, fill=muted, width=5)

    footer = (84, height - 92, width - 84, height - 62)
    _bars(draw, footer, 1, subtle, inset=0, line_height=30, gap=10)
    image.save(destination)


def ensure_default_style_reference(path: Path = DEFAULT_STYLE_REFERENCE_PATH) -> Path:
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    width, height = 1536, 864
    image = Image.new("RGB", (width, height), "#081521")
    draw = ImageDraw.Draw(image)

    cream = "#efe5d7"
    teal = "#62b3ae"
    orange = "#de7d4f"
    slate = "#18354f"
    pale = "#d7edf0"

    draw.rectangle((0, 0, width, height), fill="#091827")
    draw.rounded_rectangle((58, 52, width - 58, height - 52), radius=42, outline="#17344c", width=6, fill="#102231")
    draw.rounded_rectangle((88, 90, width - 456, 256), radius=28, fill=cream, outline=teal, width=6)
    draw.rounded_rectangle((88, 288, width - 456, height - 92), radius=34, fill="#12283a", outline=cream, width=4)
    draw.rounded_rectangle((width - 412, 90, width - 88, 404), radius=30, fill=cream, outline=orange, width=6)
    draw.rounded_rectangle((width - 412, 438, width - 88, height - 92), radius=30, fill="#f4f7fa", outline=teal, width=5)
    draw.ellipse((width - 378, 128, width - 118, 388), fill="#16324a", outline=orange, width=10)
    draw.rounded_rectangle((152, 338, width - 522, 420), radius=20, fill=pale, outline=teal, width=0)
    draw.rounded_rectangle((152, 452, width - 522, 534), radius=20, fill=pale, outline=teal, width=0)
    draw.rounded_rectangle((152, 566, width - 522, 648), radius=20, fill=pale, outline=teal, width=0)
    draw.rounded_rectangle((width - 380, 486, width - 120, 564), radius=18, fill="#d4e9e8", outline=teal, width=0)
    draw.rounded_rectangle((width - 380, 596, width - 120, 674), radius=18, fill="#f1d8c8", outline=orange, width=0)

    draw.line((width - 420, 420, width - 460, 420), fill=orange, width=8)
    draw.line((width - 460, 420, width - 460, 540), fill=orange, width=8)
    draw.line((width - 460, 540, width - 522, 540), fill=orange, width=8)
    draw.line((width - 420, 420, width - 460, 420), fill=orange, width=8)
    draw.line((width - 468, 420, width - 492, 404), fill=orange, width=8)
    draw.line((width - 468, 420, width - 492, 436), fill=orange, width=8)

    image.save(path)
    return path


def resolve_overlay_font_path(options: ComfyUIOptions) -> Path:
    if options.font_path:
        if not options.font_path.exists():
            raise RuntimeError(f"指定的字体文件不存在: {options.font_path}")
        return options.font_path
    for candidate in DEFAULT_FONT_CANDIDATES:
        path = Path(candidate)
        if path.exists():
            return path
    raise RuntimeError("未找到可用的中文字体文件，请设置 COMFYUI_FONT_PATH")


def _load_font(font_path: Path, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(font_path), size=size)


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> List[str]:
    if not text:
        return []
    lines: List[str] = []
    for paragraph in text.splitlines():
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        current = ""
        for char in paragraph:
            trial = current + char
            if not current or draw.textlength(trial, font=font) <= max_width:
                current = trial
                continue
            lines.append(current)
            current = char
        if current:
            lines.append(current)
    return lines or [text]


def _fit_wrapped_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font_path: Path,
    max_width: int,
    max_lines: int,
    max_size: int,
    min_size: int,
) -> Tuple[ImageFont.FreeTypeFont, List[str]]:
    for size in range(max_size, min_size - 1, -2):
        font = _load_font(font_path, size)
        lines = _wrap_text(draw, text, font, max_width)
        if len(lines) <= max_lines:
            return font, lines
    font = _load_font(font_path, min_size)
    lines = _wrap_text(draw, text, font, max_width)[:max_lines]
    return font, lines


def _draw_text_block(
    draw: ImageDraw.ImageDraw,
    box: Tuple[int, int, int, int],
    text_lines: List[str],
    font: ImageFont.FreeTypeFont,
    fill: Tuple[int, int, int, int],
    line_gap: int = 10,
) -> None:
    x1, y1, x2, y2 = box
    y = y1
    for line in text_lines:
        if y >= y2:
            break
        draw.text((x1, y), line, font=font, fill=fill)
        bbox = draw.textbbox((x1, y), line, font=font)
        y = bbox[3] + line_gap


def _draw_panel(
    draw: ImageDraw.ImageDraw,
    box: Tuple[int, int, int, int],
    fill: Tuple[int, int, int, int],
    outline: Tuple[int, int, int, int],
    width: int = 3,
    radius: int = 26,
) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def apply_text_overlay(image_path: Path, slide_spec: Dict[str, Any], options: ComfyUIOptions) -> None:
    if not options.render_text_overlay:
        return
    text_policy = slide_spec["text_policy"]
    if text_policy["mode"] == "none":
        return

    font_path = resolve_overlay_font_path(options)
    image = Image.open(image_path).convert("RGBA")
    width, height = image.size
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    ink = (244, 246, 248, 255)
    panel_fill = (7, 16, 26, 190)
    panel_light = (11, 24, 38, 176)
    outline = (100, 185, 176, 220)
    accent = (232, 140, 87, 255)

    title = text_policy["title"] or slide_spec["shot_title"]
    subtitle = text_policy["subtitle"]
    bullets = list(text_policy["bullets"] or [])
    data_cards = list(text_policy["data_cards"] or [])
    layout = slide_spec["layout_family"]

    if text_policy["mode"] == "quote_only":
        card = (width // 2 - int(width * 0.26), height // 2 - 120, width // 2 + int(width * 0.26), height // 2 + 120)
        _draw_panel(draw, card, panel_fill, outline, width=4, radius=30)
        font, lines = _fit_wrapped_text(draw, title, font_path, card[2] - card[0] - 72, 3, 46, 26)
        _draw_text_block(draw, (card[0] + 36, card[1] + 40, card[2] - 36, card[3] - 36), lines, font, ink, line_gap=14)
        image.alpha_composite(overlay)
        image.convert("RGB").save(image_path)
        return

    header = (60, 48, width - 60, 172 if subtitle else 148)
    _draw_panel(draw, header, panel_fill, outline, width=4, radius=28)
    title_font, title_lines = _fit_wrapped_text(draw, title, font_path, header[2] - header[0] - 40, 2, 40, 24)
    _draw_text_block(draw, (header[0] + 24, header[1] + 18, header[2] - 24, header[3] - 18), title_lines, title_font, ink, line_gap=8)
    if subtitle:
        sub_font, sub_lines = _fit_wrapped_text(draw, subtitle, font_path, header[2] - header[0] - 40, 2, 24, 16)
        title_block_height = draw.textbbox((0, 0), title_lines[-1], font=title_font)[3]
        _draw_text_block(
            draw,
            (header[0] + 24, header[1] + 24 + title_block_height + 12, header[2] - 24, header[3] - 18),
            sub_lines,
            sub_font,
            (215, 226, 232, 255),
            line_gap=6,
        )

    if text_policy["mode"] == "title_only":
        image.alpha_composite(overlay)
        image.convert("RGB").save(image_path)
        return

    if layout in {"SplitLayout", "CardLayout"}:
        body = (60, 198, int(width * 0.38), height - 56)
        _draw_panel(draw, body, panel_light, outline, width=3, radius=26)
        lines = bullets if bullets else data_cards
        body_font, body_lines = _fit_wrapped_text(
            draw,
            "\n".join(f"- {line}" for line in lines[:4]),
            font_path,
            body[2] - body[0] - 40,
            8,
            26,
            18,
        )
        _draw_text_block(draw, (body[0] + 20, body[1] + 20, body[2] - 20, body[3] - 20), body_lines, body_font, ink, line_gap=10)
    elif layout == "CenterLayout":
        left = (56, height - 208, int(width * 0.34), height - 56)
        _draw_panel(draw, left, panel_light, outline, width=3, radius=24)
        left_lines = bullets[:3] if bullets else [title]
        left_font, left_wrapped = _fit_wrapped_text(draw, "\n".join(f"- {line}" for line in left_lines), font_path, left[2] - left[0] - 32, 6, 24, 16)
        _draw_text_block(draw, (left[0] + 16, left[1] + 18, left[2] - 16, left[3] - 16), left_wrapped, left_font, ink, line_gap=8)
        if data_cards:
            right = (width - int(width * 0.3), height - 208, width - 56, height - 56)
            _draw_panel(draw, right, panel_light, outline, width=3, radius=24)
            right_font, right_wrapped = _fit_wrapped_text(
                draw,
                "\n".join(f"- {line}" for line in data_cards[:3]),
                font_path,
                right[2] - right[0] - 32,
                6,
                24,
                16,
            )
            _draw_text_block(draw, (right[0] + 16, right[1] + 18, right[2] - 16, right[3] - 16), right_wrapped, right_font, ink, line_gap=8)
    elif layout == "TripleLayout":
        segments = bullets[:3] if bullets else data_cards[:3]
        gap = 18
        w = (width - 60 * 2 - gap * 2) // 3
        y1, y2 = height - 182, height - 56
        for idx in range(min(3, len(segments))):
            x1 = 60 + idx * (w + gap)
            box = (x1, y1, x1 + w, y2)
            _draw_panel(draw, box, panel_light, outline if idx != 1 else accent, width=3, radius=22)
            font, wrapped = _fit_wrapped_text(draw, segments[idx], font_path, w - 28, 4, 22, 15)
            _draw_text_block(draw, (x1 + 14, y1 + 16, x1 + w - 14, y2 - 14), wrapped, font, ink, line_gap=6)
    else:
        footer = (60, height - 172, width - 60, height - 48)
        _draw_panel(draw, footer, panel_light, outline, width=3, radius=24)
        merged = bullets[:4] if bullets else data_cards[:4]
        footer_font, footer_lines = _fit_wrapped_text(
            draw,
            "  ".join(f"- {line}" for line in merged),
            font_path,
            footer[2] - footer[0] - 40,
            4,
            24,
            16,
        )
        _draw_text_block(draw, (footer[0] + 20, footer[1] + 18, footer[2] - 20, footer[3] - 18), footer_lines, footer_font, ink, line_gap=8)

    image.alpha_composite(overlay)
    image.convert("RGB").save(image_path)


def _load_workflow_template(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise RuntimeError(f"未找到 ComfyUI workflow 模板: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"ComfyUI workflow 模板不是合法 JSON: {path}") from exc


def _replace_tokens(value: Any, replacements: Dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {key: _replace_tokens(item, replacements) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_tokens(item, replacements) for item in value]
    if isinstance(value, str):
        if value in replacements:
            return replacements[value]

        def replacer(match: re.Match[str]) -> str:
            token = match.group(0)
            replacement = replacements.get(token)
            if replacement is None:
                return token
            return str(replacement)

        return PLACEHOLDER_RE.sub(replacer, value)
    return value


def _collect_unresolved_tokens(value: Any, found: Optional[set] = None) -> set:
    tokens = found or set()
    if isinstance(value, dict):
        for item in value.values():
            _collect_unresolved_tokens(item, tokens)
        return tokens
    if isinstance(value, list):
        for item in value:
            _collect_unresolved_tokens(item, tokens)
        return tokens
    if isinstance(value, str):
        for match in PLACEHOLDER_RE.findall(value):
            tokens.add(f"__{match}__")
    return tokens


def _collect_class_types(workflow: Dict[str, Any]) -> List[str]:
    class_types = []
    for node in workflow.values():
        if not isinstance(node, dict):
            continue
        class_type = str(node.get("class_type", "")).strip()
        if class_type and class_type not in class_types:
            class_types.append(class_type)
    return class_types


def fetch_comfyui_object_info(base_url: str) -> Dict[str, Any]:
    response = requests.get(f"{base_url}/object_info", timeout=15)
    response.raise_for_status()
    return response.json()


def _extract_combo_options(node_info: Dict[str, Any], field_name: str) -> List[str]:
    required = ((node_info or {}).get("input") or {}).get("required") or {}
    raw = required.get(field_name)
    if not raw:
        return []
    if isinstance(raw, list) and raw:
        first = raw[0]
        if isinstance(first, list):
            return [str(item) for item in first]
        if first == "COMBO" and len(raw) > 1 and isinstance(raw[1], dict):
            return [str(item) for item in (raw[1].get("options") or [])]
    return []


def inspect_comfyui_setup(
    options: ComfyUIOptions,
    workflow: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    system_stats = check_comfyui_server(options)
    object_info = fetch_comfyui_object_info(options.base_url)
    class_types = _collect_class_types(workflow or _load_workflow_template(options.workflow_template))

    missing_nodes: List[str] = []
    findings: List[str] = []
    choice_counts: Dict[str, Dict[str, Any]] = {}

    for class_type in class_types:
        if class_type not in object_info:
            missing_nodes.append(class_type)

    for class_type, (field_name, option_attr, env_name, label) in MODEL_CHOICE_FIELDS.items():
        if class_type not in class_types:
            continue
        node_info = object_info.get(class_type)
        if not node_info:
            continue
        choices = _extract_combo_options(node_info, field_name)
        choice_counts[class_type] = {
            "field": field_name,
            "label": label,
            "count": len(choices),
            "choices": choices,
        }
        if not choices:
            findings.append(f"{label} 模型列表为空: {class_type}.{field_name}")
            continue
        if option_attr:
            configured = getattr(options, option_attr, "")
            if not configured:
                findings.append(f"未设置 {env_name}")
            elif configured not in choices:
                findings.append(f"{env_name}={configured} 不在 ComfyUI 可用列表中")

    if missing_nodes:
        findings.append("缺少 workflow 所需节点: " + ", ".join(missing_nodes))

    return {
        "base_url": options.base_url,
        "workflow_template": str(options.workflow_template),
        "class_types": class_types,
        "system_stats": system_stats,
        "object_info": object_info,
        "missing_nodes": missing_nodes,
        "choice_counts": choice_counts,
        "findings": findings,
    }


def validate_comfyui_setup(
    options: ComfyUIOptions,
    workflow: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    report = inspect_comfyui_setup(options, workflow=workflow)
    if report["findings"]:
        message = "\n".join(f"- {item}" for item in report["findings"])
        raise RuntimeError(f"ComfyUI 环境校验失败:\n{message}")
    return report


def prepare_comfyui_workflow(
    slide_spec: Dict[str, Any],
    prompt: str,
    negative_prompt: str,
    shot_num: int,
    attempt: int,
    output_dir: Path,
    options: ComfyUIOptions,
) -> Dict[str, Any]:
    support_dir = output_dir / "comfyui-support" / f"shot-{shot_num:02d}"
    support_dir.mkdir(parents=True, exist_ok=True)
    control_image_path = support_dir / f"attempt-{attempt:02d}.control.png"
    render_layout_guide(slide_spec, control_image_path, width=options.width, height=options.height)
    scope_hash = hashlib.sha1(str(output_dir.resolve()).encode("utf-8")).hexdigest()[:10]

    if options.style_image:
        if not options.style_image.exists():
            raise RuntimeError(f"指定的 IPAdapter 参考图不存在: {options.style_image}")
        style_image_path = options.style_image
    else:
        style_image_path = ensure_default_style_reference()

    template = _load_workflow_template(options.workflow_template)
    output_prefix = f"article-to-video/{scope_hash}-shot-{shot_num:02d}-attempt-{attempt:02d}"
    control_upload_name = _sanitize_filename(
        f"{scope_hash}-shot-{shot_num:02d}-attempt-{attempt:02d}-control.png",
        f"shot-{shot_num:02d}-control.png",
    )
    style_upload_name = _sanitize_filename(
        f"{scope_hash}-shot-{shot_num:02d}-attempt-{attempt:02d}-{style_image_path.name}",
        "style-reference.png",
    )

    replacements = {
        "__POSITIVE_PROMPT__": prompt,
        "__NEGATIVE_PROMPT__": negative_prompt,
        "__CONTROL_IMAGE__": control_upload_name,
        "__IPADAPTER_IMAGE__": style_upload_name,
        "__OUTPUT_PREFIX__": output_prefix,
        "__WIDTH__": options.width,
        "__HEIGHT__": options.height,
        "__SEED__": _stable_seed(shot_num, attempt, slide_spec["shot_title"]),
        "__STEPS__": options.steps,
        "__CFG__": options.cfg,
        "__DENOISE__": options.denoise,
        "__CONTROL_STRENGTH__": options.control_strength,
        "__IPADAPTER_WEIGHT__": options.ipadapter_weight,
        "__SAMPLER_NAME__": options.sampler_name,
        "__SCHEDULER__": options.scheduler,
        "__CHECKPOINT_NAME__": options.checkpoint_name,
        "__CONTROLNET_NAME__": options.controlnet_name,
        "__IPADAPTER_MODEL__": options.ipadapter_model,
        "__CLIP_VISION_MODEL__": options.clip_vision_model,
        "__SHOT_TITLE__": slide_spec["shot_title"],
    }
    materialized = _replace_tokens(copy.deepcopy(template), replacements)
    unresolved = sorted(_collect_unresolved_tokens(materialized))
    if unresolved:
        raise RuntimeError(
            "ComfyUI workflow 仍有未替换占位符，请检查模板或环境变量: "
            + ", ".join(unresolved)
        )

    materialized_path = support_dir / f"attempt-{attempt:02d}.workflow.json"
    materialized_path.write_text(
        json.dumps(materialized, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        "provider": "comfyui",
        "base_url": options.base_url,
        "workflow_template": str(options.workflow_template),
        "materialized_workflow_path": str(materialized_path),
        "control_image_path": str(control_image_path),
        "style_image_path": str(style_image_path),
        "control_upload_name": control_upload_name,
        "style_upload_name": style_upload_name,
        "timeout_seconds": options.timeout_seconds,
        "output_prefix": output_prefix,
        "workflow": materialized,
        "replacements": {
            "width": options.width,
            "height": options.height,
            "steps": options.steps,
            "cfg": options.cfg,
            "denoise": options.denoise,
            "control_strength": options.control_strength,
            "ipadapter_weight": options.ipadapter_weight,
            "sampler_name": options.sampler_name,
            "scheduler": options.scheduler,
        },
    }


def check_comfyui_server(options: ComfyUIOptions) -> Dict[str, Any]:
    try:
        response = requests.get(f"{options.base_url}/system_stats", timeout=8)
        response.raise_for_status()
        return response.json()
    except Exception as exc:
        raise RuntimeError(f"无法连接 ComfyUI 服务: {options.base_url}") from exc


def _upload_image(base_url: str, image_path: Path, remote_name: str) -> Dict[str, Any]:
    with image_path.open("rb") as handle:
        files = {
            "image": (remote_name, handle, "image/png"),
        }
        data = {
            "type": "input",
            "overwrite": "true",
        }
        response = requests.post(f"{base_url}/upload/image", files=files, data=data, timeout=120)
        response.raise_for_status()
    return response.json()


def _queue_prompt(base_url: str, workflow: Dict[str, Any]) -> Dict[str, Any]:
    payload = {
        "client_id": f"article-to-video-{int(time.time())}",
        "prompt": workflow,
    }
    response = requests.post(f"{base_url}/prompt", json=payload, timeout=120)
    response.raise_for_status()
    return response.json()


def _get_history(base_url: str, prompt_id: str) -> Dict[str, Any]:
    response = requests.get(f"{base_url}/history/{prompt_id}", timeout=60)
    response.raise_for_status()
    return response.json()


def _wait_for_history(base_url: str, prompt_id: str, timeout_seconds: int) -> Dict[str, Any]:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        history = _get_history(base_url, prompt_id)
        record = history.get(prompt_id)
        if record:
            return record
        time.sleep(1.0)
    raise RuntimeError(f"等待 ComfyUI 完成超时: prompt_id={prompt_id}")


def _extract_image_record(history_record: Dict[str, Any]) -> Dict[str, Any]:
    outputs = history_record.get("outputs") or {}
    for node_id, output in outputs.items():
        for image in output.get("images") or []:
            enriched = dict(image)
            enriched["node_id"] = node_id
            return enriched
    status = history_record.get("status") or {}
    messages = status.get("messages") or []
    if messages:
        raise RuntimeError(f"ComfyUI 未返回图片输出: {messages}")
    raise RuntimeError("ComfyUI 未返回图片输出")


def _download_image(base_url: str, image_record: Dict[str, Any], destination: Path) -> None:
    query = urlencode(
        {
            "filename": image_record.get("filename", ""),
            "subfolder": image_record.get("subfolder", ""),
            "type": image_record.get("type", "output"),
        }
    )
    response = requests.get(f"{base_url}/view?{query}", timeout=180)
    response.raise_for_status()
    destination.write_bytes(response.content)


def execute_comfyui_workflow(
    prepared: Dict[str, Any],
    output_image_path: Path,
    response_path: Path,
) -> Dict[str, Any]:
    base_url = prepared["base_url"]
    control_upload = _upload_image(
        base_url,
        Path(prepared["control_image_path"]),
        prepared["control_upload_name"],
    )
    style_upload = _upload_image(
        base_url,
        Path(prepared["style_image_path"]),
        prepared["style_upload_name"],
    )
    prompt_info = _queue_prompt(base_url, prepared["workflow"])
    prompt_id = prompt_info.get("prompt_id")
    if not prompt_id:
        raise RuntimeError(f"ComfyUI /prompt 未返回 prompt_id: {prompt_info}")
    history_record = _wait_for_history(base_url, prompt_id, prepared["timeout_seconds"])
    image_record = _extract_image_record(history_record)
    output_image_path.parent.mkdir(parents=True, exist_ok=True)
    _download_image(base_url, image_record, output_image_path)
    response_payload = {
        "provider": "comfyui",
        "prompt_info": prompt_info,
        "prompt_id": prompt_id,
        "control_upload": control_upload,
        "style_upload": style_upload,
        "image_record": image_record,
        "history": history_record,
    }
    response_path.parent.mkdir(parents=True, exist_ok=True)
    response_path.write_text(
        json.dumps(response_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return response_payload


def build_comfyui_manifest_entries(
    shot_num: int,
    attempt: int,
    prepared: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "shot_num": shot_num,
        "attempt": attempt,
        "provider": "comfyui",
        "workflow_template": prepared["workflow_template"],
        "materialized_workflow_path": prepared["materialized_workflow_path"],
        "control_image_path": prepared["control_image_path"],
        "style_image_path": prepared["style_image_path"],
        "output_prefix": prepared["output_prefix"],
        "replacements": prepared["replacements"],
    }
