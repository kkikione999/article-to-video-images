#!/usr/bin/env python3
"""Generate PPT-style slide images with Alibaba Cloud Model Studio."""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import dashscope
import requests
from dashscope import MultiModalConversation

from _single_video_utils import (
    build_slide_spec,
    expected_output_filenames,
    parse_storyboard,
    safe_json_dump,
)

SIZE_PLAN = ["1792*1008", "1664*928", "1280*720"]
INTER_SHOT_DELAY_SECONDS = 2.0


def _obj_get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _serialize_response(response: Any) -> Dict[str, Any]:
    output = _obj_get(response, "output", {})
    serialized_results = []
    if _obj_get(output, "results", None):
        results = _obj_get(output, "results", []) or []
        for item in results:
            serialized_results.append(
                {
                    "url": _obj_get(item, "url"),
                    "orig_prompt": _obj_get(item, "orig_prompt"),
                    "actual_prompt": _obj_get(item, "actual_prompt"),
                }
            )
    else:
        choices = _obj_get(output, "choices", []) or []
        for choice in choices:
            message = _obj_get(choice, "message", {})
            content = _obj_get(message, "content", []) or []
            for item in content:
                if _obj_get(item, "image"):
                    serialized_results.append({"url": _obj_get(item, "image")})
    return {
        "status_code": _obj_get(response, "status_code"),
        "code": _obj_get(response, "code"),
        "message": _obj_get(response, "message"),
        "request_id": _obj_get(response, "request_id"),
        "output": {"results": serialized_results},
        "usage": _obj_get(response, "usage"),
    }


def parse_shot_numbers(raw: Optional[str]) -> Optional[List[int]]:
    if not raw:
        return None
    values = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        values.append(int(item))
    return values or None


def ensure_api_key() -> str:
    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError("未设置 DASHSCOPE_API_KEY 环境变量")
    dashscope.api_key = api_key
    return api_key


def build_prompt(slide_spec: Dict[str, Any]) -> str:
    layout_map = {
        "CenterLayout": "居中封面布局",
        "SplitLayout": "左右分栏布局",
        "StackLayout": "上下分层布局",
        "GridLayout": "信息网格布局",
        "TripleLayout": "三列信息布局",
        "CardLayout": "卡片式摘要布局",
    }
    lines = [
        "请生成一张 16:9 横版中文科技讲解幻灯片。",
        "这是一页正式的企业科技演示文稿，用于视频正文讲解，不是海报，不是封面，不是杂志排版。",
        "画面中的上屏文字只能使用我下面列出的中文文案，不要出现额外英文，不要出现布局名，不要出现提示语，不要出现说明文字。",
        f"版式要求：{layout_map.get(slide_spec['layout_family'], '卡片式摘要布局')}。",
        f"页面大标题只写：{slide_spec['title']}",
    ]
    if slide_spec["subtitle"]:
        lines.append(f"页面副标题只写：{slide_spec['subtitle']}")
    if slide_spec["bullets"]:
        lines.append("页面需要 2 到 3 条简短项目符号，只能使用这些中文短句：")
        lines.extend([f"- {item}" for item in slide_spec["bullets"]])
    if slide_spec["data_cards"]:
        lines.append("页面需要独立数据卡，只能使用这些卡片文字：")
        lines.extend([f"- {item}" for item in slide_spec["data_cards"]])
    if slide_spec["diagram_hint"]:
        lines.append(f"配图要求：{slide_spec['diagram_hint']}")
    if slide_spec["data_layer"]:
        lines.append("辅助背景信息：")
        lines.extend([f"- {k}: {v}" for k, v in slide_spec["data_layer"].items()])
    lines.extend(
        [
            "视觉风格：蓝白科技感，干净留白，信息卡片清晰，适合企业技术演示。",
            "文字要求：简体中文，大字清晰，高对比度，不要改写标题，不要乱码，不要写成长段落，不要把本段指令文字写进画面。",
            "图形要求：使用简单图标、卡片、箭头、分栏和信息图风格，但不要把这些要求文字写到画面中。",
        ]
    )
    return "\n".join(lines)


def build_negative_prompt() -> str:
    return (
        "movie poster, cinematic poster, tiny unreadable text, dense paragraph, watermark, "
        "photo collage, handwritten font, distorted characters, garbled Chinese text, "
        "irrelevant brand logos, cluttered composition, layout label, prompt text, English copy"
    )


def attempt_dir(output_dir: Path, shot_num: int) -> Path:
    return output_dir / "attempts" / f"shot-{shot_num:02d}"


def attempt_stem(shot_num: int, attempt: int) -> str:
    return f"attempt-{attempt:02d}"


def download_image(url: str, destination: Path) -> None:
    response = requests.get(url, timeout=120)
    response.raise_for_status()
    destination.write_bytes(response.content)


def call_image_api(prompt: str, negative_prompt: str, model: str, size: str) -> Any:
    if model.startswith("qwen-image-2.0"):
        messages = [{"role": "user", "content": [{"text": prompt}]}]
        return MultiModalConversation.call(
            model=model,
            messages=messages,
            stream=False,
            n=1,
            watermark=False,
            negative_prompt=negative_prompt,
            prompt_extend=False,
            size=size,
        )
    return dashscope.ImageSynthesis.call(
        model=model,
        prompt=prompt,
        negative_prompt=negative_prompt,
        n=1,
        size=size,
        watermark=False,
        prompt_extend=False,
    )


def should_try_next_size(error_text: str) -> bool:
    text = (error_text or "").lower()
    size_signals = ["size", "resolution", "image size", "invalidparameter", "invalid parameter"]
    rate_signals = ["throttling.ratequota", "rate limit", "429"]
    if any(signal in text for signal in rate_signals):
        return False
    return any(signal in text for signal in size_signals)


def generate_attempt(
    slide_spec: Dict[str, Any],
    shot_num: int,
    attempt: int,
    output_dir: Path,
    model: str,
    force: bool = False,
) -> Dict[str, Any]:
    shot_attempt_dir = attempt_dir(output_dir, shot_num)
    shot_attempt_dir.mkdir(parents=True, exist_ok=True)
    stem = attempt_stem(shot_num, attempt)
    png_path = shot_attempt_dir / f"{stem}.png"
    prompt_path = shot_attempt_dir / f"{stem}.prompt.txt"
    request_path = shot_attempt_dir / f"{stem}.request.json"
    response_path = shot_attempt_dir / f"{stem}.response.json"

    if png_path.exists() and not force:
        return {
            "shot_num": shot_num,
            "attempt": attempt,
            "status": "cached",
            "image_path": str(png_path),
            "request_path": str(request_path),
            "response_path": str(response_path),
        }

    prompt = build_prompt(slide_spec)
    negative_prompt = build_negative_prompt()
    prompt_path.write_text(prompt, encoding="utf-8")

    request_payload = {
        "shot_num": shot_num,
        "attempt": attempt,
        "model": model,
        "size_plan": SIZE_PLAN,
        "semantic_attempt": True,
        "slide_spec": slide_spec,
        "negative_prompt": negative_prompt,
    }
    safe_json_dump(request_path, request_payload)

    last_response: Dict[str, Any] = {}
    last_error = None
    for size in SIZE_PLAN:
        try:
            response = call_image_api(prompt, negative_prompt, model, size)
            last_response = _serialize_response(response)
            last_response["requested_size"] = size
            result = (last_response.get("output") or {}).get("results") or []
            url = result[0]["url"] if result else None
            if not url:
                last_error = f"图片接口未返回 URL，size={size}"
                continue
            try:
                download_image(url, png_path)
            except Exception as exc:
                try:
                    download_image(url, png_path)
                except Exception as second_exc:
                    last_error = f"图片下载失败: {second_exc}"
                    last_response["download_error"] = str(second_exc)
                    continue
            safe_json_dump(response_path, last_response)
            return {
                "shot_num": shot_num,
                "attempt": attempt,
                "status": "generated",
                "image_path": str(png_path),
                "request_path": str(request_path),
                "response_path": str(response_path),
            }
        except Exception as exc:
            last_error = str(exc)
            last_response = {
                "requested_size": size,
                "code": "exception",
                "message": str(exc),
            }
            if should_try_next_size(str(exc)):
                continue
            break

    last_response["final_error"] = last_error
    safe_json_dump(response_path, last_response)
    return {
        "shot_num": shot_num,
        "attempt": attempt,
        "status": "error",
        "error": last_error,
        "request_path": str(request_path),
        "response_path": str(response_path),
        "image_path": str(png_path),
    }


def generate_images_for_storyboard(
    storyboard_path: str,
    output_dir: str,
    attempt: int,
    shot_numbers: Optional[Iterable[int]] = None,
    model: str = "qwen-image-2.0",
    force: bool = False,
) -> Dict[str, Any]:
    ensure_api_key()
    shots = parse_storyboard(storyboard_path)
    selected = (
        set(shot["shot_num"] for shot in shots)
        if shot_numbers is None
        else set(shot_numbers)
    )
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    results = []
    for shot in shots:
        if shot["shot_num"] not in selected:
            continue
        slide_spec = build_slide_spec(shot, attempt)
        result = generate_attempt(
            slide_spec=slide_spec,
            shot_num=shot["shot_num"],
            attempt=attempt,
            output_dir=output_path,
            model=model,
            force=force,
        )
        results.append(result)
        time.sleep(INTER_SHOT_DELAY_SECONDS)

    payload = {"attempt": attempt, "model": model, "results": results}
    safe_json_dump(output_path / f"generation-attempt-{attempt:02d}.json", payload)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="生成阿里云 PPT 风格图片")
    parser.add_argument("storyboard", help="分镜脚本路径")
    parser.add_argument("-o", "--output", required=True, help="输出目录，例如 05-images/video-1")
    parser.add_argument("--attempt", type=int, default=1, help="语义生成轮次，从 1 开始")
    parser.add_argument("--shots", help="只生成指定镜号，逗号分隔，例如 1,2,4")
    parser.add_argument("--model", default="qwen-image-2.0", help="图片模型")
    parser.add_argument("--force", action="store_true", help="覆盖已有尝试产物")
    args = parser.parse_args()

    shot_numbers = parse_shot_numbers(args.shots)
    payload = generate_images_for_storyboard(
        storyboard_path=args.storyboard,
        output_dir=args.output,
        attempt=args.attempt,
        shot_numbers=shot_numbers,
        model=args.model,
        force=args.force,
    )
    print(f"✅ 已完成 attempt {args.attempt}，共处理 {len(payload['results'])} 个镜头")


if __name__ == "__main__":
    main()
