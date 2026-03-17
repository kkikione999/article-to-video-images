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
import tempfile
import time
import urllib.request

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

def _download_audio_file(audio_url, output_path, retries=3):
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(audio_url) as response, open(output_path, 'wb') as f:
                f.write(response.read())
            return
        except Exception as e:
            last_error = e
            if attempt < retries:
                time.sleep(attempt)
                continue
    raise RuntimeError(f"下载音频失败: {last_error}")

def _convert_audio_to_mp3(source_path, output_path):
    if source_path == output_path:
        return
    cmd = [
        'ffmpeg', '-y', '-i', source_path,
        '-vn', '-acodec', 'libmp3lame', '-q:a', '2',
        output_path,
    ]
    subprocess.run(cmd, capture_output=True, check=True)

def _concat_mp3_files(segment_paths, output_path):
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as list_file:
        list_path = list_file.name
        for segment_path in segment_paths:
            list_file.write(f"file '{segment_path}'\n")
    try:
        cmd = [
            'ffmpeg', '-y', '-f', 'concat', '-safe', '0',
            '-i', list_path, '-c', 'copy', output_path,
        ]
        subprocess.run(cmd, capture_output=True, check=True)
    finally:
        if os.path.exists(list_path):
            os.remove(list_path)

def _split_qwen_text(text, max_chars=600):
    chunks = []
    current = ""
    paragraphs = [part.strip() for part in text.splitlines() if part.strip()]

    def flush():
        nonlocal current
        if current.strip():
            chunks.append(current.strip())
        current = ""

    def append_piece(piece):
        nonlocal current
        piece = piece.strip()
        if not piece:
            return
        if len(piece) > max_chars:
            flush()
            start = 0
            while start < len(piece):
                chunks.append(piece[start:start + max_chars].strip())
                start += max_chars
            return
        if current and len(current) + len(piece) > max_chars:
            flush()
        current = f"{current}{piece}"

    for paragraph in paragraphs:
        sentences = re.split(r'(?<=[。！？!?；;])', paragraph)
        for sentence in sentences:
            append_piece(sentence)
        flush()

    flush()
    return chunks or [text[:max_chars]]

def _generate_qwen_audio(text, output_path, voice_id, model):
    import dashscope

    chunks = _split_qwen_text(text)
    segment_paths = []

    try:
        for index, chunk in enumerate(chunks, start=1):
            response = dashscope.MultiModalConversation.call(
                model=model,
                text=chunk,
                voice=voice_id,
            )
            if getattr(response, "status_code", None) != 200:
                raise RuntimeError(f"Qwen TTS 调用失败: {response}")

            audio_url = response.output.audio["url"]
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp_wav:
                wav_path = tmp_wav.name
            with tempfile.NamedTemporaryFile(suffix=f'.segment-{index:03d}.mp3', delete=False) as tmp_mp3:
                mp3_path = tmp_mp3.name
            try:
                _download_audio_file(audio_url, wav_path)
                _convert_audio_to_mp3(wav_path, mp3_path)
            finally:
                if os.path.exists(wav_path):
                    os.remove(wav_path)
            segment_paths.append(mp3_path)

        if len(segment_paths) == 1:
            os.replace(segment_paths[0], output_path)
        else:
            print(f"   ℹ️ Qwen TTS 自动拆分为 {len(segment_paths)} 段进行合成")
            _concat_mp3_files(segment_paths, output_path)
    finally:
        for segment_path in segment_paths:
            if os.path.exists(segment_path):
                os.remove(segment_path)

# 默认优先尝试项目内配置的克隆音色，如不可用则回退到公开音色。
DEFAULT_VENDOR = "cosyvoice"
DEFAULT_VOICE = "cosyvoice-v3-flash-bailian-d443fc1fefd34d83933974a218fb17d5"
DEFAULT_MODEL = "cosyvoice-v3-flash"
ENV_TTS_VENDOR = "DASHSCOPE_TTS_VENDOR"
ENV_TTS_MODEL = "DASHSCOPE_TTS_MODEL"
ENV_TTS_VOICE_ID = "DASHSCOPE_TTS_VOICE_ID"
FALLBACK_VOICE_OPTIONS = [
    ("cosyvoice-v1", "longxiaochun"),
]

def resolve_tts_config(cli_vendor=None, cli_model=None, cli_voice=None):
    """按 命令行 > 环境变量 > 内置默认值 解析 TTS 配置。"""
    vendor = (cli_vendor or os.environ.get(ENV_TTS_VENDOR) or DEFAULT_VENDOR).strip()
    model = (cli_model or os.environ.get(ENV_TTS_MODEL) or DEFAULT_MODEL).strip()
    voice_id = (cli_voice or os.environ.get(ENV_TTS_VOICE_ID) or DEFAULT_VOICE).strip()
    return {
        "vendor": vendor,
        "model": model,
        "voice_id": voice_id,
    }

def _candidate_voice_pairs(vendor, model, voice_id):
    pairs = [(vendor, model, voice_id)]
    if vendor.lower() == "qwen":
        return pairs
    for fallback_model, fallback_voice in FALLBACK_VOICE_OPTIONS:
        candidate = (DEFAULT_VENDOR, fallback_model, fallback_voice)
        if candidate not in pairs:
            pairs.append(candidate)
    return pairs

def generate_cosyvoice_audio(text, output_path, voice_id=DEFAULT_VOICE, model=DEFAULT_MODEL, vendor=DEFAULT_VENDOR):
    """使用阿里云 DashScope 生成语音，并在必要时自动回退到公开音色"""
    try:
        import dashscope
        from dashscope.audio.tts_v2 import SpeechSynthesizer

        dashscope.api_key = os.environ.get('DASHSCOPE_API_KEY')

        if vendor.lower() == "qwen" or model.startswith("qwen"):
            _generate_qwen_audio(text, output_path, voice_id=voice_id, model=model)
            return {
                "success": True,
                "vendor": vendor,
                "model": model,
                "voice": voice_id,
            }

        last_error = None
        for current_vendor, current_model, current_voice in _candidate_voice_pairs(vendor, model, voice_id):
            try:
                synthesizer = SpeechSynthesizer(model=current_model, voice=current_voice)
                audio = synthesizer.call(text)
                if audio:
                    with open(output_path, 'wb') as f:
                        f.write(audio)
                    return {
                        "success": True,
                        "vendor": current_vendor,
                        "model": current_model,
                        "voice": current_voice,
                    }
                last_error = f"模型 {current_model} / 音色 {current_voice} 未返回音频"
            except Exception as e:
                last_error = str(e)
                print(
                    f"   ⚠️ 语音组合不可用，准备回退: "
                    f"vendor={current_vendor}, model={current_model}, voice={current_voice}, error={e}"
                )
                continue

        print(f"❌ 语音合成失败: {last_error}")
        return {
            "success": False,
            "vendor": None,
            "model": None,
            "voice": None,
            "error": last_error,
        }
    except ImportError:
        print("❌ 未安装 dashscope，请运行: pip install dashscope")
        return {
            "success": False,
            "vendor": None,
            "model": None,
            "voice": None,
            "error": "dashscope_not_installed",
        }
    except Exception as e:
        print(f"❌ 语音合成出错: {e}")
        return {
            "success": False,
            "vendor": None,
            "model": None,
            "voice": None,
            "error": str(e),
        }

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
    parser.add_argument('--vendor', default=None, help='语音厂商，例如 qwen / cosyvoice')
    parser.add_argument('-m', '--model', default=None, help='语音模型 ID')
    parser.add_argument('-v', '--voice', default=None, help='音色ID')
    args = parser.parse_args()

    os.makedirs(args.output, exist_ok=True)
    resolved_tts = resolve_tts_config(args.vendor, args.model, args.voice)

    # 检查 API Key
    api_key = check_api_key()
    if api_key:
        print("✅ 检测到 DASHSCOPE_API_KEY，将使用阿里云 DashScope TTS 生成真实语音")
        print(
            f"   当前 TTS 配置: vendor={resolved_tts['vendor']}, "
            f"model={resolved_tts['model']}, voice={resolved_tts['voice_id']}"
        )
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
        meta_payload = {
            "article_file": os.path.abspath(article_path),
            "audio_file": output_name,
            "text_file": os.path.basename(text_output_path),
            "char_count": len(text),
            "tts_requested_vendor": resolved_tts["vendor"],
            "tts_requested_model": resolved_tts["model"],
            "tts_requested_voice_id": resolved_tts["voice_id"],
            "tts_vendor": resolved_tts["vendor"],
            "tts_model": resolved_tts["model"],
            "tts_voice_id": resolved_tts["voice_id"],
        }

        if api_key:
            # 生成真实语音
            print(f"   生成真实语音...")
            result = generate_cosyvoice_audio(
                text,
                output_path,
                voice_id=resolved_tts["voice_id"],
                model=resolved_tts["model"],
                vendor=resolved_tts["vendor"],
            )
            if result.get("success"):
                meta_payload.update(
                    {
                        "tts_mode": "dashscope",
                        "tts_vendor": result["vendor"],
                        "tts_model": result["model"],
                        "tts_voice_id": result["voice"],
                    }
                )
                print(f"   ✅ 已保存: {output_path}")
                print(f"   ✅ 已保存 TTS 文本: {text_output_path}")
                print(
                    f"   ✅ 使用厂商/模型/音色: "
                    f"{result['vendor']} / {result['model']} / {result['voice']}"
                )
            else:
                meta_payload["tts_mode"] = "dashscope_failed"
                meta_payload["tts_error"] = result.get("error")
        else:
            # 生成静音占位
            duration = estimate_duration(text)
            print(f"   生成静音占位音频（约 {duration} 秒）...")
            if generate_silent_audio(duration, output_path):
                meta_payload["tts_mode"] = "silent_placeholder"
                print(f"   ✅ 已保存: {output_path}")
                print(f"   ✅ 已保存 TTS 文本: {text_output_path}")
            else:
                meta_payload["tts_mode"] = "silent_placeholder_failed"

        with open(meta_output_path, 'w', encoding='utf-8') as f:
            json.dump(meta_payload, f, ensure_ascii=False, indent=2)

if __name__ == '__main__':
    main()
