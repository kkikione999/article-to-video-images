#!/usr/bin/env python3
"""
导出图片生成提示词脚本
基于结构化分镜实时生成图片提示词，便于外部工具复用
"""

import argparse
import csv
import json
import os
from datetime import datetime
from typing import Dict, List

from _single_video_utils import build_slide_spec, parse_storyboard
from generate_images import build_negative_prompt, build_prompt


def build_prompt_records(storyboard_path: str) -> List[Dict]:
    shots = parse_storyboard(storyboard_path)
    records: List[Dict] = []
    for shot in shots:
        slide_spec = build_slide_spec(shot, attempt=1)
        records.append(
            {
                "num": shot["shot_num"],
                "title": shot["title"],
                "start_time": shot["start_timecode"],
                "end_time": shot["end_timecode"],
                "shot_type": shot["shot_type"],
                "visual_goal": shot["visual_goal"],
                "description": " / ".join(shot["subject_elements"][:3]),
                "image_prompt": build_prompt(slide_spec),
                "negative_prompt": build_negative_prompt(slide_spec),
            }
        )
    return records


def export_prompts(records: List[Dict], output_path: str) -> None:
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("# 图片生成提示词导出\n")
        f.write(f"# 共 {len(records)} 个镜头\n")
        f.write(f"# 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("=" * 80 + "\n\n")

        for record in records:
            f.write(f"## 镜号 {record['num']}: {record['title']}\n")
            f.write(f"# 时间: {record['start_time']} - {record['end_time']}\n")
            f.write(f"# 镜头类型: {record['shot_type']}\n")
            f.write(f"# 视觉目标: {record['visual_goal']}\n")
            f.write(f"# 画面摘要: {record['description']}\n\n")
            f.write(record["image_prompt"] + "\n\n")
            f.write("# Negative Prompt\n")
            f.write(record["negative_prompt"] + "\n")
            f.write("\n" + "-" * 80 + "\n\n")


def export_csv(records: List[Dict], output_path: str) -> None:
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["镜号", "标题", "开始时间", "结束时间", "镜头类型", "视觉目标", "画面摘要", "图片生成提示词", "负面提示词"]
        )
        for record in records:
            writer.writerow(
                [
                    record["num"],
                    record["title"],
                    record["start_time"],
                    record["end_time"],
                    record["shot_type"],
                    record["visual_goal"],
                    record["description"],
                    record["image_prompt"],
                    record["negative_prompt"],
                ]
            )


def export_json(records: List[Dict], output_path: str) -> None:
    payload = {
        "total_shots": len(records),
        "shots": records,
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def export_comfyui_workflow(records: List[Dict], output_path: str, video_num: str = "1") -> None:
    workflow = {
        "last_node_id": 10,
        "last_link_id": 9,
        "nodes": [],
        "links": [],
        "groups": [],
        "prompts": [],
    }

    for record in records:
        workflow["prompts"].append(
            {
                "shot_num": record["num"],
                "title": record["title"],
                "shot_type": record["shot_type"],
                "prompt": record["image_prompt"],
                "negative_prompt": record["negative_prompt"],
                "output_filename": f"video-{video_num}-image-{record['num']:02d}.png",
            }
        )

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(workflow, f, ensure_ascii=False, indent=2)


def main() -> None:
    parser = argparse.ArgumentParser(description="从结构化分镜导出图片生成提示词")
    parser.add_argument("storyboard", help="分镜脚本文件或目录")
    parser.add_argument("-o", "--output", default="prompts.txt", help="输出文件路径")
    parser.add_argument(
        "-f",
        "--format",
        default="text",
        choices=["text", "csv", "json", "comfyui"],
        help="输出格式",
    )
    parser.add_argument("--video-num", default="1", help="视频编号（用于 ComfyUI 输出文件名）")
    args = parser.parse_args()

    if os.path.isfile(args.storyboard):
        storyboard_files = [args.storyboard]
    else:
        storyboard_files = [
            os.path.join(args.storyboard, f)
            for f in os.listdir(args.storyboard)
            if f.startswith("storyboard-") and f.endswith(".md")
        ]

    if not storyboard_files:
        print("❌ 未找到分镜脚本文件")
        return

    print(f"📖 找到 {len(storyboard_files)} 个分镜脚本\n")

    all_records: List[Dict] = []
    for sb_file in sorted(storyboard_files):
        print(f"📝 解析: {os.path.basename(sb_file)}")
        records = build_prompt_records(sb_file)
        print(f"   ✅ 找到 {len(records)} 个镜头")
        all_records.extend(records)

    print(f"\n📊 总计: {len(all_records)} 个镜头")

    if args.format == "text":
        export_prompts(all_records, args.output)
    elif args.format == "csv":
        if not args.output.endswith(".csv"):
            args.output += ".csv"
        export_csv(all_records, args.output)
    elif args.format == "json":
        if not args.output.endswith(".json"):
            args.output += ".json"
        export_json(all_records, args.output)
    elif args.format == "comfyui":
        if not args.output.endswith(".json"):
            args.output += ".json"
        export_comfyui_workflow(all_records, args.output, args.video_num)

    print(f"\n✅ 已导出到: {args.output}")


if __name__ == "__main__":
    main()
