#!/usr/bin/env python3
"""
视频合成脚本
将图片序列与音频合成为最终视频
根据ASR时间码精确控制画面切换
"""

import os
import re
import json
import argparse
import subprocess
import shutil
from pathlib import Path
from typing import List, Dict, Tuple, Optional


def resolve_tool(explicit_path: str, fallback_name: str) -> str:
    if os.path.exists(explicit_path):
        return explicit_path
    found = shutil.which(fallback_name)
    return found or fallback_name


FFMPEG_BIN = resolve_tool('/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg', 'ffmpeg')
FFPROBE_BIN = resolve_tool('/opt/homebrew/opt/ffmpeg-full/bin/ffprobe', 'ffprobe')


def ffmpeg_supports_filter(filter_name: str) -> bool:
    result = subprocess.run([FFMPEG_BIN, '-filters'], capture_output=True, text=True)
    return result.returncode == 0 and filter_name in result.stdout


def parse_srt_time(value: str) -> float:
    hhmmss, millis = value.split(',', 1)
    hours, minutes, seconds = hhmmss.split(':')
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(millis) / 1000.0


def parse_srt_file(path: str) -> List[Dict[str, str]]:
    content = Path(path).read_text(encoding='utf-8').strip()
    if not content:
        return []
    blocks = re.split(r'\n\s*\n', content)
    items = []
    for block in blocks:
        lines = [line.rstrip() for line in block.splitlines() if line.strip()]
        if len(lines) < 3:
            continue
        time_line = lines[1]
        if ' --> ' not in time_line:
            continue
        start, end = time_line.split(' --> ', 1)
        text = '\n'.join(lines[2:])
        items.append({'start': start, 'end': end, 'text': text})
    return items


def escape_drawtext_text(text: str) -> str:
    escaped = text.replace('\\', r'\\')
    escaped = escaped.replace(':', r'\:')
    escaped = escaped.replace("'", r"\'")
    escaped = escaped.replace('%', r'\%')
    escaped = escaped.replace(',', r'\,')
    escaped = escaped.replace('[', r'\[').replace(']', r'\]')
    return escaped


def write_drawtext_filter_script(subtitle_path: str, output_path: str) -> None:
    entries = parse_srt_file(subtitle_path)
    fontfile = '/System/Library/Fonts/Supplemental/Songti.ttc'
    chain = []
    for item in entries:
        start = parse_srt_time(item['start'])
        end = parse_srt_time(item['end'])
        text = escape_drawtext_text(item['text']).replace('\n', r'\n')
        chain.append(
            "drawtext="
            f"fontfile={fontfile}:"
            f"text='{text}':"
            "fontcolor=white:"
            "fontsize=34:"
            "line_spacing=6:"
            "borderw=3:"
            "bordercolor=black@0.95:"
            "box=1:"
            "boxcolor=black@0.45:"
            "boxborderw=12:"
            "x=(w-text_w)/2:"
            "y=h-210-text_h:"
            f"enable='between(t,{start:.3f},{end:.3f})'"
        )
    Path(output_path).write_text(','.join(chain) + '\n', encoding='utf-8')


def parse_storyboard(storyboard_path: str) -> List[Dict]:
    """
    解析分镜脚本，提取时间码和图片信息
    """
    with open(storyboard_path, 'r', encoding='utf-8') as f:
        content = f.read()

    shots = []

    # 使用正则表达式匹配每个镜号的信息
    shot_pattern = r'### 镜号\s*(\d+)\s*[:：]\s*([^\n]*)\n.*?-\s*\*\*时间码\*\*:\s*(\d+:\d+\.\d+)\s*-\s*(\d+:\d+\.\d+)'

    matches = re.findall(shot_pattern, content, re.DOTALL)

    for match in matches:
        shot_num = int(match[0])
        shot_title = match[1].strip()
        start_timecode = match[2]
        end_timecode = match[3]

        # 转换时间码为秒
        start_sec = timecode_to_seconds(start_timecode)
        end_sec = timecode_to_seconds(end_timecode)
        duration = end_sec - start_sec

        shots.append({
            'num': shot_num,
            'title': shot_title,
            'start_timecode': start_timecode,
            'end_timecode': end_timecode,
            'start_sec': start_sec,
            'end_sec': end_sec,
            'duration': duration
        })

    return shots


def timecode_to_seconds(timecode: str) -> float:
    """将时间码转换为秒"""
    # 格式: 00:05.320 或 01:30.500
    parts = timecode.split(':')
    if len(parts) == 2:
        minutes = int(parts[0])
        seconds = float(parts[1])
        return minutes * 60 + seconds
    return 0.0


def get_image_files(images_dir: str) -> List[str]:
    """获取图片目录中的所有图片文件，按名称排序"""
    image_extensions = ('.png', '.jpg', '.jpeg', '.webp', '.bmp')
    images = [
        os.path.join(images_dir, f)
        for f in os.listdir(images_dir)
        if f.lower().endswith(image_extensions)
    ]
    return sorted(images)


def finalize_video(
    temp_video: str,
    audio_path: str,
    output_path: str,
    subtitle_path: Optional[str] = None,
) -> bool:
    print(f"   🔊 添加音频{'和字幕' if subtitle_path else ''}...")

    if subtitle_path and ffmpeg_supports_filter('subtitles'):
        temp_dir = os.path.dirname(os.path.abspath(temp_video))
        ext = os.path.splitext(subtitle_path)[1] or '.srt'
        subtitle_copy = os.path.join(temp_dir, f'subtitles{ext}')
        shutil.copy2(subtitle_path, subtitle_copy)
        cmd = [
            FFMPEG_BIN,
            '-y',
            '-i',
            os.path.basename(temp_video),
            '-i',
            audio_path,
            '-vf',
            f"subtitles=filename={os.path.basename(subtitle_copy)}",
            '-c:v',
            'libx264',
            '-preset',
            'medium',
            '-crf',
            '20',
            '-c:a',
            'aac',
            '-b:a',
            '192k',
            '-shortest',
            output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=temp_dir)
        if result.returncode != 0:
            print(f"   ❌ 添加音频/字幕失败: {result.stderr}")
            return False
        return True

    cmd = [FFMPEG_BIN, '-y', '-i', temp_video, '-i', audio_path]
    if subtitle_path:
        cmd.extend(
            [
                '-i',
                subtitle_path,
                '-map',
                '0:v:0',
                '-map',
                '1:a:0',
                '-map',
                '2:0',
                '-c:v',
                'copy',
                '-c:a',
                'aac',
                '-b:a',
                '192k',
                '-c:s',
                'mov_text',
                '-metadata:s:s:0',
                'language=zho',
                '-disposition:s:0',
                'default',
                '-shortest',
                output_path,
            ]
        )
    else:
        cmd.extend(
            [
                '-c:v',
                'copy',
                '-c:a',
                'aac',
                '-b:a',
                '192k',
                '-shortest',
                output_path,
            ]
        )

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"   ❌ 添加音频/字幕失败: {result.stderr}")
        return False
    return True


def create_ffmpeg_input_file(shots: List[Dict], images: List[str], output_path: str) -> str:
    """
    创建 FFmpeg 输入文件列表
    格式: https://ffmpeg.org/ffmpeg-formats.html#concat
    """
    input_file = output_path + '.input.txt'

    with open(input_file, 'w', encoding='utf-8') as f:
        for i, shot in enumerate(shots):
            if i < len(images):
                image_path = images[i]
                duration = shot['duration']
                # 使用相对路径或绝对路径
                f.write(f"file '{os.path.abspath(image_path)}'\n")
                f.write(f"duration {duration:.3f}\n")

    return input_file


def compose_video_ffmpeg(
    shots: List[Dict],
    images: List[str],
    audio_path: str,
    output_path: str,
    subtitle_path: Optional[str] = None,
    resolution: Tuple[int, int] = (1920, 1080),
    fps: int = 30,
    transition_duration: float = 0.5
):
    """
    使用 FFmpeg 合成视频
    """
    print("🎬 开始合成视频...")

    # 创建临时目录
    temp_dir = output_path + '_temp'
    os.makedirs(temp_dir, exist_ok=True)

    try:
        # 方法：使用 concat demuxer + 音频混合
        # 1. 首先将每个图片转换为对应时长的视频片段
        # 2. 然后合并所有片段
        # 3. 最后添加音频

        segment_files = []

        for i, shot in enumerate(shots):
            if i >= len(images):
                print(f"   ⚠️  警告：图片数量不足，缺少第 {i+1} 张图片")
                break

            image_path = images[i]
            duration = shot['duration']
            segment_file = os.path.join(temp_dir, f'segment_{i:04d}.mp4')

            print(f"   🖼️  处理片段 {i+1}/{len(shots)}: {shot['title'][:30]}... ({duration:.2f}s)")

            # 将图片转换为视频片段
            # 使用 zoompan 滤镜添加轻微的缩放动画效果
            cmd = [
                FFMPEG_BIN, '-y',
                '-loop', '1',
                '-i', image_path,
                '-c:v', 'libx264',
                '-tune', 'stillimage',
                '-pix_fmt', 'yuv420p',
                '-vf', f'scale={resolution[0]}:{resolution[1]}:force_original_aspect_ratio=decrease,pad={resolution[0]}:{resolution[1]}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}',
                '-t', str(duration),
                '-c:v', 'libx264',
                '-preset', 'medium',
                '-crf', '23',
                segment_file
            ]

            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                print(f"   ❌ 生成片段 {i+1} 失败: {result.stderr}")
                continue

            segment_files.append(segment_file)

        if not segment_files:
            print("❌ 没有成功生成任何视频片段")
            return False

        # 创建 concat 文件列表
        concat_file = os.path.join(temp_dir, 'concat.txt')
        with open(concat_file, 'w') as f:
            for segment in segment_files:
                f.write(f"file '{segment}'\n")

        # 合并所有片段（无音频版本）
        temp_video = os.path.join(temp_dir, 'temp_video.mp4')
        print(f"   🔄 合并 {len(segment_files)} 个片段...")

        cmd = [
            FFMPEG_BIN, '-y',
            '-f', 'concat',
            '-safe', '0',
            '-i', concat_file,
            '-c', 'copy',
            temp_video
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"   ❌ 合并片段失败: {result.stderr}")
            return False

        if not finalize_video(temp_video, audio_path, output_path, subtitle_path):
            return False

        print(f"   ✅ 视频合成完成: {output_path}")
        return True

    finally:
        # 清理临时文件
        import shutil
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)


def compose_video_with_transitions(
    shots: List[Dict],
    images: List[str],
    audio_path: str,
    output_path: str,
    subtitle_path: Optional[str] = None,
    resolution: Tuple[int, int] = (1920, 1080),
    fps: int = 30,
    transition_type: str = 'fade'
):
    """
    使用 FFmpeg 合成视频，添加转场效果
    使用复杂滤镜实现转场
    """
    print("🎬 开始合成视频（带转场效果）...")

    temp_dir = output_path + '_temp'
    os.makedirs(temp_dir, exist_ok=True)

    try:
        # 如果只有一张图片或没有图片，直接处理
        if len(images) == 0:
            print("❌ 没有图片可处理")
            return False

        if len(images) == 1:
            # 单张图片 + 音频
            duration = shots[0]['duration'] if shots else 10.0
            temp_video = os.path.join(temp_dir, 'temp_single.mp4')
            cmd = [
                FFMPEG_BIN, '-y',
                '-loop', '1',
                '-i', images[0],
                '-c:v', 'libx264',
                '-tune', 'stillimage',
                '-vf', f'scale={resolution[0]}:{resolution[1]}:force_original_aspect_ratio=decrease,pad={resolution[0]}:{resolution[1]}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}',
                '-t', str(duration),
                temp_video
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                print(f"❌ 单图视频片段生成失败: {result.stderr}")
                return False
            ok = finalize_video(temp_video, audio_path, output_path, subtitle_path)
            if ok:
                print(f"✅ 视频合成完成: {output_path}")
            return ok

        # 多张图片：使用复杂滤镜链
        # 构建 FFmpeg 命令
        inputs = []
        filters = []
        current_offset = 0

        for i, image_path in enumerate(images[:len(shots)]):
            inputs.extend(['-loop', '1', '-i', image_path])

        # 构建 filter_complex
        filter_parts = []

        for i in range(len(images[:len(shots)])):
            # 为每个输入添加 fps 和格式转换
            filter_parts.append(f'[{i}:v]fps={fps},scale={resolution[0]}:{resolution[1]}:force_original_aspect_ratio=decrease,pad={resolution[0]}:{resolution[1]}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p[v{i}];')

        # 合并视频（简单连接）
        video_labels = ''.join([f'[v{i}]' for i in range(len(images[:len(shots)]))])
        filter_parts.append(f'{video_labels}concat=n={len(images[:len(shots)])}:v=1:a=0[outv]')

        filter_complex = ''.join(filter_parts)

        temp_video = os.path.join(temp_dir, 'temp_video.mp4')
        cmd = [
            FFMPEG_BIN, '-y',
            *inputs,
            '-filter_complex', filter_complex,
            '-map', '[outv]',
            '-c:v', 'libx264',
            '-preset', 'medium',
            '-crf', '23',
            temp_video
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            print(f"❌ 视频合成失败: {result.stderr}")
            return False

        if not finalize_video(temp_video, audio_path, output_path, subtitle_path):
            return False

        print(f"✅ 视频合成完成: {output_path}")
        return True

    finally:
        # 清理临时文件
        import shutil
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)


def main():
    parser = argparse.ArgumentParser(description='将图片序列与音频合成为视频')
    parser.add_argument('--storyboard', required=True, help='分镜脚本文件路径')
    parser.add_argument('--images', required=True, help='图片目录路径')
    parser.add_argument('--audio', required=True, help='音频文件路径')
    parser.add_argument('--output', required=True, help='输出视频文件路径')
    parser.add_argument('--subtitles', help='字幕文件路径（推荐 ASS 或 SRT）')
    parser.add_argument('--width', type=int, default=1920, help='视频宽度')
    parser.add_argument('--height', type=int, default=1080, help='视频高度')
    parser.add_argument('--fps', type=int, default=30, help='帧率')
    parser.add_argument('--transition', default='fade', choices=['fade', 'none'], help='转场类型')
    args = parser.parse_args()

    # 检查输入文件
    if not os.path.exists(args.storyboard):
        print(f"❌ 分镜脚本不存在: {args.storyboard}")
        return

    if not os.path.exists(args.images):
        print(f"❌ 图片目录不存在: {args.images}")
        return

    if not os.path.exists(args.audio):
        print(f"❌ 音频文件不存在: {args.audio}")
        return

    if args.subtitles and not os.path.exists(args.subtitles):
        print(f"❌ 字幕文件不存在: {args.subtitles}")
        return

    # 解析分镜脚本
    print("📖 解析分镜脚本...")
    shots = parse_storyboard(args.storyboard)
    print(f"   ✅ 找到 {len(shots)} 个镜头")

    # 获取图片文件
    print("🖼️  扫描图片文件...")
    images = get_image_files(args.images)
    print(f"   ✅ 找到 {len(images)} 张图片")

    if len(images) < len(shots):
        print(f"   ⚠️  警告: 图片数量 ({len(images)}) 少于镜头数量 ({len(shots)})")
        print(f"   将只使用 {len(images)} 个镜头")

    # 创建输出目录
    os.makedirs(os.path.dirname(os.path.abspath(args.output)) or '.', exist_ok=True)

    # 合成视频
    resolution = (args.width, args.height)

    if args.transition == 'none':
        success = compose_video_ffmpeg(
            shots, images, args.audio, args.output,
            args.subtitles,
            resolution, args.fps
        )
    else:
        success = compose_video_with_transitions(
            shots, images, args.audio, args.output,
            args.subtitles,
            resolution, args.fps, args.transition
        )

    if success:
        # 获取输出视频信息
        probe = subprocess.run(
            [FFPROBE_BIN, '-v', 'error', '-show_entries', 'format=duration',
             '-of', 'default=noprint_wrappers=1:nokey=1', args.output],
            capture_output=True, text=True
        )
        duration = float(probe.stdout.strip()) if probe.returncode == 0 else 0

        print("\n🎉 视频合成完成!")
        print(f"   📁 输出文件: {args.output}")
        print(f"   ⏱️  视频时长: {duration:.2f}s")
        print(f"   📐 分辨率: {args.width}x{args.height}")
        print(f"   🎞️  帧率: {args.fps}fps")
    else:
        print("\n❌ 视频合成失败")


if __name__ == '__main__':
    main()
