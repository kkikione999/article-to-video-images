#!/usr/bin/env python3
"""Orchestrate the single-video image pipeline with pluggable generators."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

from _single_video_utils import parse_storyboard, safe_json_dump, safe_json_load
from calibrate_subtitles import calibrate_subtitles
from generate_subtitles import generate_subtitles
from generate_images import generate_images_for_storyboard
from review_images import parse_manual_degraded, review_images_for_attempt

ENV_IMAGE_PROVIDER = "ARTICLE_TO_VIDEO_IMAGE_PROVIDER"


def write_run_status(run_dir: Path, **fields) -> Dict:
    status_path = run_dir / "run-status.json"
    current = safe_json_load(status_path) or {
        "phase": "generating_images",
        "terminal_outcome": None,
        "compose_ready": False,
        "blocking_reason": None,
        "shots_total": 0,
        "shots_compose_eligible": 0,
        "updated_by": "run_single_video_pipeline.py",
    }
    current.update(fields)
    current["updated_by"] = "run_single_video_pipeline.py"
    safe_json_dump(status_path, current)
    return current


def run_subprocess(args: List[str], workdir: Path) -> None:
    result = subprocess.run(args, cwd=workdir, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"命令执行失败: {' '.join(args)}")


def selected_modes(images_dir: Path) -> List[str]:
    payload = safe_json_load(images_dir / "final" / "selected-images.json", default=[]) or []
    return [item.get("selection_mode", "normal") for item in payload]


def main() -> None:
    parser = argparse.ArgumentParser(description="运行单视频图片驱动视频流程")
    parser.add_argument("--run-dir", required=True, help="运行目录")
    parser.add_argument("--analysis", required=True, help="单篇分析稿路径")
    parser.add_argument("--storyboard", required=True, help="单篇分镜稿路径")
    parser.add_argument("--model", default="qwen-image-2.0", help="图片模型")
    parser.add_argument(
        "--image-provider",
        default=os.environ.get(ENV_IMAGE_PROVIDER, "dashscope"),
        choices=["dashscope", "comfyui"],
        help="图片生成 provider",
    )
    parser.add_argument("--comfyui-base-url", help="ComfyUI 服务地址")
    parser.add_argument("--comfyui-workflow", help="ComfyUI API workflow 模板路径")
    parser.add_argument("--comfyui-style-image", help="可选：IPAdapter 参考图路径")
    parser.add_argument("--comfyui-timeout", type=int, help="ComfyUI 单镜头超时时间（秒）")
    parser.add_argument("--max-attempts", type=int, default=3, help="最大语义 attempt 数")
    parser.add_argument("--manual-degraded", help="人工降级选择，例如 3:2,5:1")
    parser.add_argument("--force-images", action="store_true", help="覆盖既有图片尝试产物")
    parser.add_argument("--audio-file", help="可选：直接复用已有音频文件")
    parser.add_argument("--asr-file", help="可选：直接复用已有 ASR JSON")
    args = parser.parse_args()

    run_dir = Path(args.run_dir).resolve()
    analysis_path = Path(args.analysis).resolve()
    storyboard_path = Path(args.storyboard).resolve()
    audio_dir = run_dir / "02-audio"
    asr_dir = run_dir / "03-asr"
    subtitle_calibration_dir = run_dir / "03.5-subtitles"
    images_dir = run_dir / "05-images" / "video-1"
    output_path = run_dir / "output" / "video-1.mp4"
    subtitle_base = run_dir / "output" / "video-1"
    audio_path = audio_dir / "video-1.mp3"
    asr_path = asr_dir / "asr-result-1.json"

    run_dir.mkdir(parents=True, exist_ok=True)
    audio_dir.mkdir(parents=True, exist_ok=True)
    asr_dir.mkdir(parents=True, exist_ok=True)
    subtitle_calibration_dir.mkdir(parents=True, exist_ok=True)
    images_dir.mkdir(parents=True, exist_ok=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    shots = parse_storyboard(str(storyboard_path))
    write_run_status(
        run_dir,
        phase="generating_images",
        terminal_outcome=None,
        compose_ready=False,
        blocking_reason=None,
        shots_total=len(shots),
        shots_compose_eligible=0,
    )

    if args.audio_file:
        shutil.copy2(Path(args.audio_file).resolve(), audio_path)
    if not audio_path.exists():
        run_subprocess(
            [
                sys.executable,
                str(Path(__file__).resolve().parent / "generate_voice_auto.py"),
                str(analysis_path),
                "-o",
                str(audio_dir),
            ],
            workdir=run_dir,
        )

    if args.asr_file:
        shutil.copy2(Path(args.asr_file).resolve(), asr_path)
    if not asr_path.exists():
        run_subprocess(
            [
                sys.executable,
                str(Path(__file__).resolve().parent / "asr_transcribe.py"),
                str(audio_path),
                "-o",
                str(asr_dir),
            ],
            workdir=run_dir,
        )

    tts_source_path = audio_dir / "video-1.tts-source.txt"
    calibrated_subtitles_path = subtitle_calibration_dir / "calibrated-video-1.json"
    calibrate_subtitles(
        asr_path=str(asr_path),
        output_path=str(calibrated_subtitles_path),
        source_text_path=str(tts_source_path) if tts_source_path.exists() else None,
        article_path=str(analysis_path),
    )
    _, ass_path = generate_subtitles(str(calibrated_subtitles_path), str(subtitle_base))

    unresolved = [shot["shot_num"] for shot in shots]
    review_payload: Optional[Dict] = None

    for attempt in range(1, args.max_attempts + 1):
        write_run_status(run_dir, phase="generating_images", blocking_reason=None)
        generate_images_for_storyboard(
            storyboard_path=str(storyboard_path),
            output_dir=str(images_dir),
            attempt=attempt,
            shot_numbers=unresolved,
            model=args.model,
            provider=args.image_provider,
            comfyui_base_url=args.comfyui_base_url,
            comfyui_workflow=args.comfyui_workflow,
            comfyui_style_image=args.comfyui_style_image,
            comfyui_timeout=args.comfyui_timeout,
            force=args.force_images,
        )

        write_run_status(run_dir, phase="reviewing_images", blocking_reason=None)
        review_payload = review_images_for_attempt(
            storyboard_path=str(storyboard_path),
            output_dir=str(images_dir),
            attempt=attempt,
            max_attempts=args.max_attempts,
            shot_numbers=unresolved,
        )
        unresolved = [shot["shot_num"] for shot in review_payload["shots"] if not shot["compose_eligible"]]
        write_run_status(
            run_dir,
            phase="reviewing_images",
            shots_compose_eligible=len(shots) - len(unresolved),
        )
        if not unresolved:
            break

    if unresolved and args.manual_degraded:
        review_payload = review_images_for_attempt(
            storyboard_path=str(storyboard_path),
            output_dir=str(images_dir),
            attempt=args.max_attempts,
            max_attempts=args.max_attempts,
            shot_numbers=[],
            manual_degraded=parse_manual_degraded(args.manual_degraded),
        )
        unresolved = [shot["shot_num"] for shot in review_payload["shots"] if not shot["compose_eligible"]]
        write_run_status(
            run_dir,
            phase="reviewing_images",
            shots_compose_eligible=len(shots) - len(unresolved),
        )

    selected = safe_json_load(images_dir / "final" / "selected-images.json", default=[]) or []
    if len(selected) != len(shots):
        write_run_status(
            run_dir,
            phase="awaiting_manual_review",
            compose_ready=False,
            blocking_reason="final_image_count_mismatch",
            shots_compose_eligible=len(selected),
        )
        raise SystemExit(2)

    if unresolved:
        write_run_status(
            run_dir,
            phase="awaiting_manual_review",
            compose_ready=False,
            blocking_reason="unresolved_shots_after_retry_budget",
            shots_compose_eligible=len(selected),
        )
        raise SystemExit(2)

    write_run_status(
        run_dir,
        phase="ready_to_compose",
        compose_ready=True,
        blocking_reason=None,
        shots_compose_eligible=len(selected),
    )

    write_run_status(run_dir, phase="composing", compose_ready=False)
    run_subprocess(
        [
            sys.executable,
            str(Path(__file__).resolve().parent / "compose_video.py"),
            "--storyboard",
            str(storyboard_path),
            "--images",
            str(images_dir / "final"),
            "--audio",
            str(audio_path),
            "--subtitles",
            str(ass_path),
            "--output",
            str(output_path),
            "--transition",
            "none",
        ],
        workdir=run_dir,
    )

    modes = selected_modes(images_dir)
    outcome = "degraded_success" if any(mode == "manual_degraded" for mode in modes) else "success"
    write_run_status(
        run_dir,
        phase="completed",
        terminal_outcome=outcome,
        compose_ready=False,
        blocking_reason=None,
        shots_compose_eligible=len(selected),
    )
    print(f"✅ 单视频流程完成，结果: {outcome}")
    print(f"📁 输出视频: {output_path}")


if __name__ == "__main__":
    main()
