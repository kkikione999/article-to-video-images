#!/usr/bin/env python3
"""
自动语音合成脚本 - 适用于 article-to-video-images skill
- 读取 01-analysis/ 目录下的分析文章
- 如果 DASHSCOPE_API_KEY 存在：使用阿里云 CosyVoice 生成真实语音
- 如果不存在：生成静音占位音频
"""

import os
import sys
import argparse
import re
import subprocess
import json

def check_api_key():
    """检查是否存在阿里云 API Key"""
    return os.environ.get('DASHSCOPE_API_KEY')

def extract_text_from_article(article_path):
    """从分析文章中提取需要朗读的文本"""
    with open(article_path, 'r', encoding='utf-8') as f:
        content = f.read()

    lines = content.split('\n')
    text_lines = []

    for line in lines:
        # 跳过标题行（# 开头）
        if line.startswith('# '):
            continue
        # 跳过分隔线和元信息
        if line.startswith('**') or line.startswith('---') or line.startswith('|'):
            continue
        # 跳过空行和markdown表格
        if line.strip() and not line.startswith('|'):
            # 移除markdown标记
            clean_line = re.sub(r'\*\*|\*|#|\[|\]|\([^)]+\)', '', line).strip()
            if clean_line:
                text_lines.append(clean_line)

    return '\n'.join(text_lines)

def generate_silent_audio(duration_seconds, output_path):
    """使用 ffmpeg 生成静音音频"""
    try:
        cmd = [
            'ffmpeg', '-y', '-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=mono',
            '-t', str(duration_seconds), '-acodec', 'libmp3lame', '-q:a', '9',
            output_path
        ]
        subprocess.run(cmd, capture_output=True, check=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ 生成静音音频失败: {e}")
        return False

# 默认音色 (使用克隆音色)
DEFAULT_VOICE = "cosyvoice-v3-flash-bailian-d443fc1fefd34d83933974a218fb17d5"

# 模型版本必须与克隆音色匹配
DEFAULT_MODEL = "cosyvoice-v3-flash"

def generate_cosyvoice_audio(text, output_path, voice_id=DEFAULT_VOICE):
    """使用阿里云 CosyVoice 生成语音"""
    try:
        import dashscope
        from dashscope.audio.tts_v2 import SpeechSynthesizer

        dashscope.api_key = os.environ.get('DASHSCOPE_API_KEY')

        synthesizer = SpeechSynthesizer(model=DEFAULT_MODEL, voice=voice_id)
        audio = synthesizer.call(text)

        if audio:
            with open(output_path, 'wb') as f:
                f.write(audio)
            return True
        else:
            print(f"❌ 语音合成失败")
            return False
    except ImportError:
        print("❌ 未安装 dashscope，请运行: pip install dashscope")
        return False
    except Exception as e:
        print(f"❌ 语音合成出错: {e}")
        return False

def estimate_duration(text):
    """估算文本朗读时长（按200字/分钟计算）"""
    char_count = len(text)
    minutes = char_count / 200
    seconds = minutes * 60
    return max(int(seconds) + 30, 60)  # 最少60秒，加30秒缓冲

def main():
    parser = argparse.ArgumentParser(description='自动生成语音（支持真实语音或静音占位）')
    parser.add_argument('article', help='分析文章文件路径或目录（01-analysis/）')
    parser.add_argument('-o', '--output', default='02-audio/', help='输出目录')
    parser.add_argument('-v', '--voice', default=DEFAULT_VOICE, help='音色ID')
    args = parser.parse_args()

    os.makedirs(args.output, exist_ok=True)

    # 检查 API Key
    api_key = check_api_key()
    if api_key:
        print("✅ 检测到 DASHSCOPE_API_KEY，将使用阿里云 CosyVoice 生成真实语音")
    else:
        print("⚠️ 未检测到 DASHSCOPE_API_KEY，将生成静音占位音频")
        print("   如需真实语音，请先设置: export DASHSCOPE_API_KEY='your-key'")

    # 处理文章文件
    if os.path.isfile(args.article):
        articles = [args.article]
    else:
        # 读取 01-analysis/ 目录下的 member-*.md 文件
        articles = [os.path.join(args.article, f) for f in os.listdir(args.article)
                   if f.startswith('member-') and f.endswith('.md')]

    if not articles:
        print("⚠️ 未找到分析文章文件（member-*.md）")
        return

    print(f"📄 找到 {len(articles)} 个分析文章\n")

    for article_path in sorted(articles):
        print(f"📝 处理: {os.path.basename(article_path)}")

        text = extract_text_from_article(article_path)
        if not text.strip():
            print(f"⚠️ 跳过空文件")
            continue

        # 生成 video-X.mp3 文件名
        base_name = os.path.basename(article_path)
        # 从 member-X-topic.md 提取 X
        match = re.match(r'member-(\d+)', base_name)
        if match:
            video_num = match.group(1)
        else:
            video_num = '1'

        output_name = f"video-{video_num}.mp3"
        output_path = os.path.join(args.output, output_name)
        text_output_path = os.path.join(args.output, f"video-{video_num}.tts-source.txt")
        meta_output_path = os.path.join(args.output, f"video-{video_num}.tts-source.json")

        with open(text_output_path, 'w', encoding='utf-8') as f:
            f.write(text)
        with open(meta_output_path, 'w', encoding='utf-8') as f:
            json.dump(
                {
                    "article_file": os.path.abspath(article_path),
                    "audio_file": output_name,
                    "text_file": os.path.basename(text_output_path),
                    "char_count": len(text),
                },
                f,
                ensure_ascii=False,
                indent=2,
            )

        if api_key:
            # 生成真实语音
            print(f"   生成真实语音...")
            if generate_cosyvoice_audio(text, output_path, args.voice):
                print(f"   ✅ 已保存: {output_path}")
                print(f"   ✅ 已保存 TTS 文本: {text_output_path}")
        else:
            # 生成静音占位
            duration = estimate_duration(text)
            print(f"   生成静音占位音频（约 {duration} 秒）...")
            if generate_silent_audio(duration, output_path):
                print(f"   ✅ 已保存: {output_path}")
                print(f"   ✅ 已保存 TTS 文本: {text_output_path}")

if __name__ == '__main__':
    main()
