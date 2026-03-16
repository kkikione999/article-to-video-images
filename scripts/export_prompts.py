#!/usr/bin/env python3
"""
导出图片生成提示词脚本
从分镜脚本中提取图片生成提示词，导出为文本文件
便于用户使用外部工具（如 Midjourney、Stable Diffusion）批量生成图片
"""

import os
import re
import argparse
from pathlib import Path
from typing import List, Dict


def parse_storyboard(storyboard_path: str) -> List[Dict]:
    """
    解析分镜脚本，提取图片生成提示词
    """
    with open(storyboard_path, 'r', encoding='utf-8') as f:
        content = f.read()

    shots = []

    # 分割成各个镜号
    # 匹配镜号部分
    shot_pattern = r'### 镜号\s*(\d+)\s*[:：]\s*([^\n]+)(.*?)(?=### 镜号|\Z)'
    matches = re.findall(shot_pattern, content, re.DOTALL)

    for match in matches:
        shot_num = int(match[0])
        shot_title = match[1].strip()
        shot_content = match[2]

        # 提取时间码
        timecode_match = re.search(r'\*\*时间码\*\*:\s*(\d+:\d+\.\d+)\s*-\s*(\d+:\d+\.\d+)', shot_content)
        start_time = timecode_match.group(1) if timecode_match else "00:00.000"
        end_time = timecode_match.group(2) if timecode_match else "00:00.000"

        # 提取图片生成提示词
        prompt_match = re.search(
            r'\*\*图片生成提示词\*\*\s*\(Image Prompt\):?\s*```\s*\n?(.*?)```',
            shot_content,
            re.DOTALL | re.IGNORECASE
        )

        if not prompt_match:
            # 尝试其他格式
            prompt_match = re.search(
                r'图片生成提示词[:：]?\s*\n```\s*\n?(.*?)```',
                shot_content,
                re.DOTALL | re.IGNORECASE
            )

        image_prompt = prompt_match.group(1).strip() if prompt_match else ""

        # 提取画面描述
        desc_match = re.search(r'\*\*画面描述\*\*:\s*([^\n]+)', shot_content)
        description = desc_match.group(1).strip() if desc_match else ""

        shots.append({
            'num': shot_num,
            'title': shot_title,
            'start_time': start_time,
            'end_time': end_time,
            'image_prompt': image_prompt,
            'description': description
        })

    return shots


def export_prompts(shots: List[Dict], output_path: str, format_type: str = 'text'):
    """
    导出提示词到文件
    """
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(f"# 图片生成提示词导出\n")
        f.write(f"# 共 {len(shots)} 个镜头\n")
        f.write(f"# 生成时间: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("=" * 80 + "\n\n")

        for shot in shots:
            f.write(f"## 镜号 {shot['num']}: {shot['title']}\n")
            f.write(f"# 时间: {shot['start_time']} - {shot['end_time']}\n")
            f.write(f"# 描述: {shot['description']}\n\n")

            if shot['image_prompt']:
                f.write(f"{shot['image_prompt']}\n")
            else:
                f.write(f"# 未找到图片生成提示词\n")

            f.write("\n" + "-" * 80 + "\n\n")


def export_csv(shots: List[Dict], output_path: str):
    """
    导出为 CSV 格式
    """
    import csv

    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['镜号', '标题', '开始时间', '结束时间', '画面描述', '图片生成提示词'])

        for shot in shots:
            writer.writerow([
                shot['num'],
                shot['title'],
                shot['start_time'],
                shot['end_time'],
                shot['description'],
                shot['image_prompt']
            ])


def export_json(shots: List[Dict], output_path: str):
    """
    导出为 JSON 格式
    """
    import json

    data = {
        'total_shots': len(shots),
        'shots': shots
    }

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def export_comfyui_workflow(shots: List[Dict], output_path: str, video_num: str = '1'):
    """
    生成 ComfyUI 批量处理工作流模板
    """
    import json

    # 基础 ComfyUI 工作流模板
    workflow = {
        "last_node_id": 10,
        "last_link_id": 9,
        "nodes": [
            {
                "id": 1,
                "type": "KSampler",
                "pos": [863, 186],
                "size": [315, 474],
                "inputs": [
                    {"name": "model", "type": "MODEL", "link": None},
                    {"name": "positive", "type": "CONDITIONING", "link": None},
                    {"name": "negative", "type": "CONDITIONING", "link": None},
                    {"name": "latent_image", "type": "LATENT", "link": None}
                ],
                "outputs": [
                    {"name": "LATENT", "type": "LATENT", "links": []}
                ],
                "widgets_values": [0, "randomize", 20, 8, "euler", "normal", 1]
            }
        ],
        "links": [],
        "groups": [],
        "prompts": []
    }

    # 添加每个镜头的提示词
    prompts = []
    for shot in shots:
        if shot['image_prompt']:
            prompts.append({
                'shot_num': shot['num'],
                'title': shot['title'],
                'prompt': shot['image_prompt'],
                'output_filename': f'video-{video_num}-image-{shot["num"]:02d}.png'
            })

    workflow['prompts'] = prompts

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(workflow, f, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser(description='从分镜脚本导出图片生成提示词')
    parser.add_argument('storyboard', help='分镜脚本文件或目录')
    parser.add_argument('-o', '--output', default='prompts.txt', help='输出文件路径')
    parser.add_argument('-f', '--format', default='text', choices=['text', 'csv', 'json', 'comfyui'],
                        help='输出格式')
    parser.add_argument('--video-num', default='1', help='视频编号（用于ComfyUI输出文件名）')
    args = parser.parse_args()

    # 处理输入
    if os.path.isfile(args.storyboard):
        storyboard_files = [args.storyboard]
    else:
        storyboard_files = [
            os.path.join(args.storyboard, f)
            for f in os.listdir(args.storyboard)
            if f.startswith('storyboard-') and f.endswith('.md')
        ]

    if not storyboard_files:
        print("❌ 未找到分镜脚本文件")
        return

    print(f"📖 找到 {len(storyboard_files)} 个分镜脚本\n")

    all_shots = []
    for sb_file in sorted(storyboard_files):
        print(f"📝 解析: {os.path.basename(sb_file)}")
        shots = parse_storyboard(sb_file)
        print(f"   ✅ 找到 {len(shots)} 个镜头")
        all_shots.extend(shots)

    print(f"\n📊 总计: {len(all_shots)} 个镜头")

    # 导出
    if args.format == 'text':
        export_prompts(all_shots, args.output)
    elif args.format == 'csv':
        if not args.output.endswith('.csv'):
            args.output += '.csv'
        export_csv(all_shots, args.output)
    elif args.format == 'json':
        if not args.output.endswith('.json'):
            args.output += '.json'
        export_json(all_shots, args.output)
    elif args.format == 'comfyui':
        if not args.output.endswith('.json'):
            args.output += '.json'
        export_comfyui_workflow(all_shots, args.output, args.video_num)

    print(f"\n✅ 已导出到: {args.output}")


if __name__ == '__main__':
    main()
