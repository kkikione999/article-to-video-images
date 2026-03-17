#!/usr/bin/env python3
"""Shared helpers for the single-video Aliyun image pipeline."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

SHOT_HEADER_RE = re.compile(r"^### 镜号(\d+)：(.+)$")
TIMECODE_RE = re.compile(r"^(\d{2}):(\d{2})\.(\d{3}) - (\d{2}):(\d{2})\.(\d{3})$")

SHOT_TYPES = {
    "ppt_slide",
    "infographic",
    "concept_scene",
    "comparison_frame",
    "process_frame",
    "quote_frame",
}
TEXT_POLICY_MODES = {
    "none",
    "title_only",
    "title_plus_bullets",
    "title_plus_data",
    "quote_only",
}
TEXT_DENSITIES = {"sparse", "balanced", "dense"}

LEGACY_ALLOWED_PPT_KEYS = {"布局", "主标题", "副标题", "要点", "数据卡", "图示提示"}
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
COMPOSITION_KEYS = {"景别", "构图", "视角"}
INFO_LAYER_KEYS = {"前景", "中景", "背景"}
STYLE_ANCHOR_KEYS = {"主风格", "当前变体", "色调", "光线", "画面密度"}
TEXT_POLICY_KEYS = {"模式", "标题", "副标题", "要点", "数据卡"}

LEGACY_LAYOUT_COMPOSITION = {
    "CenterLayout": {"景别": "中景", "构图": "中心聚焦构图", "视角": "平视"},
    "SplitLayout": {"景别": "中景", "构图": "左右分栏构图", "视角": "平视"},
    "StackLayout": {"景别": "中景", "构图": "上下分层构图", "视角": "平视"},
    "GridLayout": {"景别": "中景", "构图": "网格信息构图", "视角": "平视"},
    "TripleLayout": {"景别": "中景", "构图": "三分信息构图", "视角": "平视"},
    "CardLayout": {"景别": "中近景", "构图": "卡片聚合构图", "视角": "平视"},
}
DEFAULT_STYLE_ANCHOR = {
    "主风格": "知识讲解视频画面，科技演示、信息图和概念插画混合语言",
    "当前变体": "结构化讲解镜头",
    "色调": "冷色科技蓝灰，允许局部高亮强调",
    "光线": "干净、清晰、高对比度演示光线",
    "画面密度": "balanced",
}
DEFAULT_AVOID_ITEMS = [
    "电影海报感",
    "杂志封面排版",
    "整页密集小字",
    "额外英文文案",
    "乱码或错字",
]
SHOT_TYPE_TO_LAYOUT = {
    "ppt_slide": "CardLayout",
    "infographic": "GridLayout",
    "concept_scene": "CenterLayout",
    "comparison_frame": "SplitLayout",
    "process_frame": "TripleLayout",
    "quote_frame": "CenterLayout",
}
SHOT_TYPE_TO_VARIANT = {
    "ppt_slide": "结构化演示页",
    "infographic": "关系信息图镜头",
    "concept_scene": "叙事概念镜头",
    "comparison_frame": "左右对照镜头",
    "process_frame": "流程递进镜头",
    "quote_frame": "收束金句镜头",
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


def _parse_plain_list(lines: List[str], idx: int, field_name: str) -> Tuple[List[str], int]:
    items: List[str] = []
    while idx < len(lines):
        line = lines[idx]
        if not line.strip():
            idx += 1
            continue
        if not line.startswith("  - "):
            break
        items.append(strip_wrapping_quotes(line[4:]))
        idx += 1
    if not items:
        raise StoryboardError(f"{field_name} 不能为空列表")
    return items, idx


def _parse_simple_map(
    lines: List[str],
    idx: int,
    field_name: str,
    allowed_keys: set[str],
    required_keys: set[str],
) -> Tuple[Dict[str, str], int]:
    payload: Dict[str, str] = {}
    while idx < len(lines):
        line = lines[idx]
        if not line.strip():
            idx += 1
            continue
        if not line.startswith("  - "):
            break
        nested = line[4:]
        if ":" not in nested:
            raise StoryboardError(f"{field_name} 子项格式非法: {line}")
        key, value = nested.split(":", 1)
        key = key.strip()
        value = strip_wrapping_quotes(value.strip())
        if key not in allowed_keys:
            raise StoryboardError(f"{field_name} 存在未知字段: {key}")
        if not value:
            raise StoryboardError(f"{field_name} 字段 `{key}` 不能为空")
        payload[key] = value
        idx += 1
    missing = [key for key in required_keys if key not in payload]
    if missing:
        raise StoryboardError(f"{field_name} 缺少字段: {', '.join(missing)}")
    return payload, idx


def _parse_text_policy(lines: List[str], idx: int) -> Tuple[Dict[str, Any], int]:
    payload: Dict[str, Any] = {
        "模式": None,
        "标题": "",
        "副标题": "",
        "要点": [],
        "数据卡": [],
    }

    while idx < len(lines):
        line = lines[idx]
        if not line.strip():
            idx += 1
            continue
        if not line.startswith("  - "):
            break

        nested = line[4:]
        if ":" not in nested:
            raise StoryboardError(f"上屏文字策略 子项格式非法: {line}")
        key, value = nested.split(":", 1)
        key = key.strip()
        value = value.strip()
        if key not in TEXT_POLICY_KEYS:
            raise StoryboardError(f"上屏文字策略 存在未知字段: {key}")

        if key in {"模式", "标题", "副标题"}:
            if not value:
                raise StoryboardError(f"上屏文字策略 字段 `{key}` 不能为空")
            payload[key] = strip_wrapping_quotes(value)
            idx += 1
            continue

        if value and value not in {"无", "none"}:
            raise StoryboardError(f"上屏文字策略 列表字段 `{key}` 不允许同行内值")
        idx += 1
        items: List[str] = []
        while idx < len(lines) and lines[idx].startswith("    - "):
            items.append(strip_wrapping_quotes(lines[idx][6:]))
            idx += 1
        payload[key] = items

    mode = payload["模式"]
    if mode not in TEXT_POLICY_MODES:
        raise StoryboardError(f"不支持的上屏文字模式: {mode}")

    if mode in {"title_only", "title_plus_bullets", "title_plus_data", "quote_only"} and not payload["标题"]:
        raise StoryboardError(f"上屏文字模式 `{mode}` 需要提供标题")
    if mode == "title_plus_bullets" and not payload["要点"]:
        raise StoryboardError("上屏文字模式 `title_plus_bullets` 需要至少一条要点")
    if mode == "title_plus_data" and not payload["数据卡"]:
        raise StoryboardError("上屏文字模式 `title_plus_data` 需要至少一条数据卡")
    if mode == "none" and (payload["标题"] or payload["副标题"] or payload["要点"] or payload["数据卡"]):
        raise StoryboardError("上屏文字模式 `none` 不允许附带标题或列表内容")

    return payload, idx


def _parse_legacy_ppt_block(lines: List[str], idx: int) -> Tuple[Dict[str, Any], int]:
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
        if key not in LEGACY_ALLOWED_PPT_KEYS:
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
    return ppt, idx


def _parse_data_layer(lines: List[str], idx: int) -> Tuple[Dict[str, str], int]:
    if idx >= len(lines):
        raise StoryboardError("缺少 `数据层` 段落")

    data_layer: Dict[str, str] = {}
    if lines[idx].strip() == "- **数据层**: 无":
        return data_layer, idx + 1
    if lines[idx].strip() != "- **数据层**:":
        raise StoryboardError("数据层 必须为 `- **数据层**: 无` 或嵌套块")

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
    return data_layer, idx


def _derive_shot_type_from_legacy(title: str, ppt: Dict[str, Any]) -> str:
    haystack = " ".join(
        [
            title,
            ppt.get("主标题", ""),
            ppt.get("副标题", ""),
            ppt.get("图示提示", ""),
            " ".join(ppt.get("要点", [])),
            " ".join(ppt.get("数据卡", [])),
        ]
    )
    if any(token in haystack for token in ["对比", "比较", "vs", "区别"]):
        return "comparison_frame"
    if any(token in haystack for token in ["流程", "步骤", "演进", "阶段", "链路"]):
        return "process_frame"
    if any(token in haystack for token in ["总结", "一句话", "结论", "启发", "收束", "金句"]):
        return "quote_frame"
    if ppt.get("数据卡") or ppt.get("布局") in {"GridLayout", "TripleLayout"}:
        return "infographic"
    if ppt.get("图示提示"):
        return "concept_scene"
    return "ppt_slide"


def _upgrade_legacy_shot(
    title: str,
    asr_text: str,
    voiceover_text: str,
    ppt: Dict[str, Any],
    data_layer: Dict[str, str],
) -> Dict[str, Any]:
    shot_type = _derive_shot_type_from_legacy(title, ppt)
    text_mode = "title_only"
    if ppt.get("数据卡"):
        text_mode = "title_plus_data"
    elif ppt.get("要点"):
        text_mode = "title_plus_bullets"

    density = "dense" if len(ppt.get("要点", [])) + len(ppt.get("数据卡", [])) >= 4 else "balanced"
    if shot_type == "quote_frame":
        density = "sparse"

    subject_elements = dedupe_preserve_order(
        [ppt.get("主标题", "")]
        + list(ppt.get("要点", []))[:2]
        + list(ppt.get("数据卡", []))[:1]
    )
    if ppt.get("图示提示"):
        subject_elements.append(shorten_text(ppt["图示提示"], 20))
    subject_elements = dedupe_preserve_order(subject_elements) or [title]

    action_relations = []
    if ppt.get("图示提示"):
        action_relations.append(strip_wrapping_quotes(ppt["图示提示"]))
    if voiceover_text and not action_relations:
        action_relations.append(shorten_text(voiceover_text, 32))
    elif asr_text and not action_relations:
        action_relations.append(shorten_text(asr_text, 32))
    action_relations = dedupe_preserve_order(action_relations) or [f"围绕“{ppt.get('主标题', title)}”组织主要信息"]

    info_layers = {
        "前景": ppt.get("主标题", title),
        "中景": ppt.get("图示提示", "主要信息卡片与图形主体"),
        "背景": next(iter(data_layer.values()), "干净的讲解背景，服务主题表达"),
    }

    style_anchor = dict(DEFAULT_STYLE_ANCHOR)
    style_anchor["当前变体"] = SHOT_TYPE_TO_VARIANT[shot_type]
    style_anchor["画面密度"] = density
    if shot_type == "quote_frame":
        style_anchor["色调"] = "克制、安静的收束色调，允许局部暖色点亮"

    visual_goal = (
        ppt.get("副标题")
        or shorten_text(voiceover_text, 24)
        or shorten_text(asr_text, 24)
        or ppt.get("主标题")
        or title
    )

    return {
        "shot_type": shot_type,
        "visual_goal": visual_goal,
        "subject_elements": subject_elements[:3],
        "action_relations": action_relations[:2],
        "composition": LEGACY_LAYOUT_COMPOSITION.get(
            ppt["布局"],
            {"景别": "中景", "构图": "结构化构图", "视角": "平视"},
        ),
        "information_layers": info_layers,
        "text_policy": {
            "模式": text_mode,
            "标题": ppt.get("主标题", ""),
            "副标题": ppt.get("副标题", ""),
            "要点": list(ppt.get("要点", []))[:3],
            "数据卡": list(ppt.get("数据卡", []))[:3],
        },
        "style_anchor": style_anchor,
        "avoid_items": list(DEFAULT_AVOID_ITEMS),
        "ppt_visual": ppt,
    }


def _parse_rich_blocks(lines: List[str], idx: int) -> Tuple[Dict[str, Any], Dict[str, Any], int]:
    payload: Dict[str, Any] = {}
    ppt_visual: Dict[str, Any] = {}

    while idx < len(lines):
        line = lines[idx].strip()
        if not line:
            idx += 1
            continue
        if line.startswith("- **数据层**"):
            break
        if SHOT_HEADER_RE.match(line):
            raise StoryboardError("镜头描述后缺少 数据层")

        if line.startswith("- **镜头类型**: "):
            payload["shot_type"] = strip_wrapping_quotes(_expect_line(lines, idx, "- **镜头类型**: "))
            idx += 1
            continue
        if line.startswith("- **视觉目标**: "):
            payload["visual_goal"] = strip_wrapping_quotes(_expect_line(lines, idx, "- **视觉目标**: "))
            idx += 1
            continue
        if line == "- **主体元素**:":
            payload["subject_elements"], idx = _parse_plain_list(lines, idx + 1, "主体元素")
            continue
        if line == "- **动作/关系**:":
            payload["action_relations"], idx = _parse_plain_list(lines, idx + 1, "动作/关系")
            continue
        if line == "- **构图与景别**:":
            payload["composition"], idx = _parse_simple_map(
                lines,
                idx + 1,
                "构图与景别",
                COMPOSITION_KEYS,
                COMPOSITION_KEYS,
            )
            continue
        if line == "- **信息层级**:":
            payload["information_layers"], idx = _parse_simple_map(
                lines,
                idx + 1,
                "信息层级",
                INFO_LAYER_KEYS,
                INFO_LAYER_KEYS,
            )
            continue
        if line == "- **上屏文字策略**:":
            payload["text_policy"], idx = _parse_text_policy(lines, idx + 1)
            continue
        if line == "- **风格锚点**:":
            payload["style_anchor"], idx = _parse_simple_map(
                lines,
                idx + 1,
                "风格锚点",
                STYLE_ANCHOR_KEYS,
                STYLE_ANCHOR_KEYS,
            )
            continue
        if line == "- **避免项**:":
            payload["avoid_items"], idx = _parse_plain_list(lines, idx + 1, "避免项")
            continue
        if line == "- **PPT视觉层**:":
            ppt_visual, idx = _parse_legacy_ppt_block(lines, idx + 1)
            continue
        raise StoryboardError(f"未知镜头字段: {lines[idx]}")

    required = {
        "shot_type",
        "visual_goal",
        "subject_elements",
        "action_relations",
        "composition",
        "information_layers",
        "text_policy",
        "style_anchor",
        "avoid_items",
    }
    missing = [key for key in required if key not in payload]
    if missing:
        raise StoryboardError(f"缺少镜头字段: {', '.join(missing)}")

    if payload["shot_type"] not in SHOT_TYPES:
        raise StoryboardError(f"不支持的镜头类型: {payload['shot_type']}")
    density = payload["style_anchor"]["画面密度"]
    if density not in TEXT_DENSITIES:
        raise StoryboardError(f"不支持的画面密度: {density}")
    if ppt_visual:
        payload["ppt_visual"] = ppt_visual
    return payload, ppt_visual, idx


def _normalize_rich_payload(title: str, payload: Dict[str, Any], ppt_visual: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(payload)
    text_policy = dict(normalized["text_policy"])
    style_anchor = dict(DEFAULT_STYLE_ANCHOR)
    style_anchor.update(normalized["style_anchor"])
    style_anchor["当前变体"] = style_anchor.get("当前变体") or SHOT_TYPE_TO_VARIANT[normalized["shot_type"]]
    style_anchor["画面密度"] = style_anchor.get("画面密度", "balanced")
    normalized["style_anchor"] = style_anchor
    normalized["subject_elements"] = dedupe_preserve_order(normalized["subject_elements"])[:4]
    normalized["action_relations"] = dedupe_preserve_order(normalized["action_relations"])[:4]
    normalized["avoid_items"] = dedupe_preserve_order(
        list(DEFAULT_AVOID_ITEMS) + list(normalized["avoid_items"])
    )[:6]

    if text_policy["模式"] == "title_only" and not text_policy["标题"] and ppt_visual:
        text_policy["标题"] = ppt_visual.get("主标题", "")
        text_policy["副标题"] = ppt_visual.get("副标题", "")
    if text_policy["模式"] == "title_plus_bullets" and not text_policy["要点"] and ppt_visual:
        text_policy["标题"] = text_policy["标题"] or ppt_visual.get("主标题", "")
        text_policy["副标题"] = text_policy["副标题"] or ppt_visual.get("副标题", "")
        text_policy["要点"] = list(ppt_visual.get("要点", []))[:3]
    if text_policy["模式"] == "title_plus_data" and not text_policy["数据卡"] and ppt_visual:
        text_policy["标题"] = text_policy["标题"] or ppt_visual.get("主标题", "")
        text_policy["副标题"] = text_policy["副标题"] or ppt_visual.get("副标题", "")
        text_policy["数据卡"] = list(ppt_visual.get("数据卡", []))[:3]
    if text_policy["模式"] != "none" and not text_policy["标题"]:
        text_policy["标题"] = title
    normalized["text_policy"] = text_policy
    normalized["ppt_visual"] = ppt_visual
    return normalized


def parse_storyboard(path: str) -> List[Dict[str, Any]]:
    raw_lines = Path(path).read_text(encoding="utf-8").splitlines()
    lines = [line.rstrip() for line in raw_lines]

    shots: List[Dict[str, Any]] = []
    idx = 0
    expected_num = 1

    while idx < len(lines) and not SHOT_HEADER_RE.match(lines[idx].strip()):
        idx += 1

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
            voiceover_text = strip_wrapping_quotes(_expect_line(lines, idx, "- **口播内容**: "))
            idx += 1

        rich_payload: Dict[str, Any]
        ppt_visual: Dict[str, Any]
        if idx < len(lines) and lines[idx].startswith("- **镜头类型**: "):
            rich_payload, ppt_visual, idx = _parse_rich_blocks(lines, idx)
            rich_payload = _normalize_rich_payload(title, rich_payload, ppt_visual)
        elif idx < len(lines) and lines[idx].strip() == "- **PPT视觉层**:":
            ppt_visual, idx = _parse_legacy_ppt_block(lines, idx + 1)
            rich_payload = _upgrade_legacy_shot(title, asr_text, voiceover_text, ppt_visual, {})
        else:
            raise StoryboardError("缺少 `镜头类型` 或 `PPT视觉层` 段落")

        data_layer, idx = _parse_data_layer(lines, idx)
        if not rich_payload.get("data_layer"):
            rich_payload["data_layer"] = data_layer
        if rich_payload.get("ppt_visual") and rich_payload["shot_type"] == "ppt_slide":
            rich_payload["ppt_visual"].setdefault("布局", SHOT_TYPE_TO_LAYOUT["ppt_slide"])

        shot = {
            "shot_num": shot_num,
            "title": title,
            "start_timecode": start_tc,
            "end_timecode": end_tc,
            "start_sec": timecode_to_seconds(start_tc),
            "end_sec": timecode_to_seconds(end_tc),
            "duration": timecode_to_seconds(end_tc) - timecode_to_seconds(start_tc),
            "asr_text": asr_text,
            "voiceover_text": voiceover_text,
            "shot_type": rich_payload["shot_type"],
            "visual_goal": rich_payload["visual_goal"],
            "subject_elements": rich_payload["subject_elements"],
            "action_relations": rich_payload["action_relations"],
            "composition": rich_payload["composition"],
            "information_layers": rich_payload["information_layers"],
            "text_policy": rich_payload["text_policy"],
            "style_anchor": rich_payload["style_anchor"],
            "avoid_items": rich_payload["avoid_items"],
            "ppt_visual": rich_payload.get("ppt_visual", {}),
            "data_layer": data_layer,
        }
        shots.append(shot)
        expected_num += 1

    if not shots:
        raise StoryboardError("未解析到任何镜头")
    return shots


def expected_text_phrases(source: Dict[str, Any]) -> List[str]:
    text_policy = source.get("text_policy", {})
    mode = text_policy.get("模式") or text_policy.get("mode")
    title = text_policy.get("标题") or text_policy.get("title") or ""
    subtitle = text_policy.get("副标题") or text_policy.get("subtitle") or ""
    bullets = text_policy.get("要点") or text_policy.get("bullets") or []
    data_cards = text_policy.get("数据卡") or text_policy.get("data_cards") or []

    phrases: List[str] = []
    if mode in {"title_only", "title_plus_bullets", "title_plus_data", "quote_only"} and title:
        phrases.append(title)
    if mode in {"title_only", "title_plus_bullets", "title_plus_data"} and subtitle:
        phrases.append(subtitle)
    if mode == "title_plus_bullets":
        phrases.extend(bullets[:3])
    if mode == "title_plus_data":
        phrases.extend(data_cards[:3])
    return dedupe_preserve_order([strip_wrapping_quotes(item) for item in phrases if item])


def collect_review_keywords(source: Dict[str, Any]) -> List[str]:
    items = expected_text_phrases(source)
    items.extend(source.get("subject_elements", [])[:3])
    items.extend(source.get("action_relations", [])[:2])
    items.extend(shorten_text(value, 16) for value in source.get("data_layer", {}).values())
    return dedupe_preserve_order([strip_wrapping_quotes(item) for item in items if item])


def _simplify_text_policy(text_policy: Dict[str, Any], attempt: int) -> Dict[str, Any]:
    simplified = {
        "模式": text_policy["模式"],
        "标题": text_policy.get("标题", ""),
        "副标题": text_policy.get("副标题", ""),
        "要点": list(text_policy.get("要点", [])),
        "数据卡": list(text_policy.get("数据卡", [])),
    }

    if attempt >= 2:
        simplified["副标题"] = ""
        simplified["要点"] = [shorten_text(item, 16) for item in simplified["要点"][:2]]
        simplified["数据卡"] = [shorten_text(item, 16) for item in simplified["数据卡"][:2]]

    if attempt >= 3:
        simplified["要点"] = [shorten_text(item, 14) for item in simplified["要点"][:2]]
        simplified["数据卡"] = [shorten_text(item, 14) for item in simplified["数据卡"][:1]]
        if simplified["模式"] == "title_plus_data" and len(simplified["数据卡"]) < 2:
            simplified["模式"] = "title_only"
        if simplified["模式"] == "title_plus_bullets" and not simplified["要点"]:
            simplified["模式"] = "title_only"

    return simplified


def build_slide_spec(shot: Dict[str, Any], attempt: int) -> Dict[str, Any]:
    text_policy = _simplify_text_policy(shot["text_policy"], attempt)
    style_anchor = dict(DEFAULT_STYLE_ANCHOR)
    style_anchor.update(shot["style_anchor"])

    if attempt >= 2 and style_anchor["画面密度"] == "dense":
        style_anchor["画面密度"] = "balanced"
    if attempt >= 3:
        style_anchor["当前变体"] = f"{style_anchor['当前变体']}（简化构图版）"
        if shot["shot_type"] in {"quote_frame", "concept_scene"}:
            style_anchor["画面密度"] = "sparse"

    subject_elements = list(shot["subject_elements"])[:4]
    action_relations = list(shot["action_relations"])[:3]
    if attempt >= 2:
        subject_elements = [shorten_text(item, 20) for item in subject_elements[:3]]
    if attempt >= 3:
        subject_elements = [shorten_text(item, 18) for item in subject_elements[:2]]
        action_relations = [shorten_text(item, 20) for item in action_relations[:2]]

    ppt_visual = dict(shot.get("ppt_visual", {}))
    layout = ppt_visual.get("布局") or SHOT_TYPE_TO_LAYOUT[shot["shot_type"]]
    if layout not in ALLOWED_LAYOUTS:
        layout = SHOT_TYPE_TO_LAYOUT[shot["shot_type"]]

    slide_spec = {
        "shot_num": shot["shot_num"],
        "shot_title": shot["title"],
        "shot_type": shot["shot_type"],
        "layout_family": layout,
        "visual_target": shot["visual_goal"],
        "subject_elements": subject_elements,
        "action_relations": action_relations,
        "composition": dict(shot["composition"]),
        "information_layers": dict(shot["information_layers"]),
        "text_policy": {
            "mode": text_policy["模式"],
            "title": text_policy["标题"],
            "subtitle": text_policy["副标题"],
            "bullets": list(text_policy["要点"])[:3],
            "data_cards": list(text_policy["数据卡"])[:3],
        },
        "style_anchor": {
            "main_style": style_anchor["主风格"],
            "current_variant": style_anchor["当前变体"],
            "palette": style_anchor["色调"],
            "lighting": style_anchor["光线"],
            "density": style_anchor["画面密度"],
        },
        "avoid_items": dedupe_preserve_order(list(DEFAULT_AVOID_ITEMS) + shot["avoid_items"])[:8],
        "tone": "knowledge-video mixed visual language",
        "ppt_visual": ppt_visual,
        "review_keywords": collect_review_keywords(
            {
                "text_policy": text_policy,
                "subject_elements": subject_elements,
                "action_relations": action_relations,
                "data_layer": shot["data_layer"],
            }
        ),
        "expected_text_phrases": expected_text_phrases({"text_policy": text_policy}),
        "asr_text": shot["asr_text"],
        "voiceover_text": shot.get("voiceover_text", ""),
        "data_layer": shot["data_layer"],
        "richness_profile": {
            "density": style_anchor["画面密度"],
            "requires_structure": shot["shot_type"] in {"ppt_slide", "infographic", "comparison_frame", "process_frame"},
            "requires_scene_depth": shot["shot_type"] in {"concept_scene", "comparison_frame"},
            "allow_text": text_policy["模式"] != "none",
        },
    }
    return slide_spec


def expected_output_filenames(shot_num: int) -> Dict[str, str]:
    return {
        "image": f"image-{shot_num:02d}.png",
        "prompt": f"image-{shot_num:02d}.prompt.txt",
        "request": f"image-{shot_num:02d}.request.json",
        "response": f"image-{shot_num:02d}.response.json",
    }
