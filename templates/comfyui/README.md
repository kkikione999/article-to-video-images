# ComfyUI Template Notes

This folder contains the default API-format workflow template used by the
`article-to-video-images` skill when `ARTICLE_TO_VIDEO_IMAGE_PROVIDER=comfyui`.

Files:

- `controlnet_ipadapter_api.example.json`
  - Example API workflow template.
  - It is designed for a common `Checkpoint + ControlNet + IPAdapter + SaveImage`
    graph.
  - If your installed nodes use different class names or inputs, export your own
    API workflow from ComfyUI and point `COMFYUI_WORKFLOW_TEMPLATE` at that file.

Supported placeholders:

- `__POSITIVE_PROMPT__`
- `__NEGATIVE_PROMPT__`
- `__CONTROL_IMAGE__`
- `__IPADAPTER_IMAGE__`
- `__OUTPUT_PREFIX__`
- `__WIDTH__`
- `__HEIGHT__`
- `__SEED__`
- `__STEPS__`
- `__CFG__`
- `__DENOISE__`
- `__CONTROL_STRENGTH__`
- `__IPADAPTER_WEIGHT__`
- `__SAMPLER_NAME__`
- `__SCHEDULER__`
- `__CHECKPOINT_NAME__`
- `__CONTROLNET_NAME__`
- `__IPADAPTER_MODEL__`
- `__CLIP_VISION_MODEL__`

Required environment variables for the bundled example:

- `COMFYUI_CHECKPOINT_NAME`
- `COMFYUI_CONTROLNET_NAME`
- `COMFYUI_IPADAPTER_MODEL`
- `COMFYUI_CLIP_VISION_MODEL`

Optional environment variables:

- `COMFYUI_BASE_URL`
- `COMFYUI_WORKFLOW_TEMPLATE`
- `COMFYUI_STYLE_IMAGE`
- `COMFYUI_TIMEOUT_SECONDS`
- `COMFYUI_WIDTH`
- `COMFYUI_HEIGHT`
- `COMFYUI_STEPS`
- `COMFYUI_CFG`
- `COMFYUI_DENOISE`
- `COMFYUI_CONTROL_STRENGTH`
- `COMFYUI_IPADAPTER_WEIGHT`
- `COMFYUI_SAMPLER_NAME`
- `COMFYUI_SCHEDULER`
