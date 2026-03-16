#!/usr/bin/env python3
"""
ASR语音识别脚本 - 使用 Fun-ASR
生成带精确时间戳的识别结果，用于智能分镜生成
时间戳为API返回的真实值（毫秒级精度）
"""

import os
import json
import argparse
from typing import List, Dict, Optional
from urllib import request
from http import HTTPStatus


def transcribe_audio(audio_path: str, output_path: str) -> Optional[Dict]:
    """
    使用 Fun-ASR 识别音频，获取真实时间戳
    """
    try:
        import dashscope
        from dashscope.audio.asr import Recognition
        import subprocess

        api_key = os.environ.get('DASHSCOPE_API_KEY')
        if not api_key:
            print("   ❌ 未设置 DASHSCOPE_API_KEY 环境变量")
            return None

        dashscope.api_key = api_key

        # 获取音频信息
        probe = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries', 'format=duration,sample_rate',
             '-of', 'default=noprint_wrappers=1:nokey=1', audio_path],
            capture_output=True, text=True
        )
        lines = probe.stdout.strip().split('\n')
        duration = float(lines[0]) if len(lines) > 0 else 0
        sample_rate = int(lines[1]) if len(lines) > 1 else 16000

        print(f"   ⏱️  音频时长: {duration:.2f}s, 采样率: {sample_rate}Hz")

        # 转换为16kHz单声道WAV
        wav_path = audio_path.replace('.mp3', '_16k.wav')
        if not os.path.exists(wav_path):
            print(f"   🔄 转换音频格式...")
            subprocess.run([
                'ffmpeg', '-y', '-i', audio_path,
                '-ar', '16000', '-ac', '1', '-acodec', 'pcm_s16le',
                wav_path
            ], capture_output=True, check=True)

        # 使用非流式识别
        recognition = Recognition(
            model='fun-asr-realtime',
            format='wav',
            sample_rate=16000,
            callback=None
        )

        print(f"   ⏳ 开始识别...")
        result = recognition.call(wav_path)

        # 清理临时文件
        if os.path.exists(wav_path) and wav_path != audio_path:
            os.remove(wav_path)

        return parse_realtime_result(result, audio_path, output_path, duration)

    except ImportError:
        print("   ❌ 未安装 dashscope，请运行: pip install dashscope")
        return None
    except Exception as e:
        print(f"   ❌ 识别失败: {e}")
        import traceback
        traceback.print_exc()
        return None


def split_into_phrases(words_data: List[Dict], full_text: str) -> List[Dict]:
    """将字级时间戳组合成短句/短语"""
    phrases = []
    current_phrase_words = []
    current_char_count = 0
    target_chars = 8

    for i, word in enumerate(words_data):
        word_text = word.get('text', '')
        current_phrase_words.append(word)
        current_char_count += len(word_text)

        should_split = False

        if current_char_count >= target_chars:
            should_split = True

        punctuation = word.get('punctuation', '')
        if punctuation in ['，', '。', '！', '？', '；']:
            should_split = True

        if i == len(words_data) - 1:
            should_split = True

        if should_split and current_phrase_words:
            phrase_text = ''
            for j, w in enumerate(current_phrase_words):
                phrase_text += w.get('text', '')
                if j == len(current_phrase_words) - 1:
                    punc = w.get('punctuation', '')
                    if punc:
                        phrase_text += punc
                elif w.get('punctuation', '') in ['，']:
                    phrase_text += w.get('punctuation', '')

            start_time = current_phrase_words[0].get('begin_time', 0) / 1000
            end_time = current_phrase_words[-1].get('end_time', 0) / 1000

            phrases.append({
                "text": phrase_text.strip(),
                "start_time": round(start_time, 2),
                "end_time": round(end_time, 2),
            })

            current_phrase_words = []
            current_char_count = 0

    return phrases


def merge_short_segments(segments: List[Dict], min_duration: float = 5.0, max_duration: float = 20.0) -> List[Dict]:
    """合并相邻的短句"""
    if not segments:
        return []

    merged = []
    current = segments[0].copy()

    for i in range(1, len(segments)):
        next_seg = segments[i]
        current_duration = current['end_time'] - current['start_time']
        next_duration = next_seg['end_time'] - next_seg['start_time']

        if current_duration < min_duration and (current_duration + next_duration) < max_duration:
            current['text'] += next_seg['text']
            current['end_time'] = next_seg['end_time']
        else:
            merged.append(current)
            current = next_seg.copy()

    if current:
        if merged:
            current_duration = current['end_time'] - current['start_time']
            last_duration = merged[-1]['end_time'] - merged[-1]['start_time']
            if current_duration < min_duration and (last_duration + current_duration) < max_duration:
                merged[-1]['text'] += current['text']
                merged[-1]['end_time'] = current['end_time']
            else:
                merged.append(current)
        else:
            merged.append(current)

    return merged


def parse_realtime_result(
    recognition_result,
    audio_path: str,
    output_path: str,
    total_duration: float
) -> Dict:
    """解析实时语音识别结果"""
    try:
        segments = []
        full_text_parts = []

        sentence_list = recognition_result.output.get('sentence', [])

        for sentence in sentence_list:
            text = sentence.get('text', '').strip()
            if not text:
                continue

            full_text_parts.append(text)

            words_data = sentence.get('words', [])
            if not words_data:
                segment = {
                    "text": text,
                    "start_time": round(sentence.get('begin_time', 0) / 1000, 2),
                    "end_time": round(sentence.get('end_time', 0) / 1000, 2),
                }
                segments.append(segment)
                continue

            phrase_segments = split_into_phrases(words_data, text)
            segments.extend(phrase_segments)

        merged_segments = merge_short_segments(segments, min_duration=5.0, max_duration=20.0)

        output = {
            "audio_file": os.path.basename(audio_path),
            "duration": round(total_duration, 2),
            "full_text": ''.join(full_text_parts),
            "segments": merged_segments,
            "note": "时间戳由 Fun-ASR 实时识别提供（毫秒级精度）",
            "model": "fun-asr-realtime"
        }

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(output, f, ensure_ascii=False, indent=2)

        return output

    except Exception as e:
        print(f"   ❌ 解析结果失败: {e}")
        return None


def main():
    parser = argparse.ArgumentParser(
        description='ASR语音识别（带精确时间戳）- 使用 Fun-ASR'
    )
    parser.add_argument('audio', help='音频文件或目录')
    parser.add_argument(
        '-o', '--output',
        default='03-asr/',
        help='ASR结果输出目录（默认：03-asr/）'
    )
    args = parser.parse_args()

    api_key = os.environ.get('DASHSCOPE_API_KEY')
    if not api_key:
        print("❌ 未设置 DASHSCOPE_API_KEY 环境变量")
        print("   请设置: export DASHSCOPE_API_KEY='your-api-key'")
        return

    os.makedirs(args.output, exist_ok=True)

    if os.path.isfile(args.audio):
        audio_files = [args.audio]
    else:
        audio_files = [
            os.path.join(args.audio, f)
            for f in os.listdir(args.audio)
            if f.endswith('.mp3')
        ]

    if not audio_files:
        print("⚠️ 未找到音频文件")
        return

    print(f"🎙️ 找到 {len(audio_files)} 个音频文件\n")

    for audio_path in sorted(audio_files):
        abs_path = os.path.abspath(audio_path)
        base_name = os.path.basename(audio_path)
        video_num = base_name.replace('video-', '').replace('.mp3', '')
        output_name = f"asr-result-{video_num}.json"
        output_path = os.path.join(args.output, output_name)

        print(f"📝 识别: {base_name}")

        if os.path.exists(output_path):
            print(f"   ⏭️ 已存在，跳过: {output_name}")
            with open(output_path, 'r', encoding='utf-8') as f:
                result = json.load(f)
            print(f"   📊 时长: {result['duration']:.2f}s, "
                  f"{len(result['segments'])} 个片段")
            continue

        result = transcribe_audio(abs_path, output_path)

        if result:
            print(f"   ✅ 时长: {result['duration']:.2f}s")
            print(f"   ✅ 片段: {len(result['segments'])} 个")
            print(f"   ✅ 已保存: {output_name}")
        else:
            print(f"   ❌ 识别失败")

        print()

    print("🎉 ASR识别完成！")


if __name__ == '__main__':
    main()
