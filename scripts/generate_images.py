#!/usr/bin/env python3
"""Generate mixed-shot knowledge-video images via DashScope or ComfyUI."""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import dashscope
import requests
from dashscope import MultiModalConversation

from _single_video_utils import (
    build_slide_spec,
    expected_output_filenames,
    parse_storyboard,
    safe_json_dump,
)
from comfyui_workflow import (
    execute_comfyui_workflow,
    prepare_comfyui_workflow,
    resolve_comfyui_options,
    validate_comfyui_setup,
)

SIZE_PLAN = ["1792*1008", "1664*928", "1280*720"]
INTER_SHOT_DELAY_SECONDS = 2.0
ENV_IMAGE_PROVIDER = "ARTICLE_TO_VIDEO_IMAGE_PROVIDER"
DEFAULT_IMAGE_PROVIDER = "dashscope"


def _obj_get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _serialize_response(response: Any) -> Dict[str, Any]:
    output = _obj_get(response, "output", {})
    serialized_results = []
    if _obj_get(output, "results", None):
        results = _obj_get(output, "results", []) or []
        for item in results:
            serialized_results.append(
                {
                    "url": _obj_get(item, "url"),
                    "orig_prompt": _obj_get(item, "orig_prompt"),
                    "actual_prompt": _obj_get(item, "actual_prompt"),
                }
            )
    else:
        choices = _obj_get(output, "choices", []) or []
        for choice in choices:
            message = _obj_get(choice, "message", {})
            content = _obj_get(message, "content", []) or []
            for item in content:
                if _obj_get(item, "image"):
                    serialized_results.append({"url": _obj_get(item, "image")})
    return {
        "status_code": _obj_get(response, "status_code"),
        "code": _obj_get(response, "code"),
        "message": _obj_get(response, "message"),
        "request_id": _obj_get(response, "request_id"),
        "output": {"results": serialized_results},
        "usage": _obj_get(response, "usage"),
    }


def parse_shot_numbers(raw: Optional[str]) -> Optional[List[int]]:
    if not raw:
        return None
    values = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        values.append(int(item))
    return values or None


def ensure_api_key() -> str:
    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError("未设置 DASHSCOPE_API_KEY 环境变量")
    dashscope.api_key = api_key
    return api_key


def resolve_image_provider(cli_provider: Optional[str] = None) -> str:
    provider = (cli_provider or os.environ.get(ENV_IMAGE_PROVIDER) or DEFAULT_IMAGE_PROVIDER).strip().lower()
    if provider not in {"dashscope", "comfyui"}:
        raise RuntimeError(f"不支持的图片 provider: {provider}")
    return provider


def _format_list(items: List[str], bullet: str = "- ") -> List[str]:
    return [f"{bullet}{item}" for item in items if item]


def _shot_type_label(shot_type: str) -> str:
    mapping = {
        "ppt_slide": "演示页镜头",
        "infographic": "信息图镜头",
        "concept_scene": "概念场景镜头",
        "comparison_frame": "对比镜头",
        "process_frame": "流程镜头",
        "quote_frame": "金句收束镜头",
    }
    return mapping.get(shot_type, "知识讲解镜头")


def _cognitive_action_label(action: str) -> str:
    mapping = {
        "hook_question": "提问引入",
        "define_claim": "定义判断",
        "explain_mechanism": "机制解释",
        "compare_contrast": "对比判断",
        "show_process": "流程推进",
        "show_evidence": "案例举证",
        "summarize_close": "总结收束",
    }
    return mapping.get(action, action)


def _page_archetype_label(page_archetype: str) -> str:
    mapping = {
        "thesis_page": "核心命题页",
        "structure_page": "结构拆解页",
        "comparison_page": "关系对照页",
        "process_page": "流程推进页",
        "evidence_page": "案例证据页",
        "summary_page": "总结收束页",
    }
    return mapping.get(page_archetype, page_archetype)


def _shot_flavor_label(shot_flavor: str) -> str:
    mapping = {
        "steady_explainer": "稳态讲解",
        "contrast_tension": "冲突对照",
        "zoom_focus": "压迫聚焦",
        "layered_depth": "层次推进",
        "quiet_resolve": "留白收束",
    }
    return mapping.get(shot_flavor, shot_flavor)


def _shot_type_instructions(slide_spec: Dict[str, Any]) -> List[str]:
    shot_type = slide_spec["shot_type"]
    layout_map = {
        "CenterLayout": "居中封面布局",
        "SplitLayout": "左右分栏布局",
        "StackLayout": "上下分层布局",
        "GridLayout": "信息网格布局",
        "TripleLayout": "三列信息布局",
        "CardLayout": "卡片式摘要布局",
    }

    if shot_type == "ppt_slide":
        lines = [
            "这是知识视频中的高质量中文演示页镜头，要有清晰版式、图形主体和适量文字，不是纯文字白板。",
            f"版式优先参考：{layout_map.get(slide_spec['layout_family'], '卡片式摘要布局')}。",
        ]
        if slide_spec["ppt_visual"].get("图示提示"):
            lines.append(f"图示方向：{slide_spec['ppt_visual']['图示提示']}")
        return lines
    if shot_type == "infographic":
        return [
            "这镜必须明显呈现结构化信息图语言，强调模块、节点、箭头、关系线、卡片层次。",
            "不要把它做成一页普通 PPT，也不要只放一个孤立主体。",
        ]
    if shot_type == "concept_scene":
        return [
            "这镜应更像概念插画或叙事场景，用主体、动作和空间隐喻表达抽象观点。",
            "不要默认做成卡片页面，不要堆满说明文字。",
        ]
    if shot_type == "comparison_frame":
        return [
            "这镜必须存在明显的左右或上下对照，两组对象差异一眼可见。",
            "要用分隔、方向、箭头或光线对比建立比较关系，而不是只有两个并排元素。",
        ]
    if shot_type == "process_frame":
        return [
            "这镜必须体现步骤、阶段或演进顺序，让观众一眼看出流程推进。",
            "使用路径、阶段块、递进箭头或层层展开的结构，不要只是静态拼贴。",
        ]
    if shot_type == "quote_frame":
        return [
            "这镜是收束型金句画面，应明显留白、焦点集中、情绪稳定。",
            "不要做成复杂信息图，不要堆叠多个主体。",
        ]
    return ["这镜需要服务知识讲解，信息和视觉都要明确。"]


def _page_archetype_instructions(slide_spec: Dict[str, Any]) -> List[str]:
    archetype = slide_spec["page_archetype"]
    density = slide_spec["knowledge_density"]

    lines = [
        f"这是一页“{_page_archetype_label(archetype)}”，知识密度等级为 {density}。",
    ]
    if archetype == "thesis_page":
        lines.append("一页只传达一个核心判断，信息集中，不要同时展开太多支线。")
    elif archetype == "structure_page":
        lines.append("需要把系统、模块或角色拆成有层级的结构页，让观众一眼看出组成关系。")
    elif archetype == "comparison_page":
        lines.append("必须是强对照知识页，至少两组对象差异明确，不允许含糊并排。")
    elif archetype == "process_page":
        lines.append("必须是推进型知识页，观众应该一眼看到步骤、顺序或演进关系。")
    elif archetype == "evidence_page":
        lines.append("这是证据页，要用案例、数据卡或事实支撑核心判断，不要空泛概念化。")
    elif archetype == "summary_page":
        lines.append("这是收束页，只保留最重要结论，允许留白，不要再引入新结构。")
    return lines


def _shot_flavor_instructions(slide_spec: Dict[str, Any]) -> List[str]:
    flavor = slide_spec["shot_flavor"]
    if flavor == "steady_explainer":
        return ["镜头风味是稳态讲解，画面重在清晰、稳定、理性推进。"]
    if flavor == "contrast_tension":
        return ["镜头风味是冲突对照，画面需要明显张力和对立，不要平均分配注意力。"]
    if flavor == "zoom_focus":
        return ["镜头风味是压迫聚焦，主焦点必须强，周边信息要服从中心判断。"]
    if flavor == "layered_depth":
        return ["镜头风味是层次推进，前中后景或步骤层层递进，形成推进感。"]
    if flavor == "quiet_resolve":
        return ["镜头风味是留白收束，画面克制、安静、重结论，不要复杂化。"]
    return []


def _semantic_visual_instructions(slide_spec: Dict[str, Any]) -> List[str]:
    subject_elements = slide_spec["subject_elements"]
    action_relations = slide_spec["action_relations"]
    info_layers = slide_spec["information_layers"]
    composition = slide_spec["composition"]
    style_anchor = slide_spec["style_anchor"]
    allowed_text = set(
        item
        for item in [
            slide_spec["text_policy"]["title"],
            slide_spec["text_policy"]["subtitle"],
            *slide_spec["text_policy"]["bullets"],
            *slide_spec["text_policy"]["data_cards"],
        ]
        if item
    )

    non_text_subjects = [item for item in subject_elements if item not in allowed_text]

    lines = [
        "请把抽象含义转成图形、主体、图标、结构、动作和空间关系，而不是把说明句直接写在画面上。",
        f"这页承担的认知动作是：{_cognitive_action_label(slide_spec['cognitive_action'])}。",
        f"核心含义围绕 {slide_spec['visual_target'].rstrip('。')} 展开。",
    ]
    if non_text_subjects:
        lines.append("以下概念只作为视觉语义参考，应尽量通过图标、模块、角色或结构表达，不要原样渲染成中文大字：")
        lines.extend(_format_list(non_text_subjects))
    if action_relations:
        lines.append("请通过视觉关系表达这些关系或动作，而不是把它们写成说明文字：")
        lines.extend(_format_list(action_relations))
    lines.extend(
        [
            f"构图采用 {composition['构图']}，景别为 {composition['景别']}，视角为 {composition['视角']}。",
            f"前景重点放在 {info_layers['前景']}，中景放在 {info_layers['中景']}，背景服务 {info_layers['背景']}。",
            f"整体风格使用 {style_anchor['main_style']}，当前镜头偏向 {style_anchor['current_variant']}。",
            f"色调控制为 {style_anchor['palette']}，光线为 {style_anchor['lighting']}，画面密度保持 {style_anchor['density']}。",
        ]
    )
    return lines


def _text_policy_instructions(slide_spec: Dict[str, Any]) -> List[str]:
    text_policy = slide_spec["text_policy"]
    mode = text_policy["mode"]
    title = text_policy["title"]
    subtitle = text_policy["subtitle"]
    bullets = text_policy["bullets"]
    data_cards = text_policy["data_cards"]

    lines = ["文字必须使用简体中文，不允许额外英文，不允许出现提示词原文。"]
    if mode == "none":
        lines.append("这镜尽量不要出现上屏大字；如必须出现，只允许极少量标签级中文。")
        return lines
    if mode == "title_only":
        lines.append("只允许出现以下上屏文字，不要额外扩写：")
        lines.extend(_format_list([title, subtitle]))
        return lines
    if mode == "title_plus_bullets":
        lines.append("允许出现的上屏文字只有以下这些：")
        lines.extend(_format_list([title, subtitle]))
        if bullets:
            lines.append("项目符号只允许使用这些中文短句：")
            lines.extend(_format_list(bullets))
        return lines
    if mode == "title_plus_data":
        lines.append("允许出现的上屏文字只有以下这些：")
        lines.extend(_format_list([title, subtitle]))
        if data_cards:
            lines.append("数据卡只允许使用这些中文短句：")
            lines.extend(_format_list(data_cards))
        return lines
    if mode == "quote_only":
        lines.append("画面只允许出现这一句中文短句，放在最强视觉焦点：")
        lines.extend(_format_list([title]))
        return lines
    return lines


def build_prompt(slide_spec: Dict[str, Any]) -> str:
    text_policy = slide_spec["text_policy"]

    layout_map = {
        "CenterLayout": "居中封面布局",
        "SplitLayout": "左右分栏布局",
        "StackLayout": "上下分层布局",
        "GridLayout": "信息网格布局",
        "TripleLayout": "三列信息布局",
        "CardLayout": "卡片式摘要布局",
    }
    lines = [
        "请生成一张 16:9 横版中文知识镜头页。",
        f"这是第 {slide_spec['shot_num']} 镜，镜头类型是：{_shot_type_label(slide_spec['shot_type'])}。",
        f"这页属于：{_page_archetype_label(slide_spec['page_archetype'])}，镜头风味是：{_shot_flavor_label(slide_spec['shot_flavor'])}。",
        "这是一张服务视频剪辑的单镜头知识内容页，不是电影海报，不是杂志封面，不是截图拼贴，不是默认模板页。",
        "除明确允许的上屏文字外，任何说明句、标签名、字段名、提示词原文都不得出现在画面里。",
    ]
    lines.extend(_shot_type_instructions(slide_spec))
    lines.extend(_page_archetype_instructions(slide_spec))
    lines.extend(_shot_flavor_instructions(slide_spec))
    lines.extend(_semantic_visual_instructions(slide_spec))
    if slide_spec["shot_type"] == "ppt_slide":
        lines.append(f"PPT 布局参考：{layout_map.get(slide_spec['layout_family'], '卡片式摘要布局')}")
    lines.extend(_text_policy_instructions(slide_spec))
    if slide_spec["data_layer"]:
        lines.append("辅助背景信息：")
        lines.extend([f"- {k}: {v}" for k, v in slide_spec["data_layer"].items()])
    lines.extend(
        [
            "请保证画面有明确的主焦点和层次，不要所有元素等权平铺。",
            "如果镜头需要结构感，请明显做出模块关系、对照结构或流程方向，不要靠抽象背景敷衍。",
            "如果镜头需要场景感，请通过主体、动作、空间深度和光线氛围体现，而不是只做一张带标题的平面页。",
            "中文如果出现，必须大字、清晰、高对比度、简洁，不要乱码，不要长段落。",
            "不要把本段提示词、布局名、镜头类型名、英文说明文字写进画面。",
        ]
    )
    return "\n".join(lines)


def build_negative_prompt(slide_spec: Dict[str, Any]) -> str:
    shot_type = slide_spec["shot_type"]
    shot_specific: Dict[str, str] = {
        "ppt_slide": "cinematic poster, dramatic movie still, photoreal character portrait dominating frame",
        "infographic": "single isolated object, empty background, purely decorative abstract shapes, poster slogan",
        "concept_scene": "dense bullet list, template slide page, spreadsheet look, wall of text",
        "comparison_frame": "single centered composition, no contrast split, mirrored duplicate layout",
        "process_frame": "random collage, unordered objects, no directional flow, single hero object",
        "quote_frame": "busy infographic, many small cards, dense dashboard, overcomplicated diagram",
    }
    avoid_items = ", ".join(slide_spec.get("avoid_items", []))
    leaked_labels = (
        "口播语义参考, 视觉目标, 镜头标题, 主体元素, 动作或关系, 构图与景别, 信息层级, "
        "主视觉风格, 当前镜头变体, 画面密度, 允许出现的上屏文字, 数据卡只允许使用这些中文短句, "
        "项目符号只允许使用这些中文短句"
    )
    return (
        "movie poster, cinematic poster, tiny unreadable text, dense paragraph, watermark, "
        "photo collage, handwritten font, distorted characters, garbled Chinese text, "
        "irrelevant brand logos, cluttered composition, layout label, prompt text, English copy, "
        f"{shot_specific.get(shot_type, '')}, {leaked_labels}, {avoid_items}"
    )


def attempt_dir(output_dir: Path, shot_num: int) -> Path:
    return output_dir / "attempts" / f"shot-{shot_num:02d}"


def attempt_stem(shot_num: int, attempt: int) -> str:
    return f"attempt-{attempt:02d}"


def download_image(url: str, destination: Path) -> None:
    response = requests.get(url, timeout=120)
    response.raise_for_status()
    destination.write_bytes(response.content)


def call_image_api(prompt: str, negative_prompt: str, model: str, size: str) -> Any:
    if model.startswith("qwen-image-2.0"):
        messages = [{"role": "user", "content": [{"text": prompt}]}]
        return MultiModalConversation.call(
            model=model,
            messages=messages,
            stream=False,
            n=1,
            watermark=False,
            negative_prompt=negative_prompt,
            prompt_extend=False,
            size=size,
        )
    return dashscope.ImageSynthesis.call(
        model=model,
        prompt=prompt,
        negative_prompt=negative_prompt,
        n=1,
        size=size,
        watermark=False,
        prompt_extend=False,
    )


def should_try_next_size(error_text: str) -> bool:
    text = (error_text or "").lower()
    size_signals = ["size", "resolution", "image size", "invalidparameter", "invalid parameter"]
    rate_signals = ["throttling.ratequota", "rate limit", "429"]
    if any(signal in text for signal in rate_signals):
        return False
    return any(signal in text for signal in size_signals)


def generate_attempt(
    slide_spec: Dict[str, Any],
    shot_num: int,
    attempt: int,
    output_dir: Path,
    model: str,
    provider: str,
    comfyui_base_url: Optional[str] = None,
    comfyui_workflow: Optional[str] = None,
    comfyui_style_image: Optional[str] = None,
    comfyui_timeout: Optional[int] = None,
    force: bool = False,
) -> Dict[str, Any]:
    shot_attempt_dir = attempt_dir(output_dir, shot_num)
    shot_attempt_dir.mkdir(parents=True, exist_ok=True)
    stem = attempt_stem(shot_num, attempt)
    png_path = shot_attempt_dir / f"{stem}.png"
    prompt_path = shot_attempt_dir / f"{stem}.prompt.txt"
    request_path = shot_attempt_dir / f"{stem}.request.json"
    response_path = shot_attempt_dir / f"{stem}.response.json"

    if png_path.exists() and not force:
        return {
            "shot_num": shot_num,
            "attempt": attempt,
            "status": "cached",
            "image_path": str(png_path),
            "request_path": str(request_path),
            "response_path": str(response_path),
        }

    prompt = build_prompt(slide_spec)
    negative_prompt = build_negative_prompt(slide_spec)
    prompt_path.write_text(prompt, encoding="utf-8")

    request_payload = {
        "shot_num": shot_num,
        "attempt": attempt,
        "provider": provider,
        "model": model,
        "semantic_attempt": True,
        "slide_spec": slide_spec,
        "negative_prompt": negative_prompt,
    }
    if provider == "comfyui":
        try:
            options = resolve_comfyui_options(
                base_url=comfyui_base_url,
                workflow_template=comfyui_workflow,
                style_image=comfyui_style_image,
                timeout_seconds=comfyui_timeout,
            )
            prepared = prepare_comfyui_workflow(
                slide_spec=slide_spec,
                prompt=prompt,
                negative_prompt=negative_prompt,
                shot_num=shot_num,
                attempt=attempt,
                output_dir=output_dir,
                options=options,
            )
            request_payload.update(
                {
                    "comfyui": {
                        "base_url": prepared["base_url"],
                        "workflow_template": prepared["workflow_template"],
                        "materialized_workflow_path": prepared["materialized_workflow_path"],
                        "control_image_path": prepared["control_image_path"],
                        "style_image_path": prepared["style_image_path"],
                        "output_prefix": prepared["output_prefix"],
                        "replacements": prepared["replacements"],
                    }
                }
            )
            safe_json_dump(request_path, request_payload)
            execute_comfyui_workflow(
                prepared=prepared,
                output_image_path=png_path,
                response_path=response_path,
            )
            return {
                "shot_num": shot_num,
                "attempt": attempt,
                "status": "generated",
                "image_path": str(png_path),
                "request_path": str(request_path),
                "response_path": str(response_path),
            }
        except Exception as exc:
            error_payload = {
                "provider": "comfyui",
                "code": "exception",
                "message": str(exc),
            }
            safe_json_dump(request_path, request_payload)
            safe_json_dump(response_path, error_payload)
            return {
                "shot_num": shot_num,
                "attempt": attempt,
                "status": "error",
                "error": str(exc),
                "request_path": str(request_path),
                "response_path": str(response_path),
                "image_path": str(png_path),
            }

    request_payload["size_plan"] = SIZE_PLAN
    safe_json_dump(request_path, request_payload)

    last_response: Dict[str, Any] = {}
    last_error = None
    for size in SIZE_PLAN:
        try:
            response = call_image_api(prompt, negative_prompt, model, size)
            last_response = _serialize_response(response)
            last_response["requested_size"] = size
            result = (last_response.get("output") or {}).get("results") or []
            url = result[0]["url"] if result else None
            if not url:
                last_error = f"图片接口未返回 URL，size={size}"
                continue
            try:
                download_image(url, png_path)
            except Exception:
                try:
                    download_image(url, png_path)
                except Exception as second_exc:
                    last_error = f"图片下载失败: {second_exc}"
                    last_response["download_error"] = str(second_exc)
                    continue
            safe_json_dump(response_path, last_response)
            return {
                "shot_num": shot_num,
                "attempt": attempt,
                "status": "generated",
                "image_path": str(png_path),
                "request_path": str(request_path),
                "response_path": str(response_path),
            }
        except Exception as exc:
            last_error = str(exc)
            last_response = {
                "requested_size": size,
                "code": "exception",
                "message": str(exc),
            }
            if should_try_next_size(str(exc)):
                continue
            break

    last_response["final_error"] = last_error
    safe_json_dump(response_path, last_response)
    return {
        "shot_num": shot_num,
        "attempt": attempt,
        "status": "error",
        "error": last_error,
        "request_path": str(request_path),
        "response_path": str(response_path),
        "image_path": str(png_path),
    }


def generate_images_for_storyboard(
    storyboard_path: str,
    output_dir: str,
    attempt: int,
    shot_numbers: Optional[Iterable[int]] = None,
    model: str = "qwen-image-2.0",
    provider: str = DEFAULT_IMAGE_PROVIDER,
    comfyui_base_url: Optional[str] = None,
    comfyui_workflow: Optional[str] = None,
    comfyui_style_image: Optional[str] = None,
    comfyui_timeout: Optional[int] = None,
    force: bool = False,
) -> Dict[str, Any]:
    resolved_provider = resolve_image_provider(provider)
    if resolved_provider == "dashscope":
        ensure_api_key()
    else:
        validate_comfyui_setup(
            resolve_comfyui_options(
                base_url=comfyui_base_url,
                workflow_template=comfyui_workflow,
                style_image=comfyui_style_image,
                timeout_seconds=comfyui_timeout,
            )
        )
    shots = parse_storyboard(storyboard_path)
    selected = (
        set(shot["shot_num"] for shot in shots)
        if shot_numbers is None
        else set(shot_numbers)
    )
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    results = []
    for shot in shots:
        if shot["shot_num"] not in selected:
            continue
        slide_spec = build_slide_spec(shot, attempt)
        result = generate_attempt(
            slide_spec=slide_spec,
            shot_num=shot["shot_num"],
            attempt=attempt,
            output_dir=output_path,
            model=model,
            provider=resolved_provider,
            comfyui_base_url=comfyui_base_url,
            comfyui_workflow=comfyui_workflow,
            comfyui_style_image=comfyui_style_image,
            comfyui_timeout=comfyui_timeout,
            force=force,
        )
        results.append(result)
        time.sleep(INTER_SHOT_DELAY_SECONDS)

    payload = {"attempt": attempt, "provider": resolved_provider, "model": model, "results": results}
    safe_json_dump(output_path / f"generation-attempt-{attempt:02d}.json", payload)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="生成知识视频混合镜头图片（DashScope 或 ComfyUI）")
    parser.add_argument("storyboard", help="分镜脚本路径")
    parser.add_argument("-o", "--output", required=True, help="输出目录，例如 05-images/video-1")
    parser.add_argument("--attempt", type=int, default=1, help="语义生成轮次，从 1 开始")
    parser.add_argument("--shots", help="只生成指定镜号，逗号分隔，例如 1,2,4")
    parser.add_argument("--model", default="qwen-image-2.0", help="图片模型")
    parser.add_argument(
        "--provider",
        default=os.environ.get(ENV_IMAGE_PROVIDER, DEFAULT_IMAGE_PROVIDER),
        choices=["dashscope", "comfyui"],
        help="图片生成 provider",
    )
    parser.add_argument("--comfyui-base-url", help="ComfyUI 服务地址，默认读取 COMFYUI_BASE_URL")
    parser.add_argument("--comfyui-workflow", help="ComfyUI API workflow 模板路径")
    parser.add_argument("--comfyui-style-image", help="可选：IPAdapter 参考图路径")
    parser.add_argument("--comfyui-timeout", type=int, help="ComfyUI 单镜超时时间（秒）")
    parser.add_argument("--force", action="store_true", help="覆盖已有尝试产物")
    args = parser.parse_args()

    shot_numbers = parse_shot_numbers(args.shots)
    payload = generate_images_for_storyboard(
        storyboard_path=args.storyboard,
        output_dir=args.output,
        attempt=args.attempt,
        shot_numbers=shot_numbers,
        model=args.model,
        provider=args.provider,
        comfyui_base_url=args.comfyui_base_url,
        comfyui_workflow=args.comfyui_workflow,
        comfyui_style_image=args.comfyui_style_image,
        comfyui_timeout=args.comfyui_timeout,
        force=args.force,
    )
    error_count = sum(1 for item in payload["results"] if item.get("status") == "error")
    total = len(payload["results"])
    if error_count:
        print(
            f"⚠️ attempt {args.attempt} 结束，provider={payload['provider']}，"
            f"共处理 {total} 个镜头，失败 {error_count} 个"
        )
        raise SystemExit(1)
    print(f"✅ 已完成 attempt {args.attempt}，provider={payload['provider']}，共处理 {total} 个镜头")


if __name__ == "__main__":
    main()
