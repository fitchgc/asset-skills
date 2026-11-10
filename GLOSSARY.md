# Agent Asset Skills

面向 AI Agent 的多模态与游戏创作资产（图像、音频、视频、3D）生成、编辑、后处理与管线管理的同构技能集合（`asynx-image`、`asynx-audio`、`asynx-video`、`asynx-3d`）。

## Language

**Asset（创作资产）**:
用于游戏或多媒体项目中的可交付多模态内容产物，涵盖图像（Image）、音频（Audio）、视频（Video）、序列帧图集（Frame Sequence）与 3D 模型（3D）。
_Avoid_: 资源文件、附件、通用数字资产

**Modality（资产模态）**:
创作资产所属的媒介分类（`image`、`audio`、`video`、`3d`），决定对应的资产技能、默认任务视图过滤以及本地后处理算子。
_Avoid_: 资产类型、文件格式、频道

**Asset Skill（资产技能）**:
面向 AI Agent 的特定资产模态创作与处理能力单元（`asynx-image`、`asynx-audio`、`asynx-video`、`asynx-3d`），包含声明式工作流引导、自包含 CLI 脚本与按需启动的本地交互工作台。
_Avoid_: 插件、子代理、微服务

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
