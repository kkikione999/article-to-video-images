#!/usr/bin/env python3
"""Generate subtitle files from ASR JSON."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Dict, List, Tuple


def format_srt_time(seconds: float) -> str:
    total_ms = max(0, int(round(seconds * 1000)))
    hours = total_ms // 3600000
    total_ms %= 3600000
    minutes = total_ms // 60000
    total_ms %= 60000
    secs = total_ms // 1000
    millis = total_ms % 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def format_ass_time(seconds: float) -> str:
    total_cs = max(0, int(round(seconds * 100)))
    hours = total_cs // 360000
    total_cs %= 360000
    minutes = total_cs // 6000
    total_cs %= 6000
    secs = total_cs // 100
    centis = total_cs % 100
    return f"{hours:d}:{minutes:02d}:{secs:02d}.{centis:02d}"


def wrap_text(text: str, line_len: int = 18, max_lines: int = 2) -> str:
    del line_len, max_lines
    return re.sub(r"\s+", " ", text).strip()


def escape_ass_text(text: str) -> str:
    return text.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}")


def load_segments(asr_path: Path) -> List[Dict]:
    payload = json.loads(asr_path.read_text(encoding="utf-8"))
    if payload.get("subtitle_segments"):
        return payload.get("subtitle_segments", [])
    return payload.get("segments", [])


def write_srt(segments: List[Dict], output_path: Path) -> None:
    blocks = []
    for idx, segment in enumerate(segments, start=1):
        text = wrap_text(segment["text"])
        blocks.append(
            "\n".join(
                [
                    str(idx),
                    f"{format_srt_time(segment['start_time'])} --> {format_srt_time(segment['end_time'])}",
                    text,
                ]
            )
        )
    output_path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")


def write_ass(segments: List[Dict], output_path: Path) -> None:
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Hiragino Sans GB,42,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,0,0,2,110,110,120,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header.rstrip()]
    for segment in segments:
        text = escape_ass_text(wrap_text(segment["text"]).replace("\n", r"\N"))
        lines.append(
            "Dialogue: 0,{start},{end},Default,,0,0,0,,{text}".format(
                start=format_ass_time(segment["start_time"]),
                end=format_ass_time(segment["end_time"]),
                text=text,
            )
        )
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def generate_subtitles(asr_path: str, output_base: str) -> Tuple[Path, Path]:
    asr_file = Path(asr_path)
    base = Path(output_base)
    if base.suffix:
        base = base.with_suffix("")
    base.parent.mkdir(parents=True, exist_ok=True)

    segments = load_segments(asr_file)
    if not segments:
        raise ValueError("ASR 文件中没有 segments，无法生成字幕")

    srt_path = base.with_suffix(".srt")
    ass_path = base.with_suffix(".ass")
    write_srt(segments, srt_path)
    write_ass(segments, ass_path)
    return srt_path, ass_path


def main() -> None:
    parser = argparse.ArgumentParser(description="从 ASR JSON 生成字幕文件")
    parser.add_argument("asr", help="ASR JSON 路径")
    parser.add_argument("-o", "--output", required=True, help="输出基础路径，例如 output/video-1")
    args = parser.parse_args()

    srt_path, ass_path = generate_subtitles(args.asr, args.output)
    print(f"✅ 已生成字幕: {srt_path}")
    print(f"✅ 已生成字幕样式文件: {ass_path}")


if __name__ == "__main__":
    main()
