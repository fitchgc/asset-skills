# Agent Asset Skills

面向 AI Agent（Claude Code、Codex、Pi 等）的多模态与游戏创作资产（图像、音频、视频、3D）生成、编辑、后处理与自动化管线管理的同构技能集合。

包含了 4 个核心资产技能（Asset Skills）：
- **`asynx-image-pro`**：图像生成、垫图参考创作、局部重绘批注、图生图编辑与离线图像处理。
- **`asynx-audio`**：音效素材（SFX）、背景配乐（Music）、角色台词（TTS/Speech）生成与音频剪辑标准化。
- **`asynx-video`**：文生视频、图生视频、视频扩展编辑、抽帧生成 2D 精灵图集与音视频合成。
- **`asynx-3d`**：文生/图生 3D 模型、骨骼绑定检查、自动骨骼绑定与蒙皮、动画重定向与序列帧渲染。

---

## 核心特性

- **跨模态引用（Cross-Modal Pipelines）**：所有技能共用本地 SQLite 状态账本，通过 `--from-task <task-id>` 即可无缝串联不同模态（例如：概念原画 -> 图生 3D 模型 -> 自动骨骼绑定 -> 行走动画 -> 导出 2D 游戏精灵图集）。
- **自包含与开箱即用（Self-Contained Bundling）**：每个 Skill 均可作为独立目录发布分发，内置 CLI 脚本、依赖与 Agent 引导规则，零全局依赖。
- **本地免 Key 确定性后处理（Keyless Local Post-Processing）**：裁剪、格式转换、音频修剪与响度标准化、视频抽帧、3D 模型纯 Python 检查等无需 API Key，离线秒级执行。
- **按需本地交互工作台（Local UI Workbench）**：由 CLI 临时按需拉起轻量 Web 服务，在浏览器中完成需要视觉交互的操作（局部重绘圈选标注、视频时间轴去背、WebGL 3D 预览与多方向序列帧图集渲染）。

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

### 方式一：自动化安装（推荐：Codex / Claude Code）

可使用仓库自带的安装器一键完成检测、依赖安装与密钥配置：

```bash
git clone https://github.com/dawnx/asset-skills.git
cd asset-skills
python3 install.py
```

Windows 环境下将最后一行改为 `py install.py`。

安装器会自动检测 Codex 与 Claude Code，并引导用户输入 API Key。密钥应在终端中输入，不得发送至对话。

常用参数选项：
- `--target`：安装目标 Agent，可选 `auto`（默认自动检测）、`codex`、`claude` 或 `both`。
- `--skill`：指定安装的技能，默认全部安装 (`all`)。可选：`image`、`audio`、`video`、`3d`，或逗号分隔多选如 `--skill image,audio`。
- `--skip-config`：安装但不询问 API Key。
- `--no-verify`：跳过密钥和模型目录连通性检查。
- `--uninstall`：卸载已安装的 Skill，保留密钥、任务记录和已生成文件（配合 `--yes` 跳过确认）。

默认安装位置：

| Agent | 用户主目录下的路径 |
| --- | --- |
| Codex / Pi | `~/.agents/skills/asynx-{image,audio,video,3d}` |
| Claude Code | `~/.claude/skills/asynx-{image,audio,video,3d}` |

### 方式二：软链接接入（开发者本地联动）

通过软链接将技能接入当前使用的 AI Agent 运行时，修改代码实时生效：

#### 1. Claude Code
```bash
# 创建 Claude 技能目录
mkdir -p ~/.claude/skills

# 软链接所有技能
for skill in asynx-image-pro asynx-audio asynx-video asynx-3d; do
  ln -s "$(pwd)/skills/$skill" ~/.claude/skills/$skill
done
```

#### 2. Codex / Pi
```bash
# 创建 Agent 技能目录
mkdir -p ~/.agents/skills

# 软链接所有技能
for skill in asynx-image-pro asynx-audio asynx-video asynx-3d; do
  ln -s "$(pwd)/skills/$skill" ~/.agents/skills/$skill
done
```

*(Windows 环境可在对应用户的 `%USERPROFILE%\.claude\skills` 或 `%USERPROFILE%\.agents\skills` 目录下创建目录符号链接或解压压缩包)*

### 方式三：使用独立构建产物 (Dist Packages)

可以直接下载或解压 `dist/` 目录中打包好的独立归档：
- `dist/asynx-image-pro.zip`
- `dist/asynx-audio.zip`
- `dist/asynx-video.zip`
- `dist/asynx-3d.zip`

解压至任意 Agent 的 `skills/` 目录即可，内部已包含所有必要运行时文件。

### 方式四：源码编译与重新打包 (开发者构建)

若修改了 `shared/` 或 `src/` 中的核心逻辑，可运行构建脚本重新生成各个 Skill：

```bash
# 重新构建所有技能到 skills/ 目录
python3 src/build_skills.py --skill all

# 构建并打包到 dist/ 目录 (生成 zip 压缩包与 sha256 清单 json)
python3 src/build_skills.py --skill all --package
```

### 更新与卸载

升级时可直接要求 Agent“检查 Asynx Skill 更新”或“升级 Asynx Skill”。

使用本仓库安装器安装的副本，卸载时在克隆的仓库根目录运行：

```bash
python3 install.py --uninstall
```

Windows 环境使用 `py install.py --uninstall`。卸载将保留配置密钥、任务记录和已生成文件。其他方式安装的副本，可通过对应 Agent 的 Skill 管理功能移除安装目录。

---

## 配置说明 (Configuration)

各 Skill 共用同一套本机全局配置与本地状态账本，只需配置一次即可在全部 4 个技能中生效。

### 1. 安全配置 API Key（推荐）

在交互式终端中执行任一技能的 `configure` 命令，根据提示输入 API Key：

```bash
python3 skills/asynx-image-pro/scripts/asynx.py configure
```

- 输入格式以 `asx-` 开头的有效 API Key。
- 若使用私有网关或自托管地址，可传入 `--base-url`：
  ```bash
  python3 skills/asynx-image-pro/scripts/asynx.py configure --base-url "https://your-domain.com/api"
  ```

### 2. 环境变量配置（适用于 CI/CD 或临时会话）

也可以通过环境变量指定密钥与网关地址：

```bash
# 必填：Asynx 统一 API Key
export ASYNX_API_KEY="asx-xxxxxxxxxxxx"

# 可选：远端网关地址（默认为官方网关 https://asynx.llmapi.site/api）
export ASYNX_BASE_URL="https://asynx.llmapi.site/api"

# 可选：自定义配置文件存储路径
export ASYNX_CONFIG_PATH="~/.config/asynx/config.json"
```

### 3. 配置存储路径与优先级

- **默认配置文件存储位置**：
  - macOS / Linux：`~/.config/asynx/config.json`
  - Windows：`%APPDATA%\Asynx\config.json`
- **本地状态数据库存储位置**：
  - macOS / Linux：`~/.local/state/asx/state.db`
  - Windows：`%LOCALAPPDATA%\Asynx\state.db`
- **配置优先级**：环境变量（`ASYNX_API_KEY`） > 配置文件（`config.json`）。

### 4. 环境自检与连通性诊断 (Doctor)

配置完成后，使用 `doctor` 命令诊断当前系统环境与依赖是否完备：

```bash
# 离线检查配置文件、权限、Pillow 与 ffmpeg 工具链
python3 skills/asynx-image-pro/scripts/asynx.py doctor

# 联网验证密钥与网关连通性
python3 skills/asynx-image-pro/scripts/asynx.py doctor --verify

# 查看当前配置状态及密钥脱敏信息
python3 skills/asynx-image-pro/scripts/asynx.py config status
```

---

## 使用指南 (Usage)

所有技能通过统一脚本 `scripts/asynx.py` 驱动。命令支持单次生成、批量批次、本地免 Key 处理及浏览器交互工作台。

---

### 1. 图像创作 (`asynx-image-pro`)

#### 远程生成与编辑
```bash
# 文生图 (Text-to-Image)
python3 skills/asynx-image-pro/scripts/asynx.py generate --prompt "赛博朋克风格发光霓虹猫咪，8k高清" --aspect-ratio "16:9"

# 垫图参考生成 (Reference Image)
python3 skills/asynx-image-pro/scripts/asynx.py generate --prompt "保持相同构图，切换为金色秋季枫叶背景" --reference "/path/to/cat.png"

# 图生图编辑 (Image-to-Image Edit)
python3 skills/asynx-image-pro/scripts/asynx.py edit --prompt "将角色的衣服颜色改为暗红色铠甲" --image "/path/to/character.png"

# 使用任务 ID 延续编辑 (From Task)
python3 skills/asynx-image-pro/scripts/asynx.py edit --prompt "添加夜幕降临的星空光效" --from-task "<task-id>"
```

#### 本地确定性图片处理 (免 API Key)
```bash
# 查看图片基础元数据 (尺寸、格式、色彩模式)
python3 skills/asynx-image-pro/scripts/asynx.py image info "input.png"

# 调整尺寸并保持比例
python3 skills/asynx-image-pro/scripts/asynx.py image resize "input.png" --output "thumb.png" --width 512

# 裁剪指定区域 (x1, y1, x2, y2)
python3 skills/asynx-image-pro/scripts/asynx.py image crop "input.png" --output "cropped.png" --box 0,0,256,256

# 九宫格/网格切图 (用于游戏瓦片地图或图集切分)
python3 skills/asynx-image-pro/scripts/asynx.py image slice "tileset.png" --output-dir "tiles/" --rows 4 --columns 4

# 格式转换
python3 skills/asynx-image-pro/scripts/asynx.py image convert "photo.jpg" --output "photo.webp" --format webp

# 应用 Alpha 遮罩
python3 skills/asynx-image-pro/scripts/asynx.py image apply-mask "photo.png" --mask "mask.png" --output "cutout.png"
```

#### 本地交互工作台 (Local UI)
```bash
# 启动浏览器局部重绘圈选与标注工作台（自动提取 Mask 遮罩与修改指令）
python3 skills/asynx-image-pro/scripts/asynx.py ui annotate --image "character.png"
```

---

### 2. 音频创作 (`asynx-audio`)

#### 声音合成与生成
```bash
# 游戏音效合成 (SFX)
python3 skills/asynx-audio/scripts/asynx.py sfx --prompt "宝箱开启金币散落清脆声效" --duration 2.5

# 背景音乐配乐生成 (Music)
python3 skills/asynx-audio/scripts/asynx.py music --prompt "8-bit 像素地牢战斗芯片音乐，高燃快节奏" --duration 30 --instrumental

# 角色台词语音合成 (Speech / TTS)
python3 skills/asynx-audio/scripts/asynx.py speech --text "勇敢的冒险者，愿圣光庇佑你的道路！" --voice "warrior" --speed 1.0

# 声音克隆/音色延续合成
python3 skills/asynx-audio/scripts/asynx.py speech --text "前进，决不后退！" --from-task "<previous-audio-task-id>"
```

#### 本地确定性音频处理 (免 API Key)
```bash
# 查看音频元数据 (编码格式、采样率、声道数、比特率、时长)
python3 skills/asynx-audio/scripts/asynx.py audio info "sound.wav"

# 格式转换 (wav, mp3, ogg, m4a, flac)
python3 skills/asynx-audio/scripts/asynx.py audio convert "sound.wav" --output "sound.ogg"

# 音频剪辑修剪 (精确截取起止点)
python3 skills/asynx-audio/scripts/asynx.py audio trim "track.mp3" --output "clip.mp3" --start 5.0 --duration 10.0

# 淡入淡出包络处理
python3 skills/asynx-audio/scripts/asynx.py audio fade "bgm.mp3" --output "bgm_fade.mp3" --fade-in 1.0 --fade-out 2.0

# 广播级响度标准化 (EBU R128 标准)
python3 skills/asynx-audio/scripts/asynx.py audio normalize "dialogue.mp3" --output "dialogue_norm.mp3" --target-lufs -14.0

# 多个音频文件拼接合并
python3 skills/asynx-audio/scripts/asynx.py audio concat "intro.mp3" "main_loop.mp3" "outro.mp3" --output "full_ost.mp3"
```

---

### 3. 视频创作 (`asynx-video`)

#### 视频生成与编辑
```bash
# 文生视频 (Text-to-Video)
python3 skills/asynx-video/scripts/asynx.py generate --prompt "太空飞船穿梭于流星群，电影级景深" --duration 5.0 --aspect-ratio "16:9"

# 本地图生视频 (Image-to-Video)
python3 skills/asynx-video/scripts/asynx.py generate --prompt "角色转身向镜头微笑打招呼" --image "/path/to/character.png"

# 跨模态图生视频 (直接使用 asynx-image-pro 产出的任务 ID)
python3 skills/asynx-video/scripts/asynx.py generate --prompt "镜头 360 度环绕人物旋转" --from-task "<image-task-id>"

# 首末双关键帧控制生成
python3 skills/asynx-video/scripts/asynx.py generate --prompt "城堡从白昼平滑演变为夜晚灯火辉煌" --image "day.png" --end-image "night.png"

# 视频语义扩展与编辑
python3 skills/asynx-video/scripts/asynx.py edit --prompt "将环境变换为大雪纷飞的冬日场景" --video "street.mp4"
```

#### 本地确定性视频处理 (免 API Key)
```bash
# 查看视频参数 (分辨率、帧率、时长、视频编码、音频编码)
python3 skills/asynx-video/scripts/asynx.py video info "clip.mp4"

# 视频精准片段修剪
python3 skills/asynx-video/scripts/asynx.py video trim "clip.mp4" --output "short.mp4" --start 1.0 --duration 4.0

# 视频离线抽帧为图片序列
python3 skills/asynx-video/scripts/asynx.py video extract-frames "action.mp4" --output-dir "frames/" --fps 12

# 抽帧并打包为 2D 游戏动画序列帧图集 (生成 atlas.png 精灵图大图与 manifest.json 元数据清单)
python3 skills/asynx-video/scripts/asynx.py video spritesheet "action.mp4" --output-dir "spritesheet/" --fps 10 --width 128 --height 128

# 视频转 GIF 动图
python3 skills/asynx-video/scripts/asynx.py video convert "clip.mp4" --output "preview.gif"

# 音视频轨道合成 (Mux)
python3 skills/asynx-video/scripts/asynx.py video mux --video "silent_clip.mp4" --audio "bgm.mp3" --output "final_video.mp4"
```

#### 本地交互工作台 (Local UI)
```bash
# 启动视频时间轴抽帧、去背与精灵图集导出工作台
python3 skills/asynx-video/scripts/asynx.py ui frame-sequence --video "character_run.mp4"

# 启动 2D 序列帧精灵图集动画回放预览器
python3 skills/asynx-video/scripts/asynx.py ui preview-sequence --atlas "atlas.png" --manifest "manifest.json"
```

---

### 4. 3D 模型与动画 (`asynx-3d`)

#### 3D 生成与骨骼动画管线
```bash
# 文生 3D 模型 (Text-to-3D)
python3 skills/asynx-3d/scripts/asynx.py generate --prompt "中世纪双刃巨斧，暗黑浮雕花纹，PBR贴图" --output-format glb

# 本地图生 3D 模型 (Image-to-3D，支持限制面数)
python3 skills/asynx-3d/scripts/asynx.py generate --image "/path/to/monster.png" --face-limit 15000

# 跨模态图生 3D (使用 asynx-image-pro 生成的概念图任务 ID)
python3 skills/asynx-3d/scripts/asynx.py generate --from-task "<image-task-id>"

# 骨骼绑定前置合规检查
python3 skills/asynx-3d/scripts/asynx.py rig-check --model-file "character.glb"

# 云端自动骨骼绑定与权重蒙皮 (Rig)
python3 skills/asynx-3d/scripts/asynx.py rig --from-task "<gen-task-id>" --rig-type biped

# 动作重定向 (Animate：让角色执行标准动画动作)
python3 skills/asynx-3d/scripts/asynx.py animate --from-task "<rig-task-id>" --animation "walk" --animate-in-place

# 材质重新贴图 (Texture)
python3 skills/asynx-3d/scripts/asynx.py texture --model-file "mesh.glb" --prompt "赛博朋克风格，磨砂金属与青色发光电路"
```

#### 本地纯 Python 3D 模型处理 (免 API Key，无需 Blender)
```bash
# 查看 3D 模型结构 (顶点数、三角面数、材质槽、内嵌贴图、骨骼关节数、动画片段)
python3 skills/asynx-3d/scripts/asynx.py model3d info "character.glb"

# 游戏规范合规性校验 (校验多边形面数上限、必须包含 PBR 贴图、骨骼/动画要求)
python3 skills/asynx-3d/scripts/asynx.py model3d validate "character.glb" --max-faces 20000 --require-pbr --require-animations

# 直接从 GLB 中离线解包提取所有内嵌 PBR 贴图到本地目录
python3 skills/asynx-3d/scripts/asynx.py model3d extract-textures "character.glb" --output-dir "textures/"
```

#### 本地交互工作台 (Local UI)
```bash
# 启动浏览器 WebGL 3D 检查器 (支持模型 360 度旋转、骨骼动画播放、PBR 贴图通道检查)
python3 skills/asynx-3d/scripts/asynx.py ui viewer --model-file "character.glb"

# 启动多视角 2D 序列帧图集渲染工作台 (支持 1向/4向/8向正交与等轴测视角自动渲染并打包图集)
python3 skills/asynx-3d/scripts/asynx.py ui render-sequence --model-file "character.glb"
```

---

## 跨模态自动化管线实战示例

通过共享的 SQLite 状态账本，可以将多种资产模态串联成一条完整的端到端游戏资产生产管线：

```bash
# 步骤 1：生成游戏主角概念图 (asynx-image-pro) -> 输出任务 ID: task_img_001
python3 skills/asynx-image-pro/scripts/asynx.py generate --prompt "Q版魔法师角色，站立姿态，清晰白底"

# 步骤 2：由概念图生成 3D 角色模型 (asynx-3d) -> 输出任务 ID: task_3d_002
python3 skills/asynx-3d/scripts/asynx.py generate --from-task "task_img_001" --face-limit 15000

# 步骤 3：为 3D 角色自动绑定人形骨骼 (asynx-3d) -> 输出任务 ID: task_rig_003
python3 skills/asynx-3d/scripts/asynx.py rig --from-task "task_3d_002" --rig-type biped

# 步骤 4：为绑定好的角色重定向跑步动作 (asynx-3d) -> 产出带动画的 GLB 模型
python3 skills/asynx-3d/scripts/asynx.py animate --from-task "task_rig_003" --animation "run" --animate-in-place

# 步骤 5：启动多角度渲染工作台，导出 2D 俯视角 RPG 游戏 8 方向奔跑序列帧图集
python3 skills/asynx-3d/scripts/asynx.py ui render-sequence --model-file "generated-model3d/task_rig_003/animated.glb"

# 步骤 6：生成角色施法音效与背景音乐 (asynx-audio)
python3 skills/asynx-audio/scripts/asynx.py sfx --prompt "火球术吟唱与发射爆破音效"
python3 skills/asynx-audio/scripts/asynx.py music --prompt "神秘森林探索背景配乐，循环节奏" --duration 60 --instrumental
```

---

## 任务与批次管理命令 (Task & Batch Management)

所有 Skill 均支持统一的异步任务轮询、本地最近产物索引与批次管理：

```bash
# 1. 任务进度查询与等待
python3 <skill-dir>/scripts/asynx.py task list                   # 列出本地任务记录
python3 <skill-dir>/scripts/asynx.py task status "<task-id>" --refresh  # 刷新状态并自动下载完成产物
python3 <skill-dir>/scripts/asynx.py wait "<task-id>"           # 阻塞等待任务完成并下载

# 2. 本地快速索引已生成产物
python3 <skill-dir>/scripts/asynx.py recent --latest            # 查看本机最近一次成功产物
python3 <skill-dir>/scripts/asynx.py recent --query "赛博朋克"    # 按关键词检索已产出资源

# 3. 批量生成 (Batch)
python3 <skill-dir>/scripts/asynx.py batch create --prompt "不同风格的像素武器" --total 10
python3 <skill-dir>/scripts/asynx.py batch status "<batch-id>" --refresh
python3 <skill-dir>/scripts/asynx.py batch wait "<batch-id>"    # 推进并等待批次全部完成
python3 <skill-dir>/scripts/asynx.py batch pause "<batch-id>"   # 暂停批次
python3 <skill-dir>/scripts/asynx.py batch resume "<batch-id>"  # 恢复批次
```

---

## 仓库结构 (Monorepo Layout)

```text
.
├── skills/                      # 独立打包分发的 4 个 Asset Skill 目录
│   ├── asynx-image-pro/         # 图像创作技能 (SKILL.md, scripts/, references/)
│   ├── asynx-audio/             # 音频创作技能 (SKILL.md, scripts/, references/)
│   ├── asynx-video/             # 视频创作技能 (SKILL.md, scripts/, references/)
│   └── asynx-3d/                # 3D 创作技能 (SKILL.md, scripts/, references/)
├── shared/                      # 共享基础库
│   ├── asxlib/                  # 网关客户端、任务轮询、SQLite 状态账本、配置与 UI 服务器
│   ├── scripts/                 # CLI 统一入口 (asynx.py) 与运行时锁 (asx_runtime.py)
│   └── vendor/                  # 内置离线依赖 (Pillow 等)
├── src/                         # 模态特定核心源码
│   ├── image/                   # 图像任务调度与本地图片处理模块
│   ├── audio/                   # 音频任务调度与本地音频剪辑模块
│   ├── video/                   # 视频任务调度与本地抽帧图集模块
│   ├── model3d/                 # 3D 模型分析与离线 PBR/GLB 校验模块
│   ├── ui/                      # 本地 HTML5 / WebGL 轻量工作台静态资源
│   └── build_skills.py          # 构建打包器：分发并组装独立的 Skill 目录
├── dist/                        # 预打包发布的独立 Skill 压缩包 (.zip) 及清单
├── docs/                        # 架构设计记录 (ADR) 与领域模型
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
