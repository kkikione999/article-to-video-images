#!/usr/bin/env python3
"""Calibrate subtitle text against the source narration while keeping ASR timings."""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from generate_voice_auto import extract_text_from_article

PUNCT_TO_DROP = set(" \t\r\n，。！？；：、“”‘’\"'`()（）[]【】<>《》,.;:!?/\\|-_+*=~")
STRONG_BREAKS = set("\n。！？；.!?;")
WEAK_BREAKS = set("，、：,:")


def normalize_char(ch: str) -> str:
    value = unicodedata.normalize("NFKC", ch)
    if not value or value in PUNCT_TO_DROP or value.isspace():
        return ""
    return value.lower()


def normalize_with_mapping(text: str) -> Tuple[str, List[int]]:
    chars: List[str] = []
    positions: List[int] = []
    for idx, ch in enumerate(text):
        norm = normalize_char(ch)
        if not norm:
            continue
        chars.append(norm)
        positions.append(idx)
    return "".join(chars), positions


def load_source_text(source_text_path: Optional[str], article_path: Optional[str]) -> str:
    if source_text_path:
        return Path(source_text_path).read_text(encoding="utf-8")
    if article_path:
        return extract_text_from_article(article_path)
    raise ValueError("必须提供 --source-text 或 --article")


def load_asr_payload(asr_path: str) -> Dict:
    return json.loads(Path(asr_path).read_text(encoding="utf-8"))


def build_asr_timeline(segments: List[Dict]) -> Tuple[str, List[float]]:
    chars: List[str] = []
    times: List[float] = []
    for segment in segments:
        normalized, _ = normalize_with_mapping(segment["text"])
        if not normalized:
            continue
        duration = max(segment["end_time"] - segment["start_time"], 0.01)
        count = len(normalized)
        for idx, ch in enumerate(normalized):
            chars.append(ch)
            times.append(segment["start_time"] + duration * ((idx + 0.5) / count))
    return "".join(chars), times


def build_source_chunks(source_text: str) -> List[Dict]:
    spans: List[Tuple[int, int]] = []
    start = 0
    for idx, ch in enumerate(source_text):
        if ch in STRONG_BREAKS:
            spans.append((start, idx + 1))
            start = idx + 1
    if start < len(source_text):
        spans.append((start, len(source_text)))

    chunks: List[Dict] = []
    for span_start, span_end in spans:
        text = source_text[span_start:span_end].strip()
        if not text:
            continue
        pieces = split_span(source_text, span_start, span_end)
        chunks.extend(pieces)
    return chunks


def split_span(source_text: str, start: int, end: int, max_norm_len: int = 28) -> List[Dict]:
    text = source_text[start:end].strip()
    if not text:
        return []
    norm, _ = normalize_with_mapping(text)
    if len(norm) <= max_norm_len:
        return [{"text": text, "start_orig": start, "end_orig": end}]

    pieces: List[Dict] = []
    cursor = start
    weak_positions = []
    for idx in range(start, end):
        if source_text[idx] in WEAK_BREAKS:
            weak_positions.append(idx + 1)

    if weak_positions:
        prev = start
        for pos in weak_positions + [end]:
            piece_text = source_text[prev:pos].strip()
            if piece_text:
                sub_norm, _ = normalize_with_mapping(piece_text)
                if len(sub_norm) > max_norm_len:
                    pieces.extend(hard_split(source_text, prev, pos, max_norm_len))
                else:
                    pieces.append({"text": piece_text, "start_orig": prev, "end_orig": pos})
            prev = pos
        return pieces

    return hard_split(source_text, start, end, max_norm_len)


def hard_split(source_text: str, start: int, end: int, max_norm_len: int) -> List[Dict]:
    pieces: List[Dict] = []
    current_start = start
    current_norm_len = 0
    idx = start
    while idx < end:
        norm = normalize_char(source_text[idx])
        if norm:
            current_norm_len += 1
        if current_norm_len >= max_norm_len:
            pieces.append(
                {
                    "text": source_text[current_start : idx + 1].strip(),
                    "start_orig": current_start,
                    "end_orig": idx + 1,
                }
            )
            current_start = idx + 1
            current_norm_len = 0
        idx += 1
    if current_start < end:
        pieces.append(
            {
                "text": source_text[current_start:end].strip(),
                "start_orig": current_start,
                "end_orig": end,
            }
        )
    return [piece for piece in pieces if piece["text"]]


def build_source_to_asr_mapping(source_norm: str, asr_norm: str) -> List[Optional[float]]:
    matcher = SequenceMatcher(None, source_norm, asr_norm, autojunk=False)
    blocks = [block for block in matcher.get_matching_blocks() if block.size > 0]
    mapping: List[Optional[float]] = [None] * len(source_norm)

    for block in blocks:
        for offset in range(block.size):
            mapping[block.a + offset] = float(block.b + offset)

    for idx in range(len(mapping)):
        if mapping[idx] is not None:
            continue
        prev_idx = next((j for j in range(idx - 1, -1, -1) if mapping[j] is not None), None)
        next_idx = next((j for j in range(idx + 1, len(mapping)) if mapping[j] is not None), None)
        if prev_idx is not None and next_idx is not None and next_idx != prev_idx:
            ratio = (idx - prev_idx) / (next_idx - prev_idx)
            mapping[idx] = mapping[prev_idx] + (mapping[next_idx] - mapping[prev_idx]) * ratio
        elif prev_idx is not None:
            mapping[idx] = mapping[prev_idx]
        elif next_idx is not None:
            mapping[idx] = mapping[next_idx]
        else:
            mapping[idx] = 0.0
    return mapping


def normalized_indices_for_span(positions: List[int], start_orig: int, end_orig: int) -> List[int]:
    return [idx for idx, pos in enumerate(positions) if start_orig <= pos < end_orig]


def clamp_index(value: float, max_index: int) -> int:
    return max(0, min(max_index, int(round(value))))


def build_calibrated_segments(source_text: str, asr_payload: Dict) -> List[Dict]:
    source_norm, source_positions = normalize_with_mapping(source_text)
    asr_norm, asr_times = build_asr_timeline(asr_payload["segments"])
    if not source_norm or not asr_norm or not asr_times:
        raise ValueError("无法建立字幕校准映射，source/asr 为空")

    mapping = build_source_to_asr_mapping(source_norm, asr_norm)
    chunks = build_source_chunks(source_text)
    calibrated: List[Dict] = []
    prev_end = 0.0

    for chunk in chunks:
        indices = normalized_indices_for_span(source_positions, chunk["start_orig"], chunk["end_orig"])
        if not indices:
            continue
        start_map = mapping[indices[0]]
        end_map = mapping[indices[-1]]
        start_idx = clamp_index(start_map or 0.0, len(asr_times) - 1)
        end_idx = clamp_index(end_map or start_idx, len(asr_times) - 1)
        if end_idx < start_idx:
            end_idx = start_idx

        start_time = max(prev_end, round(asr_times[start_idx], 2))
        end_time = round(asr_times[end_idx], 2)
        if end_time <= start_time:
            end_time = round(start_time + 0.8, 2)

        chunk_norm, _ = normalize_with_mapping(chunk["text"])
        asr_slice = asr_norm[start_idx : end_idx + 1]
        confidence = round(SequenceMatcher(None, chunk_norm, asr_slice, autojunk=False).ratio(), 3)

        calibrated.append(
            {
                "text": chunk["text"],
                "start_time": start_time,
                "end_time": end_time,
                "confidence": confidence,
                "source_span": [chunk["start_orig"], chunk["end_orig"]],
                "matched_asr_range": [start_idx, end_idx],
            }
        )
        prev_end = end_time

    if calibrated:
        calibrated[-1]["end_time"] = min(
            round(float(asr_payload.get("duration", calibrated[-1]["end_time"])), 2),
            calibrated[-1]["end_time"],
        ) or calibrated[-1]["end_time"]
    return merge_short_segments(calibrated)


def merge_short_segments(segments: List[Dict], min_duration: float = 1.0, min_chars: int = 6) -> List[Dict]:
    if not segments:
        return []
    merged: List[Dict] = []
    idx = 0
    while idx < len(segments):
        current = dict(segments[idx])
        duration = current["end_time"] - current["start_time"]
        text_len = len(current["text"].replace(" ", ""))
        if idx < len(segments) - 1 and (duration < min_duration or text_len < min_chars):
            nxt = dict(segments[idx + 1])
            current["text"] = f"{current['text']}{nxt['text']}"
            current["end_time"] = nxt["end_time"]
            current["confidence"] = round(min(current["confidence"], nxt["confidence"]), 3)
            current["source_span"] = [current["source_span"][0], nxt["source_span"][1]]
            current["matched_asr_range"] = [current["matched_asr_range"][0], nxt["matched_asr_range"][1]]
            merged.append(current)
            idx += 2
            continue
        merged.append(current)
        idx += 1

    # Re-run once to catch chains of short segments.
    if len(merged) != len(segments):
        return merge_short_segments(merged, min_duration=min_duration, min_chars=min_chars)
    return merged


def calibrate_subtitles(
    asr_path: str,
    output_path: str,
    source_text_path: Optional[str] = None,
    article_path: Optional[str] = None,
) -> Dict:
    asr_payload = load_asr_payload(asr_path)
    source_text = load_source_text(source_text_path, article_path)
    calibrated_segments = build_calibrated_segments(source_text, asr_payload)

    payload = {
        "audio_file": asr_payload.get("audio_file"),
        "duration": asr_payload.get("duration"),
        "subtitle_source": "tts_source" if source_text_path else "article_extracted_text",
        "source_text": source_text,
        "asr_text": asr_payload.get("full_text", ""),
        "subtitle_segments": calibrated_segments,
    }
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="对字幕做内容校准，时间来自 ASR，文本来自源稿")
    parser.add_argument("asr", help="ASR JSON 路径")
    parser.add_argument("-o", "--output", required=True, help="输出校准 JSON")
    parser.add_argument("--source-text", help="TTS 源文本文件")
    parser.add_argument("--article", help="分析文章路径，未提供 source-text 时使用")
    args = parser.parse_args()

    payload = calibrate_subtitles(
        asr_path=args.asr,
        output_path=args.output,
        source_text_path=args.source_text,
        article_path=args.article,
    )
    print(f"✅ 已生成校准字幕 JSON: {args.output}")
    print(f"✅ 字幕段数: {len(payload['subtitle_segments'])}")


if __name__ == "__main__":
    main()
