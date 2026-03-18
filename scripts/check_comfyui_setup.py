#!/usr/bin/env python3
"""Check whether the local ComfyUI runtime can execute the configured workflow."""

from __future__ import annotations

import argparse
import json
import sys

from comfyui_workflow import inspect_comfyui_setup, resolve_comfyui_options, validate_comfyui_setup


def main() -> None:
    parser = argparse.ArgumentParser(description="检查 ComfyUI 工作流环境是否齐备")
    parser.add_argument("--comfyui-base-url", help="ComfyUI 服务地址")
    parser.add_argument("--comfyui-workflow", help="ComfyUI API workflow 模板路径")
    parser.add_argument("--comfyui-style-image", help="可选：IPAdapter 参考图路径")
    parser.add_argument("--comfyui-timeout", type=int, help="ComfyUI 单镜头超时时间（秒）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出完整检查结果")
    args = parser.parse_args()

    options = resolve_comfyui_options(
        base_url=args.comfyui_base_url,
        workflow_template=args.comfyui_workflow,
        style_image=args.comfyui_style_image,
        timeout_seconds=args.comfyui_timeout,
    )
    report = inspect_comfyui_setup(options)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"ComfyUI: {report['base_url']}")
        print(f"Workflow: {report['workflow_template']}")
        print(f"Class types: {', '.join(report['class_types'])}")
        print("")
        print("模型槽位:")
        for class_type, meta in report["choice_counts"].items():
            print(f"- {class_type}.{meta['field']}: {meta['count']} 个候选")
        if report["missing_nodes"]:
            print("")
            print("缺失节点:")
            for node in report["missing_nodes"]:
                print(f"- {node}")
        if report["findings"]:
            print("")
            print("问题:")
            for item in report["findings"]:
                print(f"- {item}")
        else:
            print("")
            print("环境检查通过")

    try:
        validate_comfyui_setup(options)
    except RuntimeError:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
