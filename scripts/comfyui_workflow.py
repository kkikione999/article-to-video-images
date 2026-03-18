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
from PIL import Image, ImageDraw


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
