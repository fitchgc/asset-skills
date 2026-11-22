---
name: plaiers-asynx-image-pro
description: 通过 Asynx 生成、参考图创作和编辑图片，管理批量任务、进度与中断恢复；支持浏览器交互式局部重绘、涂鸦批注与图像处理工作台；也支持本地缩放、裁剪、切图和格式转换。用户要求时可接入自定义工作流，不用于视频。
---

# Asynx 图片

使用本 Skill 的 `scripts/asynx.py` 完成图片操作。下文 `<skill-dir>` 替换为当前 Skill 的绝对路径；需要 Python 3.10+，Windows 可用 `py` 替代 `python3`。普通生成和明确的换色、换背景、角色修改可按本页直接提交，无需读取提示词参考、检查版本或扫描目录。

## 项目资产交接

先与用户确定项目根目录，把生成结果的输出目录设在其中。用本 Skill 的 `project budget --root "<项目根目录>" --currency USD --limit <金额>` 设置本身份的项目预算，省略上限参数可查逐任务预占与累计实扣。下载后向用户提供 `python3 "<skill-dir>/scripts/asynx.py" project open --root "<项目根目录>"`，在本机查看、挑选或编辑同项目素材；已打开时可复用该项目与身份的本地窗口。共享运行环境用 `runtime status` 检查，经同意后用 `runtime prepare` 准备。工作台按需打开。

游戏美术先列交接清单：概念参考、干净背景、独立透明物件、代码绘制元素。逐项记录用途、尺寸、透明度、父素材或复用路径。先报价并审核概念稿，再分别报价需要生成的运行素材；带 HUD 或主体的概念稿保留作参考，背景和独立物件分别导出。复用和代码绘制标明新增生成费用为零。

## 积分消耗确认（付费确认）

生成任务会消耗**资产积分**（资产服务的充值额度；与社区原有积分体系、玩造币相互独立，不互转）。每次提交生成任务或批次之前都要先让用户确认预计消耗。**授权 `skill_jwt`、登录或安装本 Skill 都不代表用户同意消耗积分。** PAT、JWT 是访问凭据（令牌），不是积分。

1. 第一次运行生成命令时，不要带 `--confirmed-quote`。CLI 只报价，不提交任务，并返回 `confirmation_required`。`details` 里有本次任务数（`units`）、预计消耗（`total_amount`，单位 `currency`）和剩余积分（`budget`）。数值和单位按接口返回原样展示，不自行换算，也不要把模型的输入/输出 token 数当成积分。
2. 把模型、数量、预计消耗和剩余积分告诉用户，请用户确认**这一次**请求或批次。
3. 用户确认后，带上 `details.confirm_args`（如 `--confirmed-quote USD:0.12`）重新运行同一条命令。CLI 会重新报价。新报价高于用户确认的数值、单位变化或报价过期时，会再次返回 `confirmation_required`，这时要重新征求用户确认，不能自己改大金额。
4. 报价不可用时返回 `quote_unavailable`，不会提交任务。只有用户明确同意在没有报价的情况下提交，才可以加 `--confirm-without-quote`；这个决定会写入请求日志。
5. 报价为 0 时无需确认。

提交后如果出现失败、超时或网络错误，CLI 不会自动重新提交。按错误里的 `next_action_args`（`task recover <local_id>`）恢复，它会沿用同一幂等键。超时先查原任务，不重新提交；下载重试只会重新下载，不会重新生成或再扣积分。之前的确认不能用于其他请求。

任务记录、批次和积分确认只属于提交它们的身份（站点 + Agent + 账号），每个身份有自己的任务账本。`task recover`、`batch poll` 只会重新提交当前身份自己确认过的那一份请求。遇到 `ledger_owner_mismatch`、`ledger_owner_unknown`、`charge_consent_missing`、`charge_consent_mismatch` 或 `task_gateway_mismatch` 时不要换账本、换身份或绕过：把情况告诉用户，需要时重新发起请求并按新报价确认。旧版本留下的任务用 `ledger status` 查看，用户确认归属后才用 `ledger migrate --confirm-ownership` 迁入。

## 生成与编辑

```bash
python3 "<skill-dir>/scripts/asynx.py" generate --prompt "<文字描述>"
python3 "<skill-dir>/scripts/asynx.py" generate --prompt "<文字描述>" --reference "/absolute/path/reference.png"
python3 "<skill-dir>/scripts/asynx.py" edit --prompt "<修改要求>" --image "/absolute/path/input.png"
python3 "<skill-dir>/scripts/asynx.py" edit --prompt "<修改要求>" --from-task "<task-id>"
```

- 新图使用 `generate`，修改已有图片使用 `edit`。图片通过 `--reference` 或 `--image` 独立传入，不把路径当作 Prompt；多张输入重复对应参数。
- 用户给定的 Prompt 保留原意，普通请求直接提交；只有需要整理复杂创作意图或用户要求优化时才补充结构，不擅自改变主体、风格、构图和文字。
- 未指定模型、尺寸或质量时省略对应参数，使用脚本默认值；用户指定模型时直接传入 `--model`，无需先查模型目录。
- 新图按用户指定的比例或海报、壁纸等用途传 `--aspect-ratio`，其余使用默认值。编辑本地图片默认沿用原图比例；改变画幅时显式传入。多图比例不一致或输入是 URL 时，按提示明确输出比例。
- 原图直接交给脚本处理，不额外压缩或编码。保留原始编码、Mask 和特殊输入要求按需读取[输入说明](references/operations.md#输入图片)。
- 单任务默认等待、下载并展示结果。用户要求后台运行或稍后查询时才加 `--detach`；它只表示已受理。若先分离提交但用户仍要求本轮完成，继续等待原任务，不重新生成。
- 终端首次调用优先等待 30 秒（支持时设置 `yield_time_ms: 30000`）；返回运行中的会话后，继续等待同一进程，每次 30–60 秒，以宿主限制为准。脚本负责轮询和下载，不另起命令重复监控。
- 批量或追加使用批次命令，按下面的按需入口读取批次说明；不要套用单任务的等待规则。

## 交互工作台 (Local UI Workbench)

> 本节命令由可选的**本地工作台包**（`plaiers-asynx-workbench`，技能库 ID `plaiers/asynx-workbench`）提供，本 Skill 只含生成、查询与下载。未安装时命令返回 `workbench_not_installed`，生成主流程不受影响；用户需要时，经同意后用 `get_skill` 获取并用 Plaiers 安装器装成同一版本（版本不一致返回 `workbench_version_mismatch`）。

当用户要求“编辑刚才的图片”、“修改特定区域”、“画一下 Mask”、“调起涂鸦批注/编辑界面”或进行可视化选区与指向修改时，**直接启动本地交互式编辑工作台**：

```bash
# 局部重绘与视觉批注工作台（支持涂鸦画 Mask、圈选、框选、红箭头指向与多点文字说明）
python3 "<skill-dir>/scripts/asynx.py" edit --ui --from-task "<task-id>" --prompt "<初步修改意图>"
python3 "<skill-dir>/scripts/asynx.py" ui region-edit --image "/absolute/path/input.png"

# 手绘涂鸦与视觉批注工作台
python3 "<skill-dir>/scripts/asynx.py" ui annotate --image "/absolute/path/input.png"

# 交互式本地图片工具（裁切、缩放、格式等）
python3 "<skill-dir>/scripts/asynx.py" ui tools --image "/absolute/path/input.png"
```

局部重绘提交前，页面会报价并请用户确认这次的积分消耗；用户取消则不提交。

- **稳定端口与会话**：默认优先使用稳定的默认端口 `51888`，页面刷新或断线重连保持可用。
- **操作闭环**：工作台内置涂鸦画笔（Brush）、指向箭头（Arrow）、框选、圈选工具。用户在网页点击“生成”后，本地服务会自动执行重绘并下载结果交付给 Agent。
- Agent 调用时若宿主终端不支持自动弹出浏览器，向用户展示生成的本地 URL，让用户点击进入。

## 选择图片与查询任务

“刚才那张”优先对应当前对话中已明确的文件或任务。上下文不足时查询本地索引，不扫描磁盘或用远端历史猜测：

```bash
python3 "<skill-dir>/scripts/asynx.py" recent --query "<描述或任务 ID>"
python3 "<skill-dir>/scripts/asynx.py" recent --latest
python3 "<skill-dir>/scripts/asynx.py" asset list "<task-id>"
```

`recent --latest` 是同一用户共享记录中的最近结果，不保证属于当前会话；核对描述后再使用，有歧义才询问。一个任务有多张结果时，`--from-task` 会使用该任务的多张本地结果；用户只选一张时，传该图片的 `--image` 路径。

```bash
python3 "<skill-dir>/scripts/asynx.py" task list
python3 "<skill-dir>/scripts/asynx.py" task status "<task-id>" --refresh
python3 "<skill-dir>/scripts/asynx.py" wait "<task-id>"
```

查询用 `task status --refresh`，等结果用 `wait`，均可传本地或远端任务 ID；完成后会下载原结果，不重新生图。提交中断的恢复和取消见[任务恢复](references/operations.md#任务恢复与取消)，不要为查询进度执行恢复。

## 按需读取

只读与当前请求有关的章节，普通生图无需读取：

| 请求 | 参考 |
| --- | --- |
| 批量生成、追加、暂停或继续批次 | [批次操作](references/operations.md#批次) |
| 交互式编辑、局部重绘、涂鸦批注或网页工作台 | [交互工作台](references/operations.md#交互工作台) |
| 缩放、裁剪、切图、格式转换或应用已有 Mask | [本地图片工具](references/operations.md#本地图片工具)，不需要授权 |
| 输入图片限制、Mask 编辑或特殊参数 | [输入与参数](references/operations.md#输入图片) |
| 恢复中断、取消任务、找回下载文件 | [任务恢复](references/operations.md#任务恢复与取消) |
| 配置、连接异常、安装、升级或旧入口清理 | [配置与维护](references/setup.md) |
| 用户要求模型推荐、Prompt 优化，或需梳理多图关系、复杂图文布局 | [模型与提示词](references/model-prompting.md)，读取本身不要求查模型目录 |

返回 `need_login` 时，说明本机没有有效的 Plaiers Agent 授权或凭据上下文：经 `plaiers-community` 完成设备授权并用 `get_skill` 安装本 Skill，确认勾选 `skill_jwt` 权限（返回 `scope_missing` 即缺少该权限）；不在对话中索取令牌。已授权却报错时按维护参考诊断。

## 结果与边界

- 普通成功结果直接展示 `files` 中的图片或文件，Codex 桌面端用绝对路径展示；`images` 已含实际尺寸、比例和格式，无需另查。不主动追加状态查询或逐项视觉评审，不展示诊断字段；任何新的生成提交都要按“积分消耗确认”重新报价并取得用户确认。
- 用户要求检查或纠错时，针对指定问题查看图片；尺寸与格式使用结果元数据。检查后直接交付，不把文件已下载等同于内容已正确，也不未经用户同意自动重新生图。
- 进度用自然语言概括；失败说明已知原因、影响和下一步，不直接转发 JSON 或上游英文，也不猜测原因。部分失败、下载失败和 `degraded` 等影响结果的信息必须说明；降级原因未提供时，不自行断言画质变差。
- 中断等待不等于取消。已开始的任务可能继续执行和计费；恢复结果不能通过重新生图替代，不无条件重试失败任务。
- `next_action_args` 给出继续原任务的脚本参数；仅在需要继续时使用。下载失败可重试原下载，提交恢复或失败后的重新生成须符合用户要求的范围。
- 返回 `warnings` 或 `legacy_installation_notice` 时，先交付本次结果，再说明相关提示。不要主动查更新；用户要求时运行 `update status`，有新版本也须征得同意后再用 Plaiers 安装器安装。未收到升级或清理指令时不替换、移动文件。
- SageIn PAT（`sagein_pat_...`）和 JWT 不得进入对话、命令参数、Prompt、日志、项目文件或浏览器代码。通过脚本管理任务，不直接读取、修改或依赖本 Skill 的数据库及内部状态文件。

用户要求自定义工作台或工作流时，可以调用公开 API 或封装本脚本，使用自己的数据库；遵守密钥和本地状态边界即可，不额外限定技术栈或引入用户未要求的服务。

## 可选运行环境（Pillow）

生成、查询、下载不需要 Pillow。本地参考图、Mask、本地图片处理和图集拼合需要共享运行环境；缺失时命令在提交前返回 `dependency_missing`（或校验失败时返回 `runtime_invalid`），`details.prepare_command` 给出准备命令。

- `prepare` 会联网下载按哈希锁定的 Pillow，**运行前必须先征得用户同意**；不要自动运行，也不要改用 `pip install`。
- 所有 Asset Skill 共用同一份运行环境，已准备时再次 `prepare` 不会重复下载。
- `python3 scripts/asynx.py runtime status` 只读查看状态；`runtime remove` 删除当前平台的运行环境。
