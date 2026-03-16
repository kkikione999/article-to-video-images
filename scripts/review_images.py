#!/usr/bin/env python3
"""Review generated slide attempts and promote compose-eligible images."""

from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import dashscope
from PIL import Image, ImageStat

from _single_video_utils import normalize_for_match, parse_storyboard, safe_json_dump, safe_json_load

MIN_RATIO = 16 / 9 - 0.02
MAX_RATIO = 16 / 9 + 0.02
NEAR_BLANK_STDDEV = 8.0
TOO_DARK = 20.0
TOO_BRIGHT = 245.0
OCR_MATCH_THRESHOLD = 0.6
ALLOWED_SELECTION_MODES = {"normal", "manual_degraded"}


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


def normalize_expected_phrases(shot: Dict[str, Any]) -> List[str]:
    ppt = shot["ppt_visual"]
    phrases = [ppt["主标题"]]
    phrases.extend(ppt.get("要点", [])[:3])
    phrases.extend(ppt.get("数据卡", [])[:3])
    phrases.extend(list(shot["data_layer"].values())[:2])
    return [item for item in phrases if item]


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


def compute_image_metrics(image_path: Path) -> Dict[str, Any]:
    with Image.open(image_path) as image:
        image = image.convert("RGB")
        width, height = image.size
        gray = image.convert("L")
        stat = ImageStat.Stat(gray)
        return {
            "width": width,
            "height": height,
            "mean_brightness": round(float(stat.mean[0]), 2),
            "grayscale_stddev": round(float(stat.stddev[0]), 2),
        }


def keyword_match_ratio(expected_phrases: List[str], ocr_text: str) -> float:
    normalized_ocr = normalize_for_match(ocr_text)
    candidates = [normalize_for_match(item) for item in expected_phrases if normalize_for_match(item)]
    if not candidates:
        return 0.0
    matched = sum(1 for item in candidates if item and item in normalized_ocr)
    return round(matched / len(candidates), 2)


def build_attempt_review(
    shot: Dict[str, Any],
    attempt: int,
    output_dir: Path,
    max_attempts: int,
) -> Dict[str, Any]:
    shot_dir = output_dir / "attempts" / f"shot-{shot['shot_num']:02d}"
    stem = f"attempt-{attempt:02d}"
    image_path = shot_dir / f"{stem}.png"
    review_path = shot_dir / f"{stem}.review.json"

    base = {
        "shot_num": shot["shot_num"],
        "attempt": attempt,
        "status": "retry",
        "compose_eligible": False,
        "manual_decision": "pending",
        "reason_codes": [],
        "metrics": {},
        "ocr_text": "",
    }

    if not image_path.exists():
        base["status"] = "retry" if attempt < max_attempts else "fail"
        base["reason_codes"] = ["no_image_artifact"]
        safe_json_dump(review_path, base)
        return base

    metrics = compute_image_metrics(image_path)
    base["metrics"] = metrics

    ratio = metrics["width"] / metrics["height"]
    if ratio < MIN_RATIO or ratio > MAX_RATIO:
        base["reason_codes"].append("aspect_ratio_invalid")
    if metrics["grayscale_stddev"] < NEAR_BLANK_STDDEV:
        base["reason_codes"].append("near_blank")
    if metrics["mean_brightness"] < TOO_DARK:
        base["reason_codes"].append("too_dark")
    if metrics["mean_brightness"] > TOO_BRIGHT:
        base["reason_codes"].append("too_bright")

    try:
        ocr_text = extract_ocr_text(image_path)
        base["ocr_text"] = ocr_text
        ratio_score = keyword_match_ratio(normalize_expected_phrases(shot), ocr_text)
        base["metrics"]["ocr_keyword_match_ratio"] = ratio_score
        title_match = normalize_for_match(shot["ppt_visual"]["主标题"]) in normalize_for_match(ocr_text)
        if not title_match and ratio_score < OCR_MATCH_THRESHOLD:
            base["reason_codes"].append("ocr_low_confidence")
    except Exception as exc:
        base["reason_codes"].append("ocr_api_unavailable")
        base["ocr_text"] = str(exc)

    if not base["reason_codes"]:
        base["status"] = "pass"
        base["compose_eligible"] = True
        base["manual_decision"] = "approve"
    else:
        base["status"] = "retry" if attempt < max_attempts else "fail"

    safe_json_dump(review_path, base)
    return base


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
    parser = argparse.ArgumentParser(description="审核生成的幻灯片图片")
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
