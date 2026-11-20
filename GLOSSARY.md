# Agent Asset Skills

面向 AI Agent 的多模态与游戏创作资产（图像、音频、视频、序列帧、3D）生成、编辑、后处理、管线管理与项目资产浏览的同构技能集合（`plaiers-asynx-image-pro`、`plaiers-asynx-audio`、`plaiers-asynx-video`、`plaiers-asynx-3d`、`plaiers-asynx-workbench`）。

## Language

**Asset（创作资产）**:
用于游戏或多媒体项目中的可交付多模态内容产物，涵盖图像（Image）、音频（Audio）、视频（Video）、序列帧图集（Frame Sequence）与 3D 模型（3D）。
_Avoid_: 资源文件、附件、通用数字资产

**Modality（资产模态）**:
创作资产所属的媒介分类（`image`、`audio`、`video`、`3d`），决定对应的资产技能、默认任务视图过滤以及本地后处理算子。
_Avoid_: 资产类型、文件格式、频道

**Asset Skill（资产技能）**:
面向 AI Agent 的特定资产模态创作、处理或项目资产管理能力单元（`plaiers-asynx-image-pro`、`plaiers-asynx-audio`、`plaiers-asynx-video`、`plaiers-asynx-3d`、`plaiers-asynx-workbench`），作为 Plaiers 技能库的 `kind: "capability"` 按锁定版本分发，包含声明式工作流引导与自包含 CLI 脚本。分为**核心包**和**工作台包**。

**核心包（Core Package）**:
单一模态的 Asset Skill（图片、音频、视频、3D），只含生成、查询、下载所需的纯文本文件；不含浏览器工作台、本地后处理模块、字体或第三方库。

**工作台包（Workbench Package）**:
可选安装的 `plaiers-asynx-workbench`，同时承载资产管理器、全部 Local UI Workbench 和各模态 Local Post-Processing，只打包一次。核心包在用户调用这些功能时从同一 Skills 目录加载它，并要求版本一致；未安装时返回 `workbench_not_installed`。
_Avoid_: UI 插件、扩展包

**共享运行环境（Shared Runtime）**:
按 `(系统, 架构, Python 版本)` 共享、按哈希锁定的隔离 Pillow 环境，只在用户同意后由 `runtime prepare` 准备；核心主流程不依赖它。
_Avoid_: 插件、子代理、微服务

**SageIn Agent 授权（Agent PAT → JWT）**:
用户通过 Plaiers 社区设备授权（含 `skill_jwt` 权限），本地保存长效 `sagein_pat_...`。Skill 不读取 PAT，只运行**本机凭据上下文**记录的 `get-skill-jwt.mjs` 命令取得短期 JWT（Partner Token），并从 `/api/agent/asynx-config` 获取网关接入参数。JWT 只是访问凭据，不代表付费同意。

**本机凭据上下文（`.plaiers-context.json`）**:
Plaiers 安装器写在已安装 Skill 根目录的文件，记录凭据桥接的完整 argv（`--agent-id`、`--origin`、`--config-dir`，可选 `--credential-file`、`--account-id`）。另记录技能库 ID 与锁定版本（`skill_id`、`skill_version`），供 `update status` 查询。不含 PAT，不参与包完整性校验。缺少它时 Skill 返回 `need_login`，不回退到其他凭据。
_Avoid_: 授权中心、Key 管理台

**付费确认（Charge Confirmation）**:
每次提交消耗积分的生成任务或批次之前，先向资产网关报价（`POST /v1/tasks/quote`），再取得用户对这一次请求的明确确认（`--confirmed-quote`）。授权、JWT、安装都不算付费同意。报价不可用时，只有用户明确同意，才能用 `--confirm-without-quote` 提交，这个决定会记入日志。

**积分（Credits）**:
资产服务充值得到的额度。资产服务的报价、余额与消费记录在中文里统一称“积分”。它与社区原有积分体系、玩造币相互独立，不互转，也不复用社区币账本（PR #10 Q4）。显示单位与精度以计费接口返回为准，不自行设定汇率；模型的输入/输出 token 数不等于积分。PAT、JWT 等访问凭据在中文称“令牌”或“访问凭据”，不叫积分。
_Avoid_: 自动扣费、默认同意

**Asset Gateway（资产网关）**:
提供统一鉴权、异步生成任务调度与产物分发的远端服务入口，屏蔽不同底层多模态模型的协议差异。
_Avoid_: 上游通道、第三方 API、中转站

**Generation Task（生成任务）**:
提交至资产网关的异步多模态创作或编辑请求，具有从受理、执行到完成并落盘为本地创作资产的完整生命周期，支持通过任务 ID 跨模态引用产物（如图生视频、图生 3D）。
_Avoid_: 作业、请求单、推理会话

**Batch（批次任务）**:
由本地调度器管理的多个生成任务集合，支持分批并发提交、断点暂停、恢复与汇总交付。
_Avoid_: 队列、任务组、并发池

**Local Post-Processing（本地后处理）**:
无需调用资产网关即可在本地对创作资产执行的确定性变换操作（如裁剪、切图、去底抠图、音频修剪、视频抽帧、3D 网格检查与序列帧导出等）。
_Avoid_: 离线编辑、二次生成

**Local UI Workbench（本地交互工作台）**:
由 Skill CLI 按需在本地临时启动的轻量 Web 界面服务，用于在浏览器中完成必须依赖视觉交互或 WebGL/Canvas 渲染的操作（如图片局部重绘批注、视频逐帧提取去背、3D 预览与多方向序列帧渲染），完成后将结果回传给 CLI 与 Agent。
_Avoid_: 管理后台、常驻前端服务

**Region Edit Annotation（局部重绘批注）**:
在源图上通过指向箭头、矩形框选或套索圈选绘制的视觉标记及对应的区域修改说明，导出为透明重绘遮罩（Mask）、批注定位预览图（Annotation Preview）与结构化修改指令。
_Avoid_: 涂鸦、普通水印

**Frame Sequence Atlas（序列帧图集）**:
由视频抽帧或 3D 动画多视角渲染生成的 2D 游戏动画资产包，包含拼合后的精灵大图（Atlas PNG）与描述各帧坐标及动画元数据的清单文件（Manifest JSON）。
_Avoid_: 动图、连拍图

**Asset Manager（资产管理器）**:
汇总展示当前项目内全部创作资产（图片、音频、序列帧图集、视频、3D 模型）的跨模态管理技能与交互工作台，区分由资产技能产出的受管资产与外部本地资产，并可直接针对选中资产发起跨模态衍生创作或编辑。
_Avoid_: 文件浏览器、资源文件夹、图库

**Asset Lineage（资产血缘）**:
创作资产之间通过云端生成/编辑任务或本地后处理建立的有向父子派生关系，用于追溯任一资产是由哪些源资产经过何种操作衍生而来，以及它进一步派生出了哪些下游资产。
_Avoid_: 历史版本、引用计数、文件树
