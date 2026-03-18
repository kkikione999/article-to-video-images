#!/usr/bin/env python3
"""Materialize ComfyUI workflow jobs for each storyboard shot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import List, Optional

from _single_video_utils import build_slide_spec, parse_storyboard
from comfyui_workflow import (
    build_comfyui_manifest_entries,
    prepare_comfyui_workflow,
    resolve_comfyui_options,
)
from generate_images import build_negative_prompt, build_prompt, parse_shot_numbers


def export_comfyui_workflows(
    storyboard_path: str,
    output_dir: str,
    attempt: int,
    shot_numbers: Optional[List[int]] = None,
    comfyui_base_url: Optional[str] = None,
    comfyui_workflow: Optional[str] = None,
    comfyui_style_image: Optional[str] = None,
    comfyui_timeout: Optional[int] = None,
) -> Path:
    shots = parse_storyboard(storyboard_path)
    selected = set(shot_numbers or [shot["shot_num"] for shot in shots])
    output_path = Path(output_dir).resolve()
    output_path.mkdir(parents=True, exist_ok=True)
    options = resolve_comfyui_options(
        base_url=comfyui_base_url,
        workflow_template=comfyui_workflow,
        style_image=comfyui_style_image,
        timeout_seconds=comfyui_timeout,
    )

    manifest = {
        "provider": "comfyui",
        "workflow_template": str(options.workflow_template),
        "base_url": options.base_url,
        "attempt": attempt,
        "shots": [],
    }
    for shot in shots:
        if shot["shot_num"] not in selected:
            continue
        slide_spec = build_slide_spec(shot, attempt)
        prompt = build_prompt(slide_spec)
        negative_prompt = build_negative_prompt(slide_spec)
        prepared = prepare_comfyui_workflow(
            slide_spec=slide_spec,
            prompt=prompt,
            negative_prompt=negative_prompt,
            shot_num=shot["shot_num"],
            attempt=attempt,
            output_dir=output_path,
            options=options,
        )
        manifest["shots"].append(
            build_comfyui_manifest_entries(
                shot_num=shot["shot_num"],
                attempt=attempt,
                prepared=prepared,
            )
        )

    manifest_path = output_path / "comfyui-workflow-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest_path


def main() -> None:
    parser = argparse.ArgumentParser(description="导出 ComfyUI ControlNet/IPAdapter 工作流任务")
    parser.add_argument("storyboard", help="分镜脚本路径")
    parser.add_argument("-o", "--output", required=True, help="输出目录")
    parser.add_argument("--attempt", type=int, default=1, help="语义生成轮次")
    parser.add_argument("--shots", help="只导出指定镜号，逗号分隔，例如 1,2,4")
    parser.add_argument("--comfyui-base-url", help="ComfyUI 服务地址")
    parser.add_argument("--comfyui-workflow", help="ComfyUI API workflow 模板路径")
    parser.add_argument("--comfyui-style-image", help="可选：IPAdapter 参考图路径")
    parser.add_argument("--comfyui-timeout", type=int, help="ComfyUI 单镜头超时时间（秒）")
    args = parser.parse_args()

    manifest_path = export_comfyui_workflows(
        storyboard_path=args.storyboard,
        output_dir=args.output,
        attempt=args.attempt,
        shot_numbers=parse_shot_numbers(args.shots),
        comfyui_base_url=args.comfyui_base_url,
        comfyui_workflow=args.comfyui_workflow,
        comfyui_style_image=args.comfyui_style_image,
        comfyui_timeout=args.comfyui_timeout,
    )
    print(f"✅ 已导出 ComfyUI 工作流任务: {manifest_path}")


if __name__ == "__main__":
    main()
