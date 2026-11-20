# 其他安装方式与 CLI 脚本直接调用指南

本文档包含两部分内容：
1. **其他安装与构建方式**：针对开发者本地联动（软链接）、解压预构建包以及从源码打包。
2. **CLI 命令行直接调用指南**：脱离 Agent、直接在终端或外部脚本中使用 `python3 <skill-dir>/scripts/asynx.py` 驱动各模态生成、后处理及本地工作台的方法。

> 如需了解通过 `install.py` 快速安装并在 AI Agent 中通过自然语言对话驱动技能，请参阅主文档 [README.md](README.md)。

---

## 目录

- [一、其他安装与构建方式](#一其他安装与构建方式)
  - [1. 软链接接入（开发者本地联动）](#1-软链接接入开发者本地联动)
  - [2. 使用独立构建产物 (Dist Packages)](#2-使用独立构建产物-dist-packages)
  - [3. 源码编译与重新打包 (开发者构建)](#3-源码编译与重新打包-开发者构建)
- [二、配置与环境诊断命令](#二配置与环境诊断命令)
  - [1. 授权与短期 JWT（SageIn Agent）](#1-授权与短期-jwtsagein-agent)
  - [2. 环境变量配置](#2-环境变量配置)
  - [3. 存储路径与优先级](#3-存储路径与优先级)
  - [4. 环境自检与连通性诊断 (Doctor)](#4-环境自检与连通性诊断-doctor)
- [三、命令行使用指南 (CLI Usage)](#三命令行使用指南-cli-usage)
  - [1. 图像创作 (`plaiers-asynx-image-pro`)](#1-图像创作-plaiers-asynx-image-pro)
  - [2. 音频创作 (`plaiers-asynx-audio`)](#2-音频创作-plaiers-asynx-audio)
  - [3. 视频创作 (`plaiers-asynx-video`)](#3-视频创作-plaiers-asynx-video)
  - [4. 3D 模型与动画 (`plaiers-asynx-3d`)](#4-3d-模型与动画-plaiers-asynx-3d)
- [四、命令行跨模态自动化管线实战示例](#四命令行跨模态自动化管线实战示例)
- [五、任务与批次管理命令 (Task & Batch Management)](#五任务与批次管理命令-task--batch-management)

---

## 一、其他安装与构建方式

### 1. 软链接接入（开发者本地联动）

通过软链接将仓库内的技能接入当前使用的 AI Agent 运行时，修改仓库代码即可实时生效，无需重复安装：

#### 1.1 Claude Code
```bash
# 创建 Claude 技能目录
mkdir -p ~/.claude/skills

# 软链接所有技能
for skill in plaiers-asynx-image-pro plaiers-asynx-audio plaiers-asynx-video plaiers-asynx-3d; do
  ln -s "$(pwd)/skills/$skill" ~/.claude/skills/$skill
done
```

#### 1.2 Codex / Pi
```bash
# 创建 Agent 技能目录
mkdir -p ~/.agents/skills

# 软链接所有技能
for skill in plaiers-asynx-image-pro plaiers-asynx-audio plaiers-asynx-video plaiers-asynx-3d; do
  ln -s "$(pwd)/skills/$skill" ~/.agents/skills/$skill
done
```

*(Windows 环境可在对应用户的 `%USERPROFILE%\.claude\skills` 或 `%USERPROFILE%\.agents\skills` 目录下创建目录符号链接或解压压缩包)*

---

### 2. 使用独立构建产物 (Dist Packages)

可以直接下载或解压 `dist/` 目录中打包好的独立归档：
- `dist/plaiers-asynx-image-pro.zip`
- `dist/plaiers-asynx-audio.zip`
- `dist/plaiers-asynx-video.zip`
- `dist/plaiers-asynx-3d.zip`

解压至任意 Agent 的 `skills/` 目录即可，每个压缩包内部已包含所有必要运行时文件。

---

### 3. 源码编译与重新打包 (开发者构建)

若修改了 `shared/` 或 `src/` 中的核心逻辑，可运行构建脚本重新生成各个 Skill 或打包为 zip：

```bash
# 重新构建所有技能到 skills/ 目录
python3 src/build_skills.py --skill all

# 构建并打包到 dist/ 目录 (生成 zip 压缩包与 sha256 清单 json)
python3 src/build_skills.py --skill all --package

# 生成 Plaiers 技能库发布条目（dist/registry/<slug>.json 与 size-report.md），只做校验，不发布
python3 -S src/registry_entries.py
```

构建会检查每个包：最多 200 个文件、不超过 4 MiB、全部为 UTF-8 文本，否则直接失败。发布条目还会按 Plaiers 发布器的规则校验，并扫描疑似凭据。真正发布只在 dev 进行，由运维执行 `manage-skill-registry.mjs publish`。

---

## 二、配置与环境诊断命令

各 Skill 共用同一套本机全局配置与本地状态账本，只需配置一次即可在全部 4 个技能中生效。

### 1. 授权与短期 JWT（SageIn Agent）

云端生成使用短期 JWT，只经 Plaiers 社区凭据桥接取得：安装器在本 Skill 根目录写入 `.plaiers-context.json`，记录 `get-skill-jwt.mjs` 的完整命令，Skill 每次需要时不经 shell 运行这条命令。Skill 不读取 PAT，自己不缓存 JWT。网关地址、请求头、`kid`/`iss`/`aud` 来自 `GET <origin>/api/agent/asynx-config`。详见 [docs/agent-jwt-contract.md](docs/agent-jwt-contract.md)。

```bash
# 查看剩余积分与并发（首次调用会自动换取 JWT）
python3 skills/plaiers-asynx-image-pro/scripts/asynx.py whoami
```

- 站点、Agent、账号全部由上下文决定。没有默认站点，不读取 `SAGEIN_COMMUNITY_*` 环境变量，不扫描授权目录。
- 网关返回 401 时重新运行桥接一次；拿到的仍是同一个 JWT 就报 `need_login`，不再重试。
- 缺少或无效的上下文、桥接失败都返回 `need_login`，缺少 `skill_jwt` 权限返回 `scope_missing`。
- JWT 只是访问凭据，不代表用户同意付费。
- 本地离线处理命令无需授权。

### 2. 环境变量配置

`ASYNX_PARTNER_TOKEN`（直接注入 JWT）和 `ASYNX_BASE_URL`（改写网关）已移除：设置后，需要网关的命令返回 `legacy_credentials_removed`。CI 同样经凭据桥接取得 JWT，做法见 [docs/agent-jwt-contract.md](docs/agent-jwt-contract.md#无界面-ci)。

```bash
# 可选：自定义凭据上下文路径（默认是 Skill 根目录的 .plaiers-context.json）
export PLAIERS_SKILL_CONTEXT="/path/to/.plaiers-context.json"

# 可选：自定义配置文件存储路径
export ASYNX_CONFIG_PATH="~/.config/asynx/config.json"

# 可选：不自动打开浏览器（UI 工作台）
export ASYNX_NO_OPEN_BROWSER=1
```

### 3. 存储路径与优先级

- **默认配置文件存储位置**：
  - macOS / Linux：`~/.config/asynx/config.json`
  - Windows：`%APPDATA%\Asynx\config.json`
- **本地任务账本存储位置**（每个身份一份，身份 = 站点 + Agent + 账号）：
  - macOS / Linux：`~/.local/state/asx/identities/<身份摘要>/state.db`
  - Windows：`%LOCALAPPDATA%\Asynx\identities\<身份摘要>\state.db`
  - 没有凭据上下文的纯本地处理使用未绑定账本 `~/.local/state/asx/state.db`（Windows：`%LOCALAPPDATA%\Asynx\state.db`）。旧版本的云端任务也留在这里，不会被任何身份自动读取。
  - 账本内记录归属。`ASYNX_STATE_PATH` 指向其他身份的账本时返回 `ledger_owner_mismatch`；指向没有归属记录但已有云端任务的账本时返回 `ledger_owner_unknown`。
  - `ledger status` 查看当前身份、账本位置和未绑定的旧任务；`ledger migrate [--source <路径>] --confirm-ownership` 在用户确认归属后把旧账本复制进当前身份的空账本，来源文件保留，从未提交成功的任务不会被重新提交。
- **凭据来源**：只有凭据上下文（`.plaiers-context.json`）记录的桥接命令；网关地址来自站点的 `asynx-config`。`config.json` 只保存轮询间隔等普通设置，不保存 JWT。

### 4. 环境自检与连通性诊断 (Doctor)

配置完成后，使用 `doctor` 命令诊断当前系统环境与依赖是否完备：

```bash
# 离线检查配置文件、权限、可选运行环境（Pillow，未准备时为 not_prepared）与 ffmpeg 工具链
python3 skills/plaiers-asynx-image-pro/scripts/asynx.py doctor

# 联网验证授权/JWT 与网关连通性
python3 skills/plaiers-asynx-image-pro/scripts/asynx.py doctor --verify

# 查看当前配置状态及授权状态
python3 skills/plaiers-asynx-image-pro/scripts/asynx.py config status
```

#### 可选运行环境（Pillow）

生成、查询、下载不需要 Pillow。本地参考图和 Mask 校验、本地图片处理、图集拼合需要所有 Skill 共用的运行环境，按平台（`<os>-<arch>-py<主.次>`）存放在 `${XDG_DATA_HOME:-~/.local/share}/plaiers/asynx-runtime/`（Windows 为 `%LOCALAPPDATA%\plaiers\asynx-runtime\`）。它不会修改系统 Python、用户 site-packages 或 pip 配置。

```bash
# 只读查看：ready / not_prepared / invalid / unsupported
python3 skills/plaiers-asynx-image-pro/scripts/asynx.py runtime status

# 联网下载按哈希锁定的 Pillow（需用户同意）；已准备时不重复下载，校验失败的旧环境会被整体替换
python3 skills/plaiers-asynx-image-pro/scripts/asynx.py runtime prepare

# 删除当前平台的运行环境
python3 skills/plaiers-asynx-image-pro/scripts/asynx.py runtime remove
```

锁定的版本和每个平台 wheel 的 SHA-256 记录在 `shared/asxlib/runtime_lock.py`，维护者用 `python3 -S src/lock_runtime.py --version <版本>` 重新生成。

---

## 三、命令行使用指南 (CLI Usage)

所有技能均通过统一的脚本入口 `scripts/asynx.py` 驱动。命令支持单次生成、批量批次、本地免 Key 处理及浏览器交互工作台。

---

### 1. 图像创作 (`plaiers-asynx-image-pro`)

#### 远程生成与编辑
```bash
# 文生图 (Text-to-Image)
python3 skills/plaiers-asynx-image-pro/scripts/asynx.py generate --prompt "赛博朋克风格发光霓虹猫咪，8k高清" --aspect-ratio "16:9"

# 垫图参考生成 (Reference Image)
python3 skills/plaiers-asynx-image-pro/scripts/asynx.py generate --prompt "保持相同构图，切换为金色秋季枫叶背景" --reference "/path/to/cat.png"

# 图生图编辑 (Image-to-Image Edit)
python3 skills/plaiers-asynx-image-pro/scripts/asynx.py edit --prompt "将角色的衣服颜色改为暗红色铠甲" --image "/path/to/character.png"

# 使用任务 ID 延续编辑 (From Task)
python3 skills/plaiers-asynx-image-pro/scripts/asynx.py edit --prompt "添加夜幕降临的星空光效" --from-task "<task-id>"
```

#### 本地确定性图片处理 (免授权)
```bash
# 查看图片基础元数据 (尺寸、格式、色彩模式)
python3 skills/plaiers-asynx-image-pro/scripts/asynx.py image info "input.png"

# 调整尺寸并保持比例
python3 skills/plaiers-asynx-image-pro/scripts/asynx.py image resize "input.png" --output "thumb.png" --width 512

# 裁剪指定区域 (x1, y1, x2, y2)
python3 skills/plaiers-asynx-image-pro/scripts/asynx.py image crop "input.png" --output "cropped.png" --box 0,0,256,256

# 九宫格/网格切图 (用于游戏瓦片地图或图集切分)
python3 skills/plaiers-asynx-image-pro/scripts/asynx.py image slice "tileset.png" --output-dir "tiles/" --rows 4 --columns 4

# 格式转换
python3 skills/plaiers-asynx-image-pro/scripts/asynx.py image convert "photo.jpg" --output "photo.webp" --format webp

# 应用 Alpha 遮罩
python3 skills/plaiers-asynx-image-pro/scripts/asynx.py image apply-mask "photo.png" --mask "mask.png" --output "cutout.png"
```

#### 本地交互工作台 (Local UI)
```bash
# 启动浏览器局部重绘圈选与标注工作台（自动提取 Mask 遮罩与修改指令）
python3 skills/plaiers-asynx-image-pro/scripts/asynx.py ui annotate --image "character.png"
```

---

### 2. 音频创作 (`plaiers-asynx-audio`)

#### 声音合成与生成
```bash
# 游戏音效合成 (SFX)
python3 skills/plaiers-asynx-audio/scripts/asynx.py sfx --prompt "宝箱开启金币散落清脆声效" --duration 2.5

# 背景音乐配乐生成 (Music)
python3 skills/plaiers-asynx-audio/scripts/asynx.py music --prompt "8-bit 像素地牢战斗芯片音乐，高燃快节奏" --duration 30 --instrumental

# 角色台词语音合成 (Speech / TTS)
python3 skills/plaiers-asynx-audio/scripts/asynx.py speech --text "勇敢的冒险者，愿圣光庇佑你的道路！" --voice "warrior" --speed 1.0

# 声音克隆/音色延续合成
python3 skills/plaiers-asynx-audio/scripts/asynx.py speech --text "前进，决不后退！" --from-task "<previous-audio-task-id>"
```

#### 本地确定性音频处理 (免授权)
```bash
# 查看音频元数据 (编码格式、采样率、声道数、比特率、时长)
python3 skills/plaiers-asynx-audio/scripts/asynx.py audio info "sound.wav"

# 格式转换 (wav, mp3, ogg, m4a, flac)
python3 skills/plaiers-asynx-audio/scripts/asynx.py audio convert "sound.wav" --output "sound.ogg"

# 音频剪辑修剪 (精确截取起止点)
python3 skills/plaiers-asynx-audio/scripts/asynx.py audio trim "track.mp3" --output "clip.mp3" --start 5.0 --duration 10.0

# 淡入淡出包络处理
python3 skills/plaiers-asynx-audio/scripts/asynx.py audio fade "bgm.mp3" --output "bgm_fade.mp3" --fade-in 1.0 --fade-out 2.0

# 广播级响度标准化 (EBU R128 标准)
python3 skills/plaiers-asynx-audio/scripts/asynx.py audio normalize "dialogue.mp3" --output "dialogue_norm.mp3" --target-lufs -14.0

# 多个音频文件拼接合并
python3 skills/plaiers-asynx-audio/scripts/asynx.py audio concat "intro.mp3" "main_loop.mp3" "outro.mp3" --output "full_ost.mp3"
```

---

### 3. 视频创作 (`plaiers-asynx-video`)

#### 视频生成与编辑
```bash
# 文生视频 (Text-to-Video)
python3 skills/plaiers-asynx-video/scripts/asynx.py generate --prompt "太空飞船穿梭于流星群，电影级景深" --duration 5.0 --aspect-ratio "16:9"

# 本地图生视频 (Image-to-Video)
python3 skills/plaiers-asynx-video/scripts/asynx.py generate --prompt "角色转身向镜头微笑打招呼" --image "/path/to/character.png"

# 跨模态图生视频 (直接使用 plaiers-asynx-image-pro 产出的任务 ID)
python3 skills/plaiers-asynx-video/scripts/asynx.py generate --prompt "镜头 360 度环绕人物旋转" --from-task "<image-task-id>"

# 首末双关键帧控制生成
python3 skills/plaiers-asynx-video/scripts/asynx.py generate --prompt "城堡从白昼平滑演变为夜晚灯火辉煌" --image "day.png" --end-image "night.png"

# 视频语义扩展与编辑
python3 skills/plaiers-asynx-video/scripts/asynx.py edit --prompt "将环境变换为大雪纷飞的冬日场景" --video "street.mp4"
```

#### 本地确定性视频处理 (免授权)
```bash
# 查看视频参数 (分辨率、帧率、时长、视频编码、音频编码)
python3 skills/plaiers-asynx-video/scripts/asynx.py video info "clip.mp4"

# 视频精准片段修剪
python3 skills/plaiers-asynx-video/scripts/asynx.py video trim "clip.mp4" --output "short.mp4" --start 1.0 --duration 4.0

# 视频离线抽帧为图片序列
python3 skills/plaiers-asynx-video/scripts/asynx.py video extract-frames "action.mp4" --output-dir "frames/" --fps 12

# 抽帧并打包为 2D 游戏动画序列帧图集 (生成 atlas.png 精灵图大图与 manifest.json 元数据清单)
python3 skills/plaiers-asynx-video/scripts/asynx.py video spritesheet "action.mp4" --output-dir "spritesheet/" --fps 10 --width 128 --height 128

# 视频转 GIF 动图
python3 skills/plaiers-asynx-video/scripts/asynx.py video convert "clip.mp4" --output "preview.gif"

# 音视频轨道合成 (Mux)
python3 skills/plaiers-asynx-video/scripts/asynx.py video mux --video "silent_clip.mp4" --audio "bgm.mp3" --output "final_video.mp4"
```

#### 本地交互工作台 (Local UI)
```bash
# 启动视频时间轴抽帧、去背与精灵图集导出工作台
python3 skills/plaiers-asynx-video/scripts/asynx.py ui frame-sequence --video "character_run.mp4"

# 启动 2D 序列帧精灵图集动画回放预览器
python3 skills/plaiers-asynx-video/scripts/asynx.py ui preview-sequence --atlas "atlas.png" --manifest "manifest.json"
```

---

### 4. 3D 模型与动画 (`plaiers-asynx-3d`)

#### 3D 生成与骨骼动画管线
```bash
# 文生 3D 模型 (Text-to-3D)
python3 skills/plaiers-asynx-3d/scripts/asynx.py generate --prompt "中世纪双刃巨斧，暗黑浮雕花纹，PBR贴图"

# 本地图生 3D 模型 (Image-to-3D，支持限制面数)
python3 skills/plaiers-asynx-3d/scripts/asynx.py generate --image "/path/to/monster.png" --face-limit 15000

# 跨模态图生 3D (使用 plaiers-asynx-image-pro 生成的概念图任务 ID)
python3 skills/plaiers-asynx-3d/scripts/asynx.py generate --from-task "<image-task-id>"

# 骨骼绑定前置合规检查
python3 skills/plaiers-asynx-3d/scripts/asynx.py rig-check --from-task "<gen-task-id>"

# 本地模型 (GLB/FBX/OBJ/STL) 上传为 file_id 后绑定
python3 skills/plaiers-asynx-3d/scripts/asynx.py rig --model-file "robot.glb"

# 云端自动骨骼绑定与权重蒙皮 (Rig)
python3 skills/plaiers-asynx-3d/scripts/asynx.py rig --from-task "<gen-task-id>" --rig-type biped

# 动作重定向 (Animate：让角色执行标准动画动作)
python3 skills/plaiers-asynx-3d/scripts/asynx.py animate --from-task "<rig-task-id>" --animation "walk"

# 格式转换 (fbx|gltf|obj|usdz|stl)
python3 skills/plaiers-asynx-3d/scripts/asynx.py convert --from-task "<task-id>" --output-format fbx
```

#### 本地纯 Python 3D 模型处理 (免授权，无需 Blender)
```bash
# 查看 3D 模型结构 (顶点数、三角面数、材质槽、内嵌贴图、骨骼关节数、动画片段)
python3 skills/plaiers-asynx-3d/scripts/asynx.py model3d info "character.glb"

# 游戏规范合规性校验 (校验多边形面数上限、必须包含 PBR 贴图、骨骼/动画要求)
python3 skills/plaiers-asynx-3d/scripts/asynx.py model3d validate "character.glb" --max-faces 20000 --require-pbr --require-animations

# 直接从 GLB 中离线解包提取所有内嵌 PBR 贴图到本地目录
python3 skills/plaiers-asynx-3d/scripts/asynx.py model3d extract-textures "character.glb" --output-dir "textures/"
```

#### 本地交互工作台 (Local UI)
```bash
# 启动浏览器 WebGL 3D 检查器 (支持模型 360 度旋转、骨骼动画播放、PBR 贴图通道检查)
python3 skills/plaiers-asynx-3d/scripts/asynx.py ui viewer --model-file "character.glb"

# 启动多视角 2D 序列帧图集渲染工作台 (支持 1向/4向/8向正交与等轴测视角自动渲染并打包图集)
python3 skills/plaiers-asynx-3d/scripts/asynx.py ui render-sequence --model-file "character.glb"
```

---

## 四、命令行跨模态自动化管线实战示例

通过共享的 SQLite 状态账本，可以将多种资产模态串联成一条完整的端到端游戏资产生产管线：

```bash
# 步骤 1：生成游戏主角概念图 (plaiers-asynx-image-pro) -> 输出任务 ID: task_img_001
python3 skills/plaiers-asynx-image-pro/scripts/asynx.py generate --prompt "Q版魔法师角色，站立姿态，清晰白底"

# 步骤 2：由概念图生成 3D 角色模型 (plaiers-asynx-3d) -> 输出任务 ID: task_3d_002
python3 skills/plaiers-asynx-3d/scripts/asynx.py generate --from-task "task_img_001" --face-limit 15000

# 步骤 3：为 3D 角色自动绑定人形骨骼 (plaiers-asynx-3d) -> 输出任务 ID: task_rig_003
python3 skills/plaiers-asynx-3d/scripts/asynx.py rig --from-task "task_3d_002" --rig-type biped

# 步骤 4：为绑定好的角色重定向跑步动作 (plaiers-asynx-3d) -> 产出带动画的 GLB 模型
python3 skills/plaiers-asynx-3d/scripts/asynx.py animate --from-task "task_rig_003" --animation "run"

# 步骤 5：启动多角度渲染工作台，导出 2D 俯视角 RPG 游戏 8 方向奔跑序列帧图集
python3 skills/plaiers-asynx-3d/scripts/asynx.py ui render-sequence --model-file "generated-model3d/task_rig_003/animated.glb"

# 步骤 6：生成角色施法音效与背景音乐 (plaiers-asynx-audio)
python3 skills/plaiers-asynx-audio/scripts/asynx.py sfx --prompt "火球术吟唱与发射爆破音效"
python3 skills/plaiers-asynx-audio/scripts/asynx.py music --prompt "神秘森林探索背景配乐，循环节奏" --duration 60 --instrumental
```

---

## 五、任务与批次管理命令 (Task & Batch Management)

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
