---
name: article-to-video-images
description: 将文章/文件自动转换为教育/培训视频内容，采用图片驱动的视频生成方式。当用户输入文章、论文、报告或任何文本内容并希望生成图片+口播形式的科普视频时触发。支持六步流程：1）四人小队多角度分析文章并生成科普文章（直接作为口播脚本）；2）使用CosyVoice生成语音音频；3）使用Fun-ASR进行ASR语音识别获取精确时间戳；4）基于ASR+原文生成智能分镜脚本和图片生成提示词；5）批量生成图片集合；6）将图片与音频合成为最终视频。输出格式包括Markdown分析文章、音频文件、ASR识别结果、分镜脚本、图片集合和最终视频文件。
---

# Article To Video (Images)

## 概述

将任意文章/文件转换为完整的视频内容生产流程，采用**图片驱动的视频生成方式**，包含多维度分析、语音合成、ASR时间戳、智能分镜脚本、图片生成和视频合成。

**核心特点**：
- **直接使用分析文章**：01-analysis 的分析文章质量优秀，直接作为口播脚本，无需额外的脚本转换步骤
- **图片驱动**：05-storyboard 生成图片生成提示词，06-images 批量生成图片集合
- **音画合成**：07-video 将图片序列与音频合成为最终视频

## 工作流程决策树

```
用户输入文章/文件
    │
    ├─→ 第一步：四人小队分析
    │     └─→ 根据文章内容确定4个分析维度
    │     └─→ 每个成员输出一篇科普文章（直接作为口播脚本）
    │
    ├─→ 第二步：语音生成
    │     ├─→ 直接使用分析文章生成语音
    │     └─→ 使用 CosyVoice 生成语音音频
    │
    ├─→ 第三步：ASR语音识别
    │     ├─→ 使用 Fun-ASR 识别语音
    │     └─→ 输出带时间戳的识别结果到 03-asr/
    │
    ├─→ 第四步：智能分镜脚本生成
    │     ├─→ 结合 ASR时间戳 + 口播内容 + 原始文章
    │     ├─→ 按语义段落 + 视觉功能切镜
    │     ├─→ 为每个画面生成结构化镜头设计
    │     └─→ 输出到 04-storyboard/ (storyboard-*.md)
    │
    ├─→ 第五步：图片生成
    │     ├─→ 根据分镜脚本的图片提示词
    │     ├─→ 使用 AI 图像生成工具批量生成图片
    │     └─→ 输出到 05-images/ (image-*.png/jpg)
    │
    └─→ 第六步：视频合成
          ├─→ 将图片序列与音频合成
          ├─→ 根据ASR时间码精确控制画面切换
          └─→ 输出到 output/video-*.mp4
```

## 文件夹结构规范

所有输出文件存放在以 `<标题>-MM-DD` 命名的文件夹中：

```
<标题>-MM-DD/
├── 01-analysis/                    # 第一步：文章分析（同时也是口播脚本）
│   ├── team-roles.md              # 四人小队角色定义
│   ├── member-1-<topic>.md        # 成员1的分析文章
│   ├── member-2-<topic>.md
│   ├── member-3-<topic>.md
│   └── member-4-<topic>.md
│
├── 02-audio/                       # 第二步：语音合成输出
│   ├── video-1.mp3                # 视频1音频文件
│   ├── video-2.mp3
│   ├── video-3.mp3
│   └── video-4.mp3
│
├── 03-asr/                         # 第三步：ASR识别结果
│   ├── asr-result-1.json          # ASR时间戳结果
│   ├── asr-result-2.json
│   ├── asr-result-3.json
│   └── asr-result-4.json
│
├── 04-storyboard/                  # 第四步：智能分镜脚本+图片提示词
│   ├── storyboard-1-<topic>.md    # 包含图片生成提示词的分镜脚本
│   ├── storyboard-2-<topic>.md
│   ├── storyboard-3-<topic>.md
│   └── storyboard-4-<topic>.md
│
├── 05-images/                      # 第五步：生成的图片集合
│   ├── video-1/                   # 视频1的图片序列
│   │   ├── image-01.png
│   │   ├── image-02.png
│   │   └── ...
│   ├── video-2/
│   ├── video-3/
│   └── video-4/
│
└── output/                         # 第六步：最终视频输出
    ├── video-1.mp4                # 视频1最终文件
    ├── video-2.mp4
    ├── video-3.mp4
    └── video-4.mp4
```

## 第一步：四人小队分析

### 1.1 确定分析维度

根据输入文章的主题和类型，从以下维度中选择4个最合适的分析角度：

| 维度类别 | 适用场景 | 分析重点 |
|---------|---------|---------|
| 技术原理 | 科技/工程类文章 | 核心机制、技术架构、创新点 |
| 历史演进 | 新技术/新方法 | 发展脉络、里程碑事件、技术迭代 |
| 应用场景 | 实用技术/产品 | 实际案例、行业应用、落地价值 |
| 社会影响 | 重大发现/技术 | 社会变革、伦理考量、未来趋势 |
| 对比分析 | 同类技术/方法 | 优劣势比较、选型建议、适用边界 |
| 底层逻辑 | 理论/算法文章 | 数学原理、推导过程、直觉解释 |
| 实现细节 | 工程实践文章 | 代码解读、关键实现、坑点提示 |
| 行业视角 | 商业/市场分析 | 市场格局、竞争态势、商业模式 |

### 1.2 角色分配原则

ALWAYS 根据文章特点动态分配角色：

1. **学术解析者** - 适合深度技术/理论文章
   - 解释核心概念和原理
   - 使用类比和可视化语言
   - 提供公式/算法的直觉理解

2. **历史讲述者** - 适合有历史背景的内容
   - 梳理技术发展脉络
   - 讲述关键人物和事件
   - 连接过去、现在与未来

3. **实用向导** - 适合工具/方法类文章
   - 演示具体使用方法
   - 分享最佳实践
   - 解答常见疑问

4. **深度思考者** - 适合有社会意义的内容
   - 探讨深层影响
   - 提出批判性观点
   - 引发观众思考

### 1.3 输出格式规范

每篇分析文章必须包含：

```markdown
# <分析主题>

## 核心要点（3-5个bullet points）

## 详细解读

### 背景知识
### 核心内容
### 深度分析

## 总结与启发
```

**写作要求**：
- 使用通俗易懂的中文
- 避免专业术语堆砌，必要时提供解释
- 每篇文章1500-2500字
- 适合视频化表达（有画面感）
- 适合口播朗读（口语化、节奏感好）

### 1.4 作为口播脚本的特殊要求

由于分析文章直接作为口播脚本使用，需要额外注意：

1. **口语化表达**：
   - 使用"我们"、"让我们"等拉近距离的表达
   - 适当使用设问增加互动感
   - 避免长句，多用短句和停顿

2. **朗读友好**：
   - 不使用复杂的专业术语缩写（或首次使用时解释）
   - 避免容易产生歧义的发音
   - 段落长度适合朗读（约200字/分钟）

3. **画面暗示**：
   - 在描述中加入画面感的语言
   - 例如："想象一下..."、"就像这样..."

## 第二步：语音生成

### 2.1 直接使用分析文章生成语音

**重要变更**：本 skill 跳过传统的"脚本转换"步骤，直接使用 01-analysis/ 中的分析文章生成语音。

原因：
- 四人小队生成的分析文章已经是高质量的科普内容
- 文章结构清晰，语言通俗易懂
- 直接朗读分析文章，信息更完整

### 2.2 语音生成

使用 skill 自带的脚本生成语音音频：

```bash
cd <标题>-MM-DD
python3 "${CODEX_HOME:-$HOME/.codex}/skills/article-to-video-images/scripts/generate_voice_auto.py" \
  01-analysis/ \
  -o 02-audio/
```

**输出文件**：
- `02-audio/video-1.mp3`
- `02-audio/video-2.mp3`
- `02-audio/video-3.mp3`
- `02-audio/video-4.mp3`

**说明**：
- 自动检测 `DASHSCOPE_API_KEY` 环境变量
- 如存在：使用阿里云 CosyVoice 生成真实语音
- 如不存在：生成静音占位音频（按文章时长估算）
- 可选环境变量：
  - `DASHSCOPE_TTS_VENDOR`
  - `DASHSCOPE_TTS_MODEL`
  - `DASHSCOPE_TTS_VOICE_ID`
- **重要**：直接使用 skill 脚本，不要创建副本

## 第三步：ASR语音识别

### 3.1 ASR语音识别（Fun-ASR）

使用阿里云 **Fun-ASR 实时语音识别** API 对 `02-audio/` 中的语音文件进行精确识别，获取**真实的句子级和字级时间戳**（毫秒级精度）。

**识别脚本** (`asr_transcribe.py`)：

使用 skill 自带的脚本，无需创建副本：

```bash
cd <标题>-MM-DD
python3 "${CODEX_HOME:-$HOME/.codex}/skills/article-to-video-images/scripts/asr_transcribe.py" \
  02-audio/ \
  -o 03-asr/
```

**输出格式** (`asr-result-1.json`)：

```json
{
  "audio_file": "video-1.mp3",
  "duration": 230.06,
  "full_text": "你有没有想过，ChatGPT是如何理解你问的问题的？...",
  "segments": [
    {
      "text": "你有没有想过，ChatGPT是如何理解你问的问题的？",
      "start_time": 0.76,
      "end_time": 5.32
    },
    {
      "text": "其实，它的核心秘密就藏在一个叫做注意力机制的技术里。",
      "start_time": 5.40,
      "end_time": 10.85
    }
  ],
  "note": "时间戳由 Fun-ASR 实时识别提供（毫秒级精度）",
  "model": "fun-asr-realtime"
}
```

**说明**：
- 使用 `dashscope.audio.asr.Recognition` API 进行**实时语音识别**
- Fun-ASR **固定返回真实时间戳**，无需额外配置
- 时间戳精度为**毫秒级**（误差 ±50ms）

## 第三点五步：字幕校准

**重要**：ASR 结果只负责提供时间戳，不直接作为最终字幕内容。

最终字幕应以实际送进 TTS 的文本为准，并用 ASR 时间轴做对齐校准：

```bash
python3 "${CODEX_HOME:-$HOME/.codex}/skills/article-to-video-images/scripts/calibrate_subtitles.py" \
  03-asr/asr-result-1.json \
  -o 03.5-subtitles/calibrated-video-1.json \
  --source-text 02-audio/video-1.tts-source.txt
```

如果没有 `tts-source.txt`，也可以退回到分析文章：

```bash
python3 "${CODEX_HOME:-$HOME/.codex}/skills/article-to-video-images/scripts/calibrate_subtitles.py" \
  03-asr/asr-result-1.json \
  -o 03.5-subtitles/calibrated-video-1.json \
  --article 01-analysis/member-1.md
```

输出的 `calibrated-video-1.json` 中：
- 字幕文本来自 TTS 源文本或分析文章提取文本
- 时间戳来自 ASR
- `subtitle_segments` 是最终字幕生成的权威输入
- 每个 `segment` 包含 `start_time` 和 `end_time`（秒）

## 第四步：智能分镜脚本生成

### 4.1 分镜脚本生成流程

```
ASR识别结果 (03-asr/asr-result-*.json)
    ↓
原始分析文章 (01-analysis/member-*.md)
    ↓
【Claude分析】→ 生成 04-storyboard/storyboard-*.md
```

### 4.2 分镜脚本格式（镜头级结构化 schema）

分镜脚本不再直接写“图片生成提示词”，而是先写**结构化知识镜头页设计**，再由脚本根据镜头结构自动生成 prompt。

这个 skill 的基本单位不是普通图片，也不是纯 PPT 页，而是：

- **知识镜头页**：承担一个明确认知动作的静态讲解镜头
- **认知动作**：这页让观众完成什么理解动作
- **页面原型**：这页主要是哪种知识页
- **镜头风味**：这页在视频里呈现出什么节奏感

每个镜头至少包含以下字段：

- `时间码`
- `ASR文本`
- `口播内容`
- `镜头类型`
- `认知动作`
- `页面原型`
- `镜头风味`
- `视觉目标`
- `主体元素`
- `动作/关系`
- `构图与景别`
- `信息层级`
- `上屏文字策略`
- `风格锚点`
- `避免项`
- `数据层`

可选字段：

- `PPT视觉层`
  仅当 `镜头类型 = ppt_slide` 时使用，用来指定布局、标题、要点和图示方向。

```markdown
### 镜号1：不是 Prompt 包
- **时间码**: 00:00.000 - 00:19.300
- **ASR文本**: 作者做的不是更华丽的提示词包，而是围绕 OpenCode 的 agent harness。
- **口播内容**: 开场先立总论，把“聊天工具”和“执行系统”的差别说透。
- **镜头类型**: comparison_frame
- **认知动作**: 对比判断
- **页面原型**: 关系对照页
- **镜头风味**: 冲突对照
- **视觉目标**: 用强对照建立“聊天助手”和“执行系统”不是一回事
- **主体元素**:
  - 左侧普通聊天窗口
  - 右侧任务控制台与多角色节点
  - 中央清晰对比分隔带
- **动作/关系**:
  - 左侧信息堆积在单一对话框中
  - 右侧任务被拆分并沿多条路径流动
- **构图与景别**:
  - 景别: 中景
  - 构图: 左右强对照构图
  - 视角: 平视
- **信息层级**:
  - 前景: 对比分隔带与任务卡片
  - 中景: 左右两组主体
  - 背景: 深色科技空间
- **上屏文字策略**:
  - 模式: title_plus_bullets
  - 标题: 不是 Prompt 包
  - 副标题: 作者真正做的是 Agent Harness
  - 要点:
    - 左侧是聊天
    - 右侧是执行系统
- **风格锚点**:
  - 主风格: 科技演示、信息图和概念插画混合语言
  - 当前变体: 对照镜头
  - 色调: 深蓝灰底色配青色高亮
  - 光线: 清晰、高对比边缘光
  - 画面密度: balanced
- **避免项**:
  - 电影海报感
  - 英文 UI
  - 密集小字
- **数据层**:
  - 结论: 复杂任务依赖分工和调度
```

### 4.3 切镜策略

切镜不再按“每 3-5 句机械切一页”，而是按**语义段落 + 视觉功能**切镜。

规则：

1. 每镜优先覆盖一个完整语义动作，而不是固定句数。
2. 默认单镜时长控制在 8-18 秒；复杂对比或流程镜可稍长。
3. 开头 1-2 镜优先使用 `concept_scene` 或 `comparison_frame` 建立吸引力。
4. 解释段优先使用 `infographic` 或 `process_frame`。
5. 每 4-6 镜至少插入一个非 `ppt_slide` 镜头，避免全程像同模板换字。
6. 收束段优先使用 `quote_frame` 或轻量 `ppt_slide`，承担总结作用。

### 4.4 知识镜头页三层模型

每一镜先回答三个问题：

1. **认知动作**
   这一镜让观众完成什么理解动作？
   - `提问引入`
   - `定义判断`
   - `机制解释`
   - `对比判断`
   - `流程推进`
   - `案例举证`
   - `总结收束`

2. **页面原型**
   这一镜本质上是哪一类知识页？
   - `核心命题页`
   - `结构拆解页`
   - `关系对照页`
   - `流程推进页`
   - `案例证据页`
   - `总结收束页`

3. **镜头风味**
   这一镜在视频中的节奏感是什么？
   - `稳态讲解`
   - `冲突对照`
   - `压迫聚焦`
   - `层次推进`
   - `留白收束`

### 4.5 镜头类型与默认用途

推荐使用以下镜头类型：

- `ppt_slide`
  适合明确讲概念、列要点、做结构化总结。
- `infographic`
  适合关系图、模块图、系统结构、信息整合。
- `concept_scene`
  适合把抽象观点转成有主体和空间感的概念画面。
- `comparison_frame`
  适合做 before/after、单体/协作、旧范式/新范式等对照。
- `process_frame`
  适合步骤、链路、演进、阶段变化。
- `quote_frame`
  适合金句、总结、情绪收束和结尾停顿。

### 4.6 生成分镜脚本的步骤

1. 读取 ASR 与分析稿，先按语义段落切分，不在句中截断。
2. 先确定这段口播的**认知动作**，不要直接跳到画面描述。
3. 再选择最匹配的**页面原型**，确保它首先是一页有效的知识页。
4. 然后决定**镜头风味**，让这页在视频中有镜头感而不是普通静态课件。
5. 选择最合适的 `镜头类型`，而不是默认写成 PPT 页。
6. 写清楚主体、动作、构图、层次和文字策略，让画面可以被真正“设计”出来。
7. 先确定统一主风格，再给每镜定义局部变体，保证整体一致、局部有变化。
8. 在 `避免项` 中写出本镜头最怕出现的低质量模式，例如海报感、英文 UI、字太多、过于空。
9. 保存到 `04-storyboard/storyboard-*.md`，后续 prompt 由脚本自动生成。

## 第五步：图片生成

### 5.1 图片生成策略

根据分镜脚本中的图片生成提示词，批量生成图片集合。

**图片规格**：
- 格式：PNG 或 JPG
- 分辨率：1920x1080（横屏）或 1080x1920（竖屏）
- 风格：与分镜脚本中定义的风格一致

### 5.2 图片生成方式

根据用户可用的工具，可以选择以下方式之一：

**当前安装包状态**：
- 已包含：`export_prompts.py`
- 已包含：`generate_images.py`、`review_images.py`、`generate_comfyui_workflow.py`
- `generate_images.py` 现在支持 `dashscope` 与 `comfyui` 两种 provider
- 方式 A、B、C 都可以直接使用

#### 方式A：使用 AI 图像生成 API

如果有图像生成 API（如 DALL-E、Midjourney API、Stable Diffusion API 等）：

```bash
# 使用脚本批量生成图片
python3 "${CODEX_HOME:-$HOME/.codex}/skills/article-to-video-images/scripts/generate_images.py" \
  04-storyboard/storyboard-1.md \
  -o 05-images/video-1 \
  --attempt 1 \
  --provider dashscope
```

默认模型为 `qwen-image-2.0`。如果你的账号具备更高额度，也可以显式指定其他阿里云模型，例如：

```bash
python3 "${CODEX_HOME:-$HOME/.codex}/skills/article-to-video-images/scripts/generate_images.py" \
  04-storyboard/storyboard-1.md \
  -o 05-images/video-1 \
  --attempt 1 \
  --model qwen-image-2.0-pro
```

#### 方式B：导出提示词到文件

如果需要使用外部工具（如 Midjourney、Stable Diffusion WebUI）生成图片：

```bash
# 导出所有图片生成提示词到文本文件
python3 "${CODEX_HOME:-$HOME/.codex}/skills/article-to-video-images/scripts/export_prompts.py" \
  04-storyboard/ \
  -o prompts.txt
```

然后：
1. 将提示词复制到图像生成工具
2. 批量生成图片
3. 按命名规则保存到 `05-images/` 目录

#### 方式C：使用 ComfyUI 工作流

如果使用 ComfyUI + ControlNet/IPAdapter 进行批量图片生成：

```bash
# 1) 先导出每镜的布局控制图 + materialized workflow
python3 "${CODEX_HOME:-$HOME/.codex}/skills/article-to-video-images/scripts/generate_comfyui_workflow.py" \
  04-storyboard/storyboard-1.md \
  -o 05-images/video-1

# 2) 直接通过 ComfyUI API 执行图片生成
ARTICLE_TO_VIDEO_IMAGE_PROVIDER=comfyui \
COMFYUI_BASE_URL=http://127.0.0.1:8188 \
COMFYUI_WORKFLOW_TEMPLATE="${CODEX_HOME:-$HOME/.codex}/skills/article-to-video-images/templates/comfyui/controlnet_ipadapter_api.example.json" \
COMFYUI_CHECKPOINT_NAME="your-checkpoint.safetensors" \
COMFYUI_CONTROLNET_NAME="your-controlnet.safetensors" \
COMFYUI_IPADAPTER_MODEL="ip-adapter-plus_sdxl_vit-h.safetensors" \
COMFYUI_CLIP_VISION_MODEL="CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors" \
python3 "${CODEX_HOME:-$HOME/.codex}/skills/article-to-video-images/scripts/generate_images.py" \
  04-storyboard/storyboard-1.md \
  -o 05-images/video-1 \
  --attempt 1 \
  --provider comfyui
```

说明：
1. skill 会先为每个镜头自动生成一张 `layout guide`，作为 ControlNet 输入，稳定知识页镜头结构。
2. 如未显式提供 `COMFYUI_STYLE_IMAGE`，skill 会自动生成一张默认风格参考图，作为 IPAdapter 输入。
3. 如果你的 ComfyUI 图里使用的节点类型或输入名与示例模板不同，请从 ComfyUI 导出你自己的 API workflow JSON，并用 `COMFYUI_WORKFLOW_TEMPLATE` 指向它。

### 5.3 图片命名规则

```
05-images/
├── video-1/
│   ├── image-01.png           # 对应镜号1
│   ├── image-02.png           # 对应镜号2
│   ├── image-03.png           # 对应镜号3
│   └── ...
├── video-2/
│   ├── image-01.png
│   ├── image-02.png
│   └── ...
└── ...
```

### 5.4 图片质量检查

生成图片后，进行以下检查：

1. **数量检查**：每个视频的图片数量与分镜数量一致
2. **尺寸检查**：所有图片尺寸符合要求（1920x1080 或 1080x1920）
3. **风格一致性**：所有图片风格统一
4. **内容匹配**：图片内容与分镜描述一致
5. **文字可读性**：如果图片包含文字，确保清晰可读

可以使用 skill 自带的审核脚本：

```bash
python3 "${CODEX_HOME:-$HOME/.codex}/skills/article-to-video-images/scripts/review_images.py" \
  04-storyboard/storyboard-1.md \
  -o 05-images/video-1 \
  --attempt 1
```

审核通过的图片会被复制到 `05-images/video-1/final/`，后续视频合成只读取这个目录。

### 5.5 单视频串联执行

如果这次只做一篇文章、一个视频，可以直接使用单视频 orchestration 脚本：

```bash
python3 "${CODEX_HOME:-$HOME/.codex}/skills/article-to-video-images/scripts/run_single_video_pipeline.py" \
  --run-dir <标题>-MM-DD \
  --analysis <标题>-MM-DD/01-analysis/member-1.md \
  --storyboard <标题>-MM-DD/04-storyboard/storyboard-1.md
```

该脚本会顺序执行语音、ASR、图片生成、图片审核和最终视频合成，并把运行状态写到 `run-status.json`。

## 第五点五步：字幕生成

可以直接根据 `03-asr/asr-result-1.json` 生成字幕文件：

```bash
python3 "${CODEX_HOME:-$HOME/.codex}/skills/article-to-video-images/scripts/generate_subtitles.py" \
  03.5-subtitles/calibrated-video-1.json \
  -o output/video-1
```

脚本会同时生成：
- `output/video-1.srt`
- `output/video-1.ass`

当前 skill 在视频合成时默认读取 `output/video-1.ass` 进行烧录，以保留字幕样式；`output/video-1.srt` 会作为兼容性产物保留。

## 第六步：视频合成

### 6.1 视频合成策略

将生成的图片序列与音频文件合成为最终视频。

**合成逻辑**：
- 根据ASR时间戳确定每张图片的显示时长
- 图片切换与口播内容同步
- 添加适当的转场效果

### 6.2 视频合成脚本

使用 FFmpeg 进行视频合成：

```bash
# 使用 skill 自带的合成脚本
python3 "${CODEX_HOME:-$HOME/.codex}/skills/article-to-video-images/scripts/compose_video.py" \
  --storyboard 04-storyboard/storyboard-1.md \
  --images 05-images/video-1/ \
  --audio 02-audio/video-1.mp3 \
  --output output/video-1.mp4
```

### 6.3 合成参数

**视频规格**：
- 分辨率: 1920x1080 (16:9 横屏) 或 1080x1920 (9:16 竖屏)
- 帧率: 30fps
- 视频编码: H.264
- 音频编码: AAC
- 音画同步: 基于ASR时间戳精确同步

**转场效果**：
- 默认使用淡入淡出（fade）
- 转场时长：0.5秒

### 6.4 批量合成

批量合成所有视频：

```bash
cd <标题>-MM-DD

# 批量合成脚本
for i in 1 2 3 4; do
  python3 "${CODEX_HOME:-$HOME/.codex}/skills/article-to-video-images/scripts/compose_video.py" \
    --storyboard "04-storyboard/storyboard-${i}.md" \
    --images "05-images/video-${i}/" \
    --audio "02-audio/video-${i}.mp3" \
    --output "output/video-${i}.mp4"
done
```

## 执行流程

### 完整执行命令

```
1. 创建项目文件夹 <标题>-MM-DD
2. 执行第一步：四人小队分析
   └─→ 输出：4篇分析文章（01-analysis/，同时也是口播脚本）
3. 执行第二步：语音生成
   └─→ 直接使用分析文章生成语音
   └─→ 输出：音频文件（02-audio/video-*.mp3）
4. 执行第三步：ASR语音识别
   ├─→ 使用 Fun-ASR 识别音频
   └─→ 输出：ASR识别结果（03-asr/asr-result-*.json）
5. 执行第四步：智能分镜脚本生成
   ├─→ 结合ASR时间戳+分析文章+原始文章
   ├─→ 每3-5句话切分一个画面
   ├─→ 为每个画面生成图片生成提示词
   └─→ 输出：分镜脚本（04-storyboard/storyboard-*.md）
6. 执行第五步：图片生成
   ├─→ 根据分镜脚本的图片提示词
   ├─→ 批量生成图片集合
   └─→ 输出到 05-images/
7. 执行第六步：视频合成
   ├─→ 将图片序列与音频合成
   ├─→ 根据ASR时间码精确控制画面切换
   └─→ 输出到 output/video-*.mp4
8. 汇总输出
   └─→ 4个MP4视频文件已生成
```

### 分步执行命令

```bash
# 步骤1：生成分析文章（由Claude完成）

# 步骤2：生成语音（使用skill自带脚本）
cd <标题>-MM-DD
python3 "${CODEX_HOME:-$HOME/.codex}/skills/article-to-video-images/scripts/generate_voice_auto.py" \
  01-analysis/ \
  -o 02-audio/

# 步骤3：ASR识别（使用skill自带脚本）
python3 "${CODEX_HOME:-$HOME/.codex}/skills/article-to-video-images/scripts/asr_transcribe.py" \
  02-audio/ \
  -o 03-asr/

# 步骤4：智能分镜脚本生成（由Claude完成）
# 读取：03-asr/asr-result-*.json + 01-analysis/member-*.md + 原始文章
# 输出：04-storyboard/storyboard-*.md

# 步骤5：图片生成
python3 "${CODEX_HOME:-$HOME/.codex}/skills/article-to-video-images/scripts/generate_images.py" \
  04-storyboard/storyboard-1.md \
  -o 05-images/video-1 \
  --attempt 1 \
  --provider dashscope

# 或者切到 ComfyUI provider
ARTICLE_TO_VIDEO_IMAGE_PROVIDER=comfyui \
COMFYUI_BASE_URL=http://127.0.0.1:8188 \
COMFYUI_WORKFLOW_TEMPLATE="${CODEX_HOME:-$HOME/.codex}/skills/article-to-video-images/templates/comfyui/controlnet_ipadapter_api.example.json" \
COMFYUI_CHECKPOINT_NAME="your-checkpoint.safetensors" \
COMFYUI_CONTROLNET_NAME="your-controlnet.safetensors" \
COMFYUI_IPADAPTER_MODEL="ip-adapter-plus_sdxl_vit-h.safetensors" \
COMFYUI_CLIP_VISION_MODEL="CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors" \
python3 "${CODEX_HOME:-$HOME/.codex}/skills/article-to-video-images/scripts/generate_images.py" \
  04-storyboard/storyboard-1.md \
  -o 05-images/video-1 \
  --attempt 1 \
  --provider comfyui

# 步骤5.1：图片审核
python3 "${CODEX_HOME:-$HOME/.codex}/skills/article-to-video-images/scripts/review_images.py" \
  04-storyboard/storyboard-1.md \
  -o 05-images/video-1 \
  --attempt 1

# 步骤5.2：字幕校准
python3 "${CODEX_HOME:-$HOME/.codex}/skills/article-to-video-images/scripts/calibrate_subtitles.py" \
  03-asr/asr-result-1.json \
  -o 03.5-subtitles/calibrated-video-1.json \
  --source-text 02-audio/video-1.tts-source.txt

# 步骤5.3：生成字幕
python3 "${CODEX_HOME:-$HOME/.codex}/skills/article-to-video-images/scripts/generate_subtitles.py" \
  03.5-subtitles/calibrated-video-1.json \
  -o output/video-1

# 步骤6：视频合成（使用skill自带脚本，封装内嵌字幕）
python3 "${CODEX_HOME:-$HOME/.codex}/skills/article-to-video-images/scripts/compose_video.py" \
  --storyboard 04-storyboard/storyboard-1.md \
  --images 05-images/video-1/final/ \
  --audio 02-audio/video-1.mp3 \
  --subtitles output/video-1.ass \
  --output output/video-1.mp4
```

## 依赖与配置

### 必需配置

1. **FFmpeg** - `brew install ffmpeg`（用于视频合成）
2. **Python 3.8+** - 用于运行脚本

### 可选配置（用于真实语音）

**阿里云 API Key** - 用于生成真实语音（如未设置则生成静音占位音频）

```bash
# 检查是否已设置
printenv | grep DASHSCOPE_API_KEY

# 临时设置（当前终端会话）
export DASHSCOPE_API_KEY="your-api-key"
export DASHSCOPE_TTS_VENDOR="qwen"
export DASHSCOPE_TTS_MODEL="qwen3-tts-vc-2026-01-22"
export DASHSCOPE_TTS_VOICE_ID="qwen-tts-vc-bailian-voice-20260317224117018-3a82"

# 永久设置（添加到 ~/.zshrc 或 ~/.bashrc）
echo 'export DASHSCOPE_API_KEY="your-api-key"' >> ~/.zshrc
echo 'export DASHSCOPE_TTS_VENDOR="qwen"' >> ~/.zshrc
echo 'export DASHSCOPE_TTS_MODEL="qwen3-tts-vc-2026-01-22"' >> ~/.zshrc
echo 'export DASHSCOPE_TTS_VOICE_ID="qwen-tts-vc-bailian-voice-20260317224117018-3a82"' >> ~/.zshrc
source ~/.zshrc
```

**获取阿里云 API Key**：
1. 登录 [阿里云控制台](https://bailian.console.aliyun.com/)
2. 进入「API-KEY 管理」创建新密钥
3. 确保账户有余额（语音合成按调用量计费）

### 图片生成工具（可选）

根据用户的图片生成方式，可能需要：

1. **ComfyUI** - 本地 AI 图像生成
2. **Midjourney** - 在线图像生成服务
3. **DALL-E** - OpenAI 图像生成 API
4. **Stable Diffusion** - 本地或云端部署

**ComfyUI provider 推荐环境变量**：

```bash
export ARTICLE_TO_VIDEO_IMAGE_PROVIDER="comfyui"
export COMFYUI_BASE_URL="http://127.0.0.1:8188"
export COMFYUI_WORKFLOW_TEMPLATE="${CODEX_HOME:-$HOME/.codex}/skills/article-to-video-images/templates/comfyui/controlnet_ipadapter_api.example.json"
export COMFYUI_CHECKPOINT_NAME="your-checkpoint.safetensors"
export COMFYUI_CONTROLNET_NAME="your-controlnet.safetensors"
export COMFYUI_IPADAPTER_MODEL="ip-adapter-plus_sdxl_vit-h.safetensors"
export COMFYUI_CLIP_VISION_MODEL="CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors"

# 可选
export COMFYUI_STYLE_IMAGE="/abs/path/to/your/style-reference.png"
export COMFYUI_TIMEOUT_SECONDS="900"
export COMFYUI_CONTROL_STRENGTH="0.82"
export COMFYUI_IPADAPTER_WEIGHT="0.72"
```

### 安装依赖

**Python 依赖**：

```bash
# 安装 dashscope（语音合成 + ASR识别）
pip install dashscope

# 安装图片处理依赖
pip install Pillow

# 安装其他依赖
pip install pyyaml

# 使用 ComfyUI provider 时还需要 requests
pip install requests
```

**FFmpeg（必需）**：

```bash
# macOS
brew install ffmpeg

# Ubuntu/Debian
sudo apt-get install ffmpeg

# Windows (使用 chocolatey)
choco install ffmpeg
```

## 质量检查清单

**第一步（分析）：**
- [ ] 4篇分析文章是否覆盖不同维度
- [ ] 每篇文章1500-2500字，适合视频化
- [ ] 内容通俗易懂，避免术语堆砌
- [ ] 文章适合口播朗读（口语化、节奏感好）

**第二步（语音生成）：**
- [ ] 音频文件是否成功生成（`02-audio/video-*.mp3` 存在）
- [ ] 音频文件时长是否与文章匹配
- [ ] 语音朗读是否清晰自然

**第三步（ASR识别）：**
- [ ] ASR识别是否成功（`03-asr/asr-result-*.json` 存在）
- [ ] ASR时间戳是否精确（秒级精度）
- [ ] 句子分割是否合理（以标点结尾）

**第四步（智能分镜脚本）：**
- [ ] 分镜脚本是否包含**图片生成提示词**
- [ ] 时间码是否与ASR结果对齐
- [ ] 页面切分是否符合3-5句话原则
- [ ] 是否在句子边界处切分（不在句中）
- [ ] 整体视觉风格是否确定并记录
- [ ] 图片生成提示词是否详细、风格一致

**第五步（图片生成）：**
- [ ] 图片数量是否与分镜数量一致
- [ ] 图片尺寸是否符合要求
- [ ] 图片风格是否统一
- [ ] 图片内容与分镜描述是否匹配
- [ ] 图片中的文字是否清晰可读

**第六步（视频合成）：**
- [ ] 视频合成是否成功
- [ ] 输出视频格式是否正确（1920x1080 或 1080x1920）
- [ ] 音画是否精确同步
- [ ] 视频时长是否与音频完全匹配
- [ ] 转场效果是否自然

## 与 article-to-video 的区别

| 特性 | article-to-video | article-to-video-images |
|------|------------------|------------------------|
| 第一步 | 四人小队分析 | 相同 |
| 第二步 | 裸口播脚本 | **删除，直接使用分析文章** |
| 第三步 | 语音输出 | 语音输出 |
| 第四步 | ASR识别 | ASR识别 |
| 第五步 | 智能分镜脚本 | 智能分镜脚本 **+ 图片生成提示词** |
| 第六步 | PPT视频生成（Remotion） | **图片生成** |
| 第七步 | 自动渲染 | **视频合成（图片+音频）** |

**核心区别**：
1. **更简洁的流程**：删除 02-scripts 步骤，直接使用分析文章
2. **图片驱动**：用图片序列替代 PPT 页面
3. **不同的输出**：生成图片集合而非代码项目
4. **合成方式**：图片+音频合成而非程序化渲染

## 示例

**示例输入：**
> "请帮我将这篇关于Transformer架构的论文转换为图片风格的科普视频"

**示例输出结构：**
```
transformer-explained-03-04/
├── 01-analysis/                       # 第一步：文章分析（同时也是口播脚本）
│   ├── team-roles.md
│   ├── member-1-attention-mechanism.md
│   ├── member-2-architecture-comparison.md
│   ├── member-3-training-insights.md
│   └── member-4-real-world-impact.md
│
├── 02-audio/                          # 第二步：语音合成输出
│   ├── video-1.mp3                    # 视频1音频文件
│   ├── video-2.mp3
│   ├── video-3.mp3
│   └── video-4.mp3
│
├── 03-asr/                            # 第三步：ASR识别结果
│   ├── asr-result-1.json              # ASR时间戳结果
│   ├── asr-result-2.json
│   ├── asr-result-3.json
│   └── asr-result-4.json
│
├── 04-storyboard/                     # 第四步：智能分镜脚本+图片提示词
│   ├── storyboard-1-attention.md      # 包含图片生成提示词的分镜
│   ├── storyboard-2-architecture.md
│   ├── storyboard-3-training.md
│   └── storyboard-4-impact.md
│
├── 05-images/                         # 第五步：生成的图片集合
│   ├── video-1/
│   │   ├── image-01.png               # 开场画面
│   │   ├── image-02.png               # 概念解释
│   │   ├── image-03.png               # 数据可视化
│   │   └── ...
│   ├── video-2/
│   ├── video-3/
│   └── video-4/
│
└── output/                            # 第六步：最终视频输出
    ├── video-1.mp4
    ├── video-2.mp4
    ├── video-3.mp4
    └── video-4.mp4
```
