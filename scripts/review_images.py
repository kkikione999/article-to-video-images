#!/usr/bin/env python3
"""Review generated mixed-shot images and promote compose-eligible attempts."""

from __future__ import annotations

import argparse
import math
import os
import shutil
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import dashscope
from PIL import Image, ImageFilter, ImageStat

from _single_video_utils import (
    build_slide_spec,
    normalize_for_match,
    parse_storyboard,
    safe_json_dump,
    safe_json_load,
)

MIN_RATIO = 16 / 9 - 0.02
MAX_RATIO = 16 / 9 + 0.02
NEAR_BLANK_STDDEV = 8.0
TOO_DARK = 20.0
TOO_BRIGHT = 245.0
OCR_MATCH_THRESHOLD = 0.55
OCR_MATCH_THRESHOLD_MINIMAL = 0.4
LOW_EDGE_DENSITY = 0.035
MEDIUM_EDGE_DENSITY = 0.055
HIGH_EDGE_DENSITY = 0.18
LOW_COLORFULNESS = 12.0
MEDIUM_COLORFULNESS = 18.0
NEIGHBOR_HASH_DISTANCE_SIMILAR = 5
NEIGHBOR_HASH_DISTANCE_WEAK = 8
ALLOWED_SELECTION_MODES = {"normal", "manual_degraded"}
PROMPT_LEAK_MARKERS = [
    "口播语义参考",
    "视觉目标",
    "镜头标题",
    "主体元素",
    "动作或关系",
    "构图与景别",
    "信息层级",
    "主视觉风格",
    "当前镜头变体",
    "画面密度",
    "允许出现的上屏文字",
    "数据卡只允许使用这些中文短句",
    "项目符号只允许使用这些中文短句",
]


def _attempt_request_path(output_dir: Path, shot_num: int, attempt: int) -> Path:
    return output_dir / "attempts" / f"shot-{shot_num:02d}" / f"attempt-{attempt:02d}.request.json"


def load_attempt_request(output_dir: Path, shot_num: int, attempt: int) -> Dict[str, Any]:
    return safe_json_load(_attempt_request_path(output_dir, shot_num, attempt), default={}) or {}


def is_local_overlay_attempt(attempt_request: Dict[str, Any]) -> bool:
    return (
        attempt_request.get("provider") == "comfyui"
        and bool(attempt_request.get("suppress_generated_text"))
    )


def _obj_get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def ensure_api_key() -> str:
    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError("未设置 DASHSCOPE_API_KEY 环境变量")
    dashscope.api_key = api_key
    return api_key


def parse_shot_numbers(raw: Optional[str]) -> Optional[List[int]]:
    if not raw:
        return None
    return [int(item.strip()) for item in raw.split(",") if item.strip()]


def parse_manual_degraded(raw: Optional[str]) -> Dict[int, int]:
    mapping: Dict[int, int] = {}
    if not raw:
        return mapping
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair:
            continue
        shot_str, attempt_str = pair.split(":", 1)
        mapping[int(shot_str)] = int(attempt_str)
    return mapping


def parse_manual_reject(raw: Optional[str]) -> List[int]:
    if not raw:
        return []
    return [int(item.strip()) for item in raw.split(",") if item.strip()]


def extract_ocr_text(image_path: Path) -> str:
    response = dashscope.MultiModalConversation.call(
        model="qwen-vl-ocr-latest",
        messages=[
            {
                "role": "user",
                "content": [
                    {"image": f"file://{image_path.resolve()}"},
                    {"text": "请识别图片中的主要中文文字，尽量原样输出。"},
                ],
            }
        ],
        ocr_options={"task": "text_recognition"},
    )
    output = _obj_get(response, "output", {})
    choices = _obj_get(output, "choices", []) or []
    texts: List[str] = []
    for choice in choices:
        message = _obj_get(choice, "message", {})
        content = _obj_get(message, "content", []) or []
        for item in content:
            text = _obj_get(item, "text")
            if text:
                texts.append(text)
    if texts:
        return "\n".join(texts)
    return _obj_get(output, "text", "") or ""


def _average_hash(image: Image.Image, size: int = 8) -> str:
    resized = image.convert("L").resize((size, size))
    pixels = list(resized.getdata())
    mean_value = sum(pixels) / len(pixels)
    return "".join("1" if pixel >= mean_value else "0" for pixel in pixels)


def _hash_distance(left: Optional[str], right: Optional[str]) -> Optional[int]:
    if not left or not right or len(left) != len(right):
        return None
    return sum(1 for a, b in zip(left, right) if a != b)


def _colorfulness(image: Image.Image) -> float:
    sample = image.resize((160, 90)).convert("RGB")
    pixels = list(sample.getdata())
    if not pixels:
        return 0.0

    rg_values: List[float] = []
    yb_values: List[float] = []
    for r, g, b in pixels:
        rg = abs(r - g)
        yb = abs(0.5 * (r + g) - b)
        rg_values.append(rg)
        yb_values.append(yb)

    def _mean(values: List[float]) -> float:
        return sum(values) / len(values)

    def _std(values: List[float], mean_value: float) -> float:
        return math.sqrt(sum((value - mean_value) ** 2 for value in values) / len(values))

    mean_rg = _mean(rg_values)
    mean_yb = _mean(yb_values)
    std_rg = _std(rg_values, mean_rg)
    std_yb = _std(yb_values, mean_yb)
    return round(math.sqrt(std_rg**2 + std_yb**2) + 0.3 * math.sqrt(mean_rg**2 + mean_yb**2), 2)


def _edge_density(gray_image: Image.Image) -> float:
    sampled = gray_image.resize((160, 90))
    edged = sampled.filter(ImageFilter.FIND_EDGES)
    pixels = list(edged.getdata())
    if not pixels:
        return 0.0
    edge_pixels = sum(1 for value in pixels if value >= 36)
    return round(edge_pixels / len(pixels), 4)


def compute_image_metrics(image_path: Path) -> Dict[str, Any]:
    with Image.open(image_path) as image:
        rgb = image.convert("RGB")
        width, height = rgb.size
        gray = rgb.convert("L")
        stat = ImageStat.Stat(gray)
        return {
            "width": width,
            "height": height,
            "mean_brightness": round(float(stat.mean[0]), 2),
            "grayscale_stddev": round(float(stat.stddev[0]), 2),
            "colorfulness": _colorfulness(rgb),
            "edge_density": _edge_density(gray),
            "ahash": _average_hash(rgb),
        }


def keyword_match_ratio(expected_phrases: List[str], ocr_text: str) -> float:
    normalized_ocr = normalize_for_match(ocr_text)
    candidates = [normalize_for_match(item) for item in expected_phrases if normalize_for_match(item)]
    if not candidates:
        return 0.0
    matched = sum(1 for item in candidates if item and item in normalized_ocr)
    return round(matched / len(candidates), 2)


def _finalize_attempt_record(record: Dict[str, Any], attempt: int, max_attempts: int) -> Dict[str, Any]:
    if not record["reason_codes"]:
        record["status"] = "pass"
        record["compose_eligible"] = True
        record["manual_decision"] = "approve"
        return record
    record["status"] = "retry" if attempt < max_attempts else "fail"
    record["compose_eligible"] = False
    record["manual_decision"] = "pending"
    return record


def _apply_basic_image_checks(record: Dict[str, Any]) -> None:
    metrics = record["metrics"]
    ratio = metrics["width"] / metrics["height"]
    if ratio < MIN_RATIO or ratio > MAX_RATIO:
        record["reason_codes"].append("aspect_ratio_invalid")
    if metrics["grayscale_stddev"] < NEAR_BLANK_STDDEV:
        record["reason_codes"].append("near_blank")
    if metrics["mean_brightness"] < TOO_DARK:
        record["reason_codes"].append("too_dark")
    if metrics["mean_brightness"] > TOO_BRIGHT:
        record["reason_codes"].append("too_bright")


def _apply_ocr_checks(
    record: Dict[str, Any],
    slide_spec: Dict[str, Any],
    ocr_text: str,
    attempt_request: Optional[Dict[str, Any]] = None,
) -> None:
    normalized_ocr = normalize_for_match(ocr_text)
    expected_phrases = slide_spec["expected_text_phrases"]
    text_mode = slide_spec["text_policy"]["mode"]
    local_overlay = is_local_overlay_attempt(attempt_request or {})
    leaked_markers = [
        marker
        for marker in PROMPT_LEAK_MARKERS
        if normalize_for_match(marker) and normalize_for_match(marker) in normalized_ocr
    ]

    record["metrics"]["ocr_text_length"] = len(normalized_ocr)
    if leaked_markers:
        record["metrics"]["prompt_leak_markers"] = leaked_markers
        record["reason_codes"].append("prompt_meta_text_visible")

    if expected_phrases:
        ratio_score = keyword_match_ratio(expected_phrases, ocr_text)
        record["metrics"]["ocr_keyword_match_ratio"] = ratio_score
        title_match = normalize_for_match(expected_phrases[0]) in normalized_ocr
        threshold = OCR_MATCH_THRESHOLD_MINIMAL if text_mode in {"title_only", "quote_only"} else OCR_MATCH_THRESHOLD
        if local_overlay:
            visible_overlay_text = len(normalized_ocr) >= 8
            title_match = title_match or visible_overlay_text
            threshold = 0.0 if visible_overlay_text else min(threshold, 0.15)
        if not title_match or ratio_score < threshold:
            record["reason_codes"].append("ocr_low_confidence")
    else:
        record["metrics"]["ocr_keyword_match_ratio"] = None

    if text_mode == "none" and len(normalized_ocr) > 18:
        record["reason_codes"].append("text_overload")
    if text_mode in {"title_only", "quote_only"} and len(normalized_ocr) > 44:
        record["reason_codes"].append("text_overload")
    if text_mode in {"title_plus_bullets", "title_plus_data"} and len(normalized_ocr) > 72:
        record["reason_codes"].append("text_overload")


def _apply_richness_checks(
    record: Dict[str, Any],
    slide_spec: Dict[str, Any],
    attempt_request: Optional[Dict[str, Any]] = None,
) -> None:
    metrics = record["metrics"]
    shot_type = slide_spec["shot_type"]
    page_archetype = slide_spec["page_archetype"]
    shot_flavor = slide_spec["shot_flavor"]
    knowledge_density = slide_spec["knowledge_density"]
    density = slide_spec["style_anchor"]["density"]
    local_overlay = is_local_overlay_attempt(attempt_request or {})
    busy_threshold = 0.34 if local_overlay else HIGH_EDGE_DENSITY
    summary_busy_threshold = 0.34 if local_overlay else 0.14
    quiet_threshold = 0.34 if local_overlay else 0.14

    if shot_type in {"infographic", "comparison_frame", "process_frame", "ppt_slide"} and metrics["edge_density"] < MEDIUM_EDGE_DENSITY:
        record["reason_codes"].append("structure_too_flat")

    if shot_type in {"infographic", "comparison_frame"} and metrics["colorfulness"] < LOW_COLORFULNESS:
        record["reason_codes"].append("visual_richness_low")

    if shot_type == "process_frame" and metrics["edge_density"] < 0.06:
        record["reason_codes"].append("process_flow_missing")

    if shot_type == "comparison_frame" and metrics["edge_density"] < 0.065:
        record["reason_codes"].append("comparison_structure_missing")

    if shot_type == "concept_scene":
        if metrics["edge_density"] < LOW_EDGE_DENSITY and metrics["colorfulness"] < MEDIUM_COLORFULNESS:
            record["reason_codes"].append("scene_depth_low")

    if shot_type == "quote_frame":
        if metrics["edge_density"] > busy_threshold:
            record["reason_codes"].append("quote_frame_too_busy")

    if page_archetype == "thesis_page" and metrics["edge_density"] > busy_threshold:
        record["reason_codes"].append("thesis_page_too_busy")
    if page_archetype == "summary_page" and metrics["edge_density"] > summary_busy_threshold:
        record["reason_codes"].append("summary_page_too_busy")
    if page_archetype == "evidence_page" and metrics["ocr_text_length"] < 4 and metrics["edge_density"] < MEDIUM_EDGE_DENSITY:
        record["reason_codes"].append("evidence_page_too_empty")
    if knowledge_density == "high" and metrics["edge_density"] < MEDIUM_EDGE_DENSITY:
        record["reason_codes"].append("knowledge_density_underdelivered")
    if knowledge_density == "low" and metrics["edge_density"] > busy_threshold:
        record["reason_codes"].append("knowledge_density_too_busy")

    if density == "dense" and metrics["edge_density"] < MEDIUM_EDGE_DENSITY:
        record["reason_codes"].append("density_underdelivered")

    if density == "sparse" and metrics["edge_density"] > busy_threshold:
        record["reason_codes"].append("density_too_busy")

    if shot_flavor == "quiet_resolve" and metrics["edge_density"] > quiet_threshold:
        record["reason_codes"].append("quiet_resolve_broken")
    if shot_flavor == "contrast_tension" and metrics["edge_density"] < MEDIUM_EDGE_DENSITY:
        record["reason_codes"].append("contrast_tension_missing")


def _latest_attempt_record(shot_state: Dict[str, Any], attempt: int) -> Optional[Dict[str, Any]]:
    for record in reversed(shot_state.get("attempts", [])):
        if record.get("attempt") == attempt:
            return record
    return None


def _apply_neighbor_diversity_check(
    record: Dict[str, Any],
    slide_spec: Dict[str, Any],
    shot_num: int,
    shot_lookup: Dict[int, Dict[str, Any]],
    state_map: Dict[int, Dict[str, Any]],
    attempt: int,
) -> None:
    prev_state = state_map.get(shot_num - 1)
    if not prev_state:
        return

    prev_record = _latest_attempt_record(prev_state, attempt)
    if not prev_record:
        approved_attempt = prev_state.get("approved_attempt")
        if approved_attempt:
            prev_record = _latest_attempt_record(prev_state, approved_attempt)
    if not prev_record:
        return

    distance = _hash_distance(record["metrics"].get("ahash"), prev_record.get("metrics", {}).get("ahash"))
    if distance is None:
        return

    record["metrics"]["neighbor_hash_distance"] = distance
    prev_slide_spec = build_slide_spec(shot_lookup[shot_num - 1], attempt)

    if distance <= NEIGHBOR_HASH_DISTANCE_SIMILAR:
        if slide_spec["shot_type"] != prev_slide_spec["shot_type"]:
            record["reason_codes"].append("neighbor_not_distinct")
        elif slide_spec["shot_type"] != "quote_frame":
            record["reason_codes"].append("neighbor_too_similar")
    elif distance <= NEIGHBOR_HASH_DISTANCE_WEAK and slide_spec["shot_type"] != prev_slide_spec["shot_type"]:
        record["reason_codes"].append("neighbor_variation_weak")


def build_attempt_review(
    shot: Dict[str, Any],
    attempt: int,
    output_dir: Path,
    max_attempts: int,
) -> Dict[str, Any]:
    slide_spec = build_slide_spec(shot, attempt)
    shot_dir = output_dir / "attempts" / f"shot-{shot['shot_num']:02d}"
    stem = f"attempt-{attempt:02d}"
    image_path = shot_dir / f"{stem}.png"
    review_path = shot_dir / f"{stem}.review.json"
    attempt_request = load_attempt_request(output_dir, shot["shot_num"], attempt)

    base = {
        "shot_num": shot["shot_num"],
        "attempt": attempt,
        "shot_type": slide_spec["shot_type"],
        "cognitive_action": slide_spec["cognitive_action"],
        "page_archetype": slide_spec["page_archetype"],
        "shot_flavor": slide_spec["shot_flavor"],
        "status": "retry",
        "compose_eligible": False,
        "manual_decision": "pending",
        "selection_mode": None,
        "reason_codes": [],
        "metrics": {},
        "ocr_text": "",
        "provider": attempt_request.get("provider"),
        "suppress_generated_text": bool(attempt_request.get("suppress_generated_text")),
        "review_path": str(review_path),
    }

    if not image_path.exists():
        base["status"] = "retry" if attempt < max_attempts else "fail"
        base["reason_codes"] = ["no_image_artifact"]
        return base

    metrics = compute_image_metrics(image_path)
    base["metrics"] = metrics
    _apply_basic_image_checks(base)

    try:
        ocr_text = extract_ocr_text(image_path)
        base["ocr_text"] = ocr_text
        _apply_ocr_checks(base, slide_spec, ocr_text, attempt_request=attempt_request)
    except Exception as exc:
        base["reason_codes"].append("ocr_api_unavailable")
        base["ocr_text"] = str(exc)

    _apply_richness_checks(base, slide_spec, attempt_request=attempt_request)
    return _finalize_attempt_record(base, attempt, max_attempts)


def _initial_review_state(shots: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "shots": [
            {
                "shot_num": shot["shot_num"],
                "current_status": "retry",
                "approved_attempt": None,
                "compose_eligible": False,
                "manual_decision": "pending",
                "selection_mode": None,
                "attempts": [],
            }
            for shot in shots
        ]
    }


def _shot_state_map(review_payload: Dict[str, Any]) -> Dict[int, Dict[str, Any]]:
    return {item["shot_num"]: item for item in review_payload["shots"]}


def _replace_attempt_record(attempts: List[Dict[str, Any]], record: Dict[str, Any]) -> None:
    for idx, existing in enumerate(attempts):
        if existing.get("attempt") == record.get("attempt"):
            attempts[idx] = record
            return
    attempts.append(record)
    attempts.sort(key=lambda item: item.get("attempt", 0))


def _copy_final_image(output_dir: Path, shot_num: int, attempt: int) -> Path:
    shot_dir = output_dir / "attempts" / f"shot-{shot_num:02d}"
    source = shot_dir / f"attempt-{attempt:02d}.png"
    final_dir = output_dir / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    destination = final_dir / f"image-{shot_num:02d}.png"
    shutil.copy2(source, destination)
    return destination


def _write_selected_images(output_dir: Path, review_payload: Dict[str, Any]) -> None:
    selected = []
    for shot in sorted(review_payload["shots"], key=lambda item: item["shot_num"]):
        if not shot["compose_eligible"] or not shot["approved_attempt"]:
            continue
        selected.append(
            {
                "shot_num": shot["shot_num"],
                "approved_attempt": shot["approved_attempt"],
                "selection_mode": shot["selection_mode"],
                "compose_eligible": True,
                "final_path": str(
                    (output_dir / "final" / f"image-{shot['shot_num']:02d}.png").resolve()
                ),
            }
        )
    safe_json_dump(output_dir / "final" / "selected-images.json", selected)


def review_images_for_attempt(
    storyboard_path: str,
    output_dir: str,
    attempt: int,
    max_attempts: int,
    shot_numbers: Optional[Iterable[int]] = None,
    manual_degraded: Optional[Dict[int, int]] = None,
    manual_reject: Optional[Iterable[int]] = None,
) -> Dict[str, Any]:
    ensure_api_key()
    shots = parse_storyboard(storyboard_path)
    shot_lookup = {shot["shot_num"]: shot for shot in shots}
    if shot_numbers is None:
        if manual_degraded or manual_reject:
            selected_shots = set()
        else:
            selected_shots = set(shot["shot_num"] for shot in shots)
    else:
        selected_shots = set(shot_numbers)
    output_path = Path(output_dir)
    review_path = output_path / "review.json"
    review_payload = safe_json_load(review_path) or _initial_review_state(shots)
    state_map = _shot_state_map(review_payload)

    for shot in shots:
        if shot["shot_num"] not in selected_shots:
            continue
        shot_state = state_map[shot["shot_num"]]
        if shot_state.get("compose_eligible"):
            continue

        attempt_review = build_attempt_review(shot, attempt, output_path, max_attempts)
        slide_spec = build_slide_spec(shot, attempt)
        _apply_neighbor_diversity_check(
            attempt_review,
            slide_spec,
            shot["shot_num"],
            shot_lookup,
            state_map,
            attempt,
        )
        attempt_review["reason_codes"] = sorted(set(attempt_review["reason_codes"]))
        attempt_review = _finalize_attempt_record(attempt_review, attempt, max_attempts)
        safe_json_dump(Path(attempt_review["review_path"]), attempt_review)

        _replace_attempt_record(shot_state["attempts"], attempt_review)
        shot_state["current_status"] = attempt_review["status"]
        shot_state["manual_decision"] = attempt_review["manual_decision"]
        shot_state["compose_eligible"] = attempt_review["compose_eligible"]
        if attempt_review["status"] == "pass":
            shot_state["approved_attempt"] = attempt
            shot_state["selection_mode"] = "normal"
            _copy_final_image(output_path, shot["shot_num"], attempt)

    for shot_num, selected_attempt in (manual_degraded or {}).items():
        shot_state = state_map[shot_num]
        _copy_final_image(output_path, shot_num, selected_attempt)
        shot_state["current_status"] = "pass"
        shot_state["approved_attempt"] = selected_attempt
        shot_state["compose_eligible"] = True
        shot_state["manual_decision"] = "approve_degraded"
        shot_state["selection_mode"] = "manual_degraded"

    for shot_num in manual_reject or []:
        shot_state = state_map[shot_num]
        shot_state["current_status"] = "fail"
        shot_state["compose_eligible"] = False
        shot_state["manual_decision"] = "reject_and_pause"
        shot_state["selection_mode"] = None
        final_image = output_path / "final" / f"image-{shot_num:02d}.png"
        if final_image.exists():
            final_image.unlink()

    _write_selected_images(output_path, review_payload)
    safe_json_dump(review_path, review_payload)
    return review_payload


def main() -> None:
    parser = argparse.ArgumentParser(description="审核生成的知识视频镜头图片")
    parser.add_argument("storyboard", help="分镜脚本路径")
    parser.add_argument("-o", "--output", required=True, help="输出目录，例如 05-images/video-1")
    parser.add_argument("--attempt", type=int, required=True, help="当前审核的 attempt 编号")
    parser.add_argument("--max-attempts", type=int, default=3, help="最大语义 attempt 数")
    parser.add_argument("--shots", help="只审核指定镜号，逗号分隔")
    parser.add_argument(
        "--manual-degraded",
        help="人工降级选择，例如 3:2,5:1 表示镜号3选attempt2、镜号5选attempt1",
    )
    parser.add_argument("--manual-reject", help="人工拒绝的镜号，逗号分隔，例如 1,3")
    args = parser.parse_args()

    payload = review_images_for_attempt(
        storyboard_path=args.storyboard,
        output_dir=args.output,
        attempt=args.attempt,
        max_attempts=args.max_attempts,
        shot_numbers=parse_shot_numbers(args.shots),
        manual_degraded=parse_manual_degraded(args.manual_degraded),
        manual_reject=parse_manual_reject(args.manual_reject),
    )
    eligible = sum(1 for shot in payload["shots"] if shot["compose_eligible"])
    print(f"✅ 审核完成，当前可合成镜头数: {eligible}/{len(payload['shots'])}")


if __name__ == "__main__":
    main()
