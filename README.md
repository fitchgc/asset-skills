# Agent Asset Skills

面向 AI Agent（Claude Code、Codex、Pi 等）的多模态与游戏创作资产（图像、音频、视频、3D）生成、编辑、后处理与自动化管线管理的同构技能集合。

包含了 4 个核心资产技能（Asset Skills）：
- **`asynx-image-pro`**：图像生成、垫图参考创作、局部重绘批注、图生图编辑与离线图像处理。
- **`asynx-audio`**：音效素材（SFX）、背景配乐（Music）、角色台词（TTS/Speech）生成与音频剪辑标准化。
- **`asynx-video`**：文生视频、图生视频、视频扩展编辑、抽帧生成 2D 精灵图集与音视频合成。
- **`asynx-3d`**：文生/图生 3D 模型、骨骼绑定检查、自动骨骼绑定与蒙皮、动画重定向与序列帧渲染。

---

## 核心特性

- **跨模态引用（Cross-Modal Pipelines）**：所有技能共用本地 SQLite 状态账本，Agent 可通过上下文任务 ID 无缝串联不同模态（例如：概念原画 -> 图生 3D 模型 -> 自动骨骼绑定 -> 行走动画 -> 导出 2D 游戏精灵图集）。
- **自包含与开箱即用（Self-Contained Bundling）**：每个 Skill 均包含独立的配置与规则定义，零全局环境污染。
- **本地免 Key 确定性后处理（Keyless Local Post-Processing）**：裁剪、格式转换、音频修剪与响度标准化、视频抽帧、3D 模型纯 Python 检查等均无需消耗 API Key，本地离线秒级执行。
- **按需本地交互工作台（Local UI Workbench）**：Agent 可按需自动拉起轻量 Web 服务，在浏览器中完成视觉交互操作（局部重绘圈选标注、视频时间轴去背、WebGL 3D 预览与多方向序列帧图集渲染）。

---

## 环境要求

1. **Python 3.10+**（推荐 Python 3.10 ~ 3.12）
2. **多媒体系统工具（音频与视频处理）**：
   - 运行 `asynx-audio` 与 `asynx-video` 的高级处理（格式转换、剪辑、抽帧合成等）依赖 `ffmpeg` 和 `ffprobe`：
     - **macOS**：`brew install ffmpeg`
     - **Ubuntu / Debian**：`sudo apt update && sudo apt install -y ffmpeg`
     - **Windows**：`winget install Gyan.FFmpeg` 或 `choco install ffmpeg`
3. **图像与 3D 处理**：
   - 图像及精灵图集拼合依赖 `Pillow`（内置或执行 `pip install pillow`）。
   - 3D 离线模型元数据解析与规范校验基于纯 Python 实现，**无需安装 Blender**。

---

## 安装说明 (Installation)

运行环境要求 Python 3.10+。生成多模态资产须配置 [LLMAPI API Key](https://llmapi.site/api-keys)；仅使用本地处理功能时无需配置密钥。

### 自动化安装（推荐）

使用仓库自带的安装器一键完成环境检测、依赖安装与密钥配置：

```bash
git clone https://github.com/dawnx/asset-skills.git
cd asset-skills
python3 install.py
```

Windows 环境下将最后一行改为 `py install.py`。

安装器会自动检测当前环境中的 Codex、Claude Code 或 Pi，并引导在终端中安全输入 API Key（无需且严禁将密钥发送给 Agent 对话）：

常用参数选项：
- `--target`：指定安装目标 Agent，可选 `auto`（默认自动检测）、`codex`、`claude` 或 `both`。
- `--skill`：指定安装的技能，默认全部安装 (`all`)。可选：`image`、`audio`、`video`、`3d`，或多选如 `--skill image,audio`。
- `--skip-config`：仅安装技能，不提示输入 API Key。
- `--no-verify`：跳过密钥连通性校验。
- `--uninstall`：卸载已安装的 Skill，保留密钥、任务记录和已生成文件（可配合 `--yes` 跳过确认）。

默认安装位置：

| Agent | 用户主目录下的路径 |
| --- | --- |
| Codex / Pi | `~/.agents/skills/asynx-{image,audio,video,3d}` |
| Claude Code | `~/.claude/skills/asynx-{image,audio,video,3d}` |

### 更新与卸载

- **升级**：可直接在对话中要求 Agent“检查 Asynx Skill 更新”或重新运行 `python3 install.py`。
- **卸载**：在仓库根目录执行 `python3 install.py --uninstall` 即可完成干净卸载。

> 如需使用软链接接入（开发者联动）、解压预打包 zip 归档或从源码重新构建，请参阅 [其他安装方式与 CLI 脚本直接调用指南](CLI_USAGE.md)。

---

## 配置说明 (Configuration)

各 Skill 共用同一套本机配置与本地状态账本，只需配置一次即可全局生效：

1. **自动引导**：运行 `python3 install.py` 时会主动引导配置并加密存储。
2. **环境变量**：适用于 CI/CD 或临时环境，直接导出 `export ASYNX_API_KEY="asx-xxxxxxxxxxxx"`。
3. **终端配置**：如需后续更新密钥，可让用户在终端中运行配置命令（详见 [CLI 指南](CLI_USAGE.md#二配置与环境诊断命令)）。

---

## 使用指南：在 Agent 中调用技能

技能安装完成后，Agent 在启动时会自动加载技能上下文。你无需手动记忆或拼写复杂的命令行参数，**只需在对话框中用自然语言向 Agent 发出创作指令**，Agent 会自动决定调用对应 Skill 进行云端生成、本地后处理或拉起本地可视化工作台。

---

### 1. 图像创作与编辑 (`asynx-image-pro`)

在与 Agent 对话时直接描述图片创作意图：

- **文生图与角色设计**：
  > “帮我生成一张赛博朋克风格的发光霓虹猫咪，16:9 画幅。”
- **垫图参考创作（保持一致性）**：
  > “参考角色原画 `character.png`，保持主角构图与发型，生成一套在金色秋季枫叶背景下的立绘。”
- **图生图编辑与局部修改**：
  > “把刚才生成的图片中主角的衣服颜色修改为暗红色金属铠甲。”
- **启动交互式标注与重绘工作台**：
  > “打开刚才图片的局部重绘工作台，我要圈选并涂鸦修改面部细节。”  
  *(Agent 会拉起本地 Web 服务并提供访问链接，在浏览器中圈选涂鸦后点击生成即可自动回传并替换结果)*
- **本地图片免 Key 后处理**：
  > “把 `input.png` 等比缩放到 512 宽，裁剪中心 256x256 区域，并转换为 WebP 格式。”

---

### 2. 音频创作与剪辑 (`asynx-audio`)

在对话中向 Agent 请求游戏音效、配乐或台词朗读：

- **游戏音效（SFX）**：
  > “生成一个复古像素游戏里拾取金币的清脆音效，时长 2.5 秒。”
- **背景配乐（Music）**：
  > “生成一段 30 秒的 8-bit 地牢快节奏战斗纯音乐。”
- **角色台词语音（Speech / TTS）**：
  > “用英勇战士的音色朗读这句台词：‘勇敢的冒险者，愿圣光庇佑你的道路！’”
- **音色延续克隆**：
  > “沿用上一句语音的战士音色，继续朗读：‘前进，决不后退！’”
- **本地音频确定性处理**：
  > “将 `bgm.mp3` 转为 OGG 格式，截取前 15 秒，添加 1 秒淡入与 2 秒淡出，并进行 EBU R128 标准化。”

---

### 3. 视频创作与精灵图集 (`asynx-video`)

通过自然语言驱动视频生成、动态转写与 2D 游戏动画帧提取：

- **文生视频**：
  > “生成一段太空飞船穿梭于流星群的电影质感 5 秒视频，16:9 画幅。”
- **图生视频**：
  > “基于刚才生成的角色立绘，生成一段人物转身向镜头微笑打招呼的 5 秒视频。”
- **首末关键帧过渡**：
  > “以 `day.png` 为开头、`night.png` 为结尾，生成城堡从白昼平滑过渡到夜晚灯火辉煌的动态视频。”
- **2D 游戏动画序列帧图集导出**：
  > “把角色跑步动作视频按 10 fps 抽帧，打包导出为 128x128 规格的 2D 游戏精灵图集（atlas.png 与 manifest.json 清单）。”
- **启动视频交互工作台**：
  > “启动视频时间轴抽帧去背工作台，我想为这个动作视频去背景并导出动画序列。”

---

### 4. 3D 模型与骨骼动画 (`asynx-3d`)

在对话中直接构建 3D 资产、检查规范并绑定动画：

- **文生/图生 3D 模型**：
  > “把这张角色概念图转成 3D 模型，限制在 15000 三角面以内，输出 GLB 格式。”
- **自动骨骼绑定与权重蒙皮**：
  > “为刚才生成的 3D 模型进行自动人形骨骼绑定。”
- **动作重定向（Animate）**：
  > “让这个绑定好的 3D 角色应用跑步（run）动画，导出就地循环动画的 GLB 文件。”
- **本地纯 Python 规范校验（免 Blender）**：
  > “检查 `character.glb` 是否符合规范：要求面数不超过 20000、必须包含 PBR 贴图和骨骼动画，并提取其中的贴图文件。”
- **启动浏览器 3D 查看与渲染工作台**：
  > “启动 3D 模型查看器预览刚才的模型” 或 “打开序列帧渲染工作台，导出等轴测 8 方向奔跑精灵图集。”

---

### 5. 跨模态自动化管线（Agent 协同创作）

得益于共享的状态账本，你可以让 Agent 一句话串联多模态生产链路：

> **用户需求提示示例**：  
> “帮我设计一个 Q 版魔法师游戏角色：  
> 1. 先生成概念设计图；  
> 2. 转成 15000 面以内的 3D 模型；  
> 3. 自动绑定人形骨骼并应用奔跑动画；  
> 4. 导出 8 方向 2D 游戏序列帧图集；  
> 5. 最后为他配一段火球术施法音效和森林探索背景音乐。”

Agent 会在后台依次调用 `asynx-image-pro` -> `asynx-3d` -> `asynx-audio`，并通过上下文传递生成的任务 ID 实现各资产的自动衔接。

---

## 进阶：直接使用 Python 命令行

如果你需要在自动化脚本、CI/CD 流程或独立终端中**直接运行 Python CLI 命令**（包括详细的参数选项、免 Key 图像/音视频/3D 批处理、批次管理等），请参阅完整指南：

👉 **[其他安装方式与 CLI 脚本直接调用指南 (CLI_USAGE.md)](CLI_USAGE.md)**

---

## 仓库结构 (Monorepo Layout)

```text
.
├── install.py                   # 统一自动化安装与卸载脚本
├── skills/                      # 独立打包分发的 4 个 Asset Skill 目录
│   ├── asynx-image-pro/         # 图像创作技能 (SKILL.md, scripts/, references/)
│   ├── asynx-audio/             # 音频创作技能 (SKILL.md, scripts/, references/)
│   ├── asynx-video/             # 视频创作技能 (SKILL.md, scripts/, references/)
│   └── asynx-3d/                # 3D 创作技能 (SKILL.md, scripts/, references/)
├── shared/                      # 共享基础库 (网关、状态账本、配置与 UI 服务器)
├── src/                         # 模态特定核心源码 (图像、音频、视频、3D与构建器)
├── dist/                        # 预打包发布的独立 Skill 压缩包 (.zip)
├── docs/                        # 架构设计记录 (ADR) 与领域文档
├── CLI_USAGE.md                 # 进阶安装方式与 CLI 脚本直接调用指南
└── tests/                       # 单元测试与端到端跨模态集成测试
```

---

## 运行自动化测试

项目包含完备的测试套件（覆盖状态账本迁移、各模态任务生成、本地免 Key 算子、本地工作台与跨模态串联）：

```bash
# 运行全部测试
python3 -m unittest discover tests
```

---

## 许可证

本项目遵循 MIT 许可证。
