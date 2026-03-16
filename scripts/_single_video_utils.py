#!/usr/bin/env python3
"""Shared helpers for the single-video Aliyun image pipeline."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

SHOT_HEADER_RE = re.compile(r"^### 镜号(\d+)：(.+)$")
TIMECODE_RE = re.compile(r"^(\d{2}):(\d{2})\.(\d{3}) - (\d{2}):(\d{2})\.(\d{3})$")

ALLOWED_TOP_LEVEL_KEYS = {"时间码", "ASR文本", "口播内容", "PPT视觉层", "数据层"}
ALLOWED_PPT_KEYS = {"布局", "主标题", "副标题", "要点", "数据卡", "图示提示"}
ALLOWED_LAYOUTS = {
    "CenterLayout",
    "SplitLayout",
    "StackLayout",
    "GridLayout",
    "TripleLayout",
    "CardLayout",
}
ALLOWED_DATA_KEYS = {
    "来源",
    "时间",
    "团队规模",
    "关键数字",
    "关键规则",
    "技术栈",
    "案例",
    "结论",
    "对比项",
}


class StoryboardError(ValueError):
    pass


def safe_json_dump(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False),
        encoding="utf-8",
    )


def safe_json_load(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def strip_wrapping_quotes(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"', "“", "”"}:
        return text[1:-1].strip()
    return text


def timecode_to_seconds(timecode: str) -> float:
    minutes, seconds = timecode.split(":")
    return int(minutes) * 60 + float(seconds)


def parse_timecode_range(value: str) -> Tuple[str, str]:
    text = value.strip()
    if not TIMECODE_RE.match(text):
        raise StoryboardError(f"非法时间码格式: {value}")
    start, end = text.split(" - ", 1)
    return start, end


def normalize_for_match(text: str) -> str:
    cleaned = re.sub(r"\s+", "", text)
    return re.sub(r"[\"'“”‘’`·•,，。！？!?:：；;（）()\[\]【】\-—_/|]+", "", cleaned).lower()


def shorten_text(text: str, max_chars: int = 18) -> str:
    value = strip_wrapping_quotes(text)
    for separator in ["。", "；", "：", "，", "、", ":", ";", ",", "（", "("]:
        if separator in value:
            value = value.split(separator, 1)[0].strip()
            break
    return value[:max_chars].strip()


def dedupe_preserve_order(items: List[str]) -> List[str]:
    seen = set()
    out = []
    for item in items:
        item = item.strip()
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _expect_line(lines: List[str], idx: int, prefix: str) -> str:
    if idx >= len(lines):
        raise StoryboardError(f"缺少字段: {prefix}")
    line = lines[idx]
    if not line.startswith(prefix):
        raise StoryboardError(f"期望 `{prefix}`，实际得到: {line}")
    return line[len(prefix) :].strip()


def parse_storyboard(path: str) -> List[Dict[str, Any]]:
    raw_lines = Path(path).read_text(encoding="utf-8").splitlines()
    lines = [line.rstrip() for line in raw_lines]

    shots: List[Dict[str, Any]] = []
    idx = 0
    expected_num = 1

    while idx < len(lines):
        while idx < len(lines) and not lines[idx].strip():
            idx += 1
        if idx >= len(lines):
            break

        header = lines[idx].strip()
        match = SHOT_HEADER_RE.match(header)
        if not match:
            raise StoryboardError(f"非法镜号标题: {header}")
        shot_num = int(match.group(1))
        if shot_num != expected_num:
            raise StoryboardError(
                f"镜号必须连续且从 1 开始，期望镜号 {expected_num}，实际 {shot_num}"
            )
        title = match.group(2).strip()
        idx += 1

        timecode_value = _expect_line(lines, idx, "- **时间码**: ")
        start_tc, end_tc = parse_timecode_range(timecode_value)
        idx += 1

        asr_text = strip_wrapping_quotes(_expect_line(lines, idx, "- **ASR文本**: "))
        idx += 1

        voiceover_text = ""
        if idx < len(lines) and lines[idx].startswith("- **口播内容**: "):
            voiceover_text = strip_wrapping_quotes(
                _expect_line(lines, idx, "- **口播内容**: ")
            )
            idx += 1

        if idx >= len(lines) or lines[idx].strip() != "- **PPT视觉层**:":
            raise StoryboardError("缺少 `- **PPT视觉层**:` 段落")
        idx += 1

        ppt: Dict[str, Any] = {}
        while idx < len(lines):
            line = lines[idx]
            if not line.strip():
                idx += 1
                continue
            if line.startswith("- **数据层**"):
                break
            if SHOT_HEADER_RE.match(line.strip()):
                raise StoryboardError("PPT视觉层 后缺少 数据层")
            if not line.startswith("  - **"):
                raise StoryboardError(f"PPT视觉层 子项缩进非法: {line}")

            child_match = re.match(r"^  - \*\*([^*]+)\*\*:(.*)$", line)
            if not child_match:
                raise StoryboardError(f"PPT视觉层 子项格式非法: {line}")
            key = child_match.group(1).strip()
            remainder = child_match.group(2).strip()
            if key not in ALLOWED_PPT_KEYS:
                raise StoryboardError(f"未知 PPT视觉层 字段: {key}")
            if key in {"布局", "主标题", "副标题", "图示提示"}:
                if not remainder:
                    raise StoryboardError(f"PPT视觉层 字段 `{key}` 不能为空")
                ppt[key] = strip_wrapping_quotes(remainder)
                idx += 1
                continue
            if remainder:
                raise StoryboardError(f"PPT视觉层 列表字段 `{key}` 不允许同行内值")
            idx += 1
            items: List[str] = []
            while idx < len(lines) and lines[idx].startswith("    - "):
                items.append(strip_wrapping_quotes(lines[idx][6:]))
                idx += 1
            if not items:
                raise StoryboardError(f"PPT视觉层 列表字段 `{key}` 不能为空列表")
            ppt[key] = items

        if "布局" not in ppt or "主标题" not in ppt:
            raise StoryboardError("PPT视觉层 必须包含 `布局` 和 `主标题`")
        if ppt["布局"] not in ALLOWED_LAYOUTS:
            raise StoryboardError(f"不支持的布局: {ppt['布局']}")

        if idx >= len(lines):
            raise StoryboardError("缺少 `数据层` 段落")

        data_layer: Dict[str, str] = {}
        if lines[idx].strip() == "- **数据层**: 无":
            idx += 1
        elif lines[idx].strip() == "- **数据层**:":
            idx += 1
            while idx < len(lines):
                line = lines[idx]
                if not line.strip():
                    idx += 1
                    continue
                if SHOT_HEADER_RE.match(line.strip()):
                    break
                if line.startswith("- **"):
                    raise StoryboardError(f"数据层后出现未知顶层字段: {line}")
                if not line.startswith("  - "):
                    raise StoryboardError(f"数据层 子项缩进非法: {line}")
                nested = line[4:]
                if ":" not in nested:
                    raise StoryboardError(f"数据层 子项格式非法: {line}")
                key, value = nested.split(":", 1)
                key = key.strip()
                value = strip_wrapping_quotes(value.strip())
                if key not in ALLOWED_DATA_KEYS:
                    raise StoryboardError(f"未知 数据层 字段: {key}")
                if not value:
                    raise StoryboardError(f"数据层 字段 `{key}` 不能为空")
                data_layer[key] = value
                idx += 1
        else:
            raise StoryboardError("数据层 必须为 `- **数据层**: 无` 或嵌套块")

        shots.append(
            {
                "shot_num": shot_num,
                "title": title,
                "start_timecode": start_tc,
                "end_timecode": end_tc,
                "start_sec": timecode_to_seconds(start_tc),
                "end_sec": timecode_to_seconds(end_tc),
                "duration": timecode_to_seconds(end_tc) - timecode_to_seconds(start_tc),
                "asr_text": asr_text,
                "voiceover_text": voiceover_text,
                "ppt_visual": ppt,
                "data_layer": data_layer,
            }
        )
        expected_num += 1

    if not shots:
        raise StoryboardError("未解析到任何镜头")
    return shots


def build_slide_spec(shot: Dict[str, Any], attempt: int) -> Dict[str, Any]:
    ppt = shot["ppt_visual"]
    bullets = list(ppt.get("要点", []))
    data_cards = list(ppt.get("数据卡", []))
    subtitle = ppt.get("副标题", "")
    layout = ppt["布局"]

    if attempt >= 2:
        bullets = [shorten_text(item, 16) for item in bullets[:2]]
        subtitle = ""
    if attempt >= 3:
        layout = "CardLayout"
        bullets = [shorten_text(item, 14) for item in bullets[:2]]
        data_cards = [shorten_text(item, 16) for item in data_cards[:2]]

    review_keywords = dedupe_preserve_order(
        [ppt["主标题"]]
        + bullets[:3]
        + data_cards[:3]
        + [shorten_text(v, 16) for v in shot["data_layer"].values()]
    )

    return {
        "shot_num": shot["shot_num"],
        "title": ppt["主标题"],
        "subtitle": subtitle,
        "layout_family": layout,
        "bullets": bullets[:3],
        "data_cards": data_cards[:3],
        "diagram_hint": ppt.get("图示提示", ""),
        "tone": "enterprise-tech presentation slide",
        "forbidden_elements": [
            "movie poster",
            "tiny unreadable text",
            "handwritten font",
            "photo collage",
            "watermark",
            "dense paragraph",
        ],
        "review_keywords": review_keywords,
        "asr_text": shot["asr_text"],
        "voiceover_text": shot.get("voiceover_text", ""),
        "data_layer": shot["data_layer"],
    }


def expected_output_filenames(shot_num: int) -> Dict[str, str]:
    return {
        "image": f"image-{shot_num:02d}.png",
        "prompt": f"image-{shot_num:02d}.prompt.txt",
        "request": f"image-{shot_num:02d}.request.json",
        "response": f"image-{shot_num:02d}.response.json",
    }
