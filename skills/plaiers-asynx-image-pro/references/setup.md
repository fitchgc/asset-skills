# 配置与维护

仅在安装、配置异常、版本管理或旧入口清理时阅读。命令中的 `<skill-dir>` 是当前 Skill 的绝对路径；Windows 可用 `py` 替代 `python3`。不要在普通生图前增加配置或更新检查。

## 配置与诊断

```bash
python3 "<skill-dir>/scripts/asynx.py" whoami
python3 "<skill-dir>/scripts/asynx.py" config status
python3 "<skill-dir>/scripts/asynx.py" doctor
python3 "<skill-dir>/scripts/asynx.py" doctor --verify
```

- 认证只经 Plaiers 社区凭据桥接：安装器在本 Skill 根目录写入 `.plaiers-context.json`，记录 `get-skill-jwt.mjs` 的完整命令（站点、Agent、凭据目录，可选账号；需 `skill_jwt` 权限）。Skill 需要时运行这条命令取得短期 JWT，自己不缓存 JWT，也不读取 PAT；`kid`/`iss`/`aud` 须与 `GET <origin>/api/agent/asynx-config` 一致，网关地址、请求头等参数也从这里读取。没有默认站点，不读取 `SAGEIN_COMMUNITY_*` 环境变量。未授权或缺少 `.plaiers-context.json` 时返回 `need_login`：经 `plaiers-community` 用 `get_skill` 安装本 Skill，并确认授权勾选了 `skill_jwt`。 `whoami` 查看剩余积分与并发。
- `config status` 查看配置来源与环境变量覆盖；需要进一步排查副本、权限等问题时用 `doctor`。两者默认不联网，不要读取配置原文输出令牌。
- 用户要求验证连接或登录状态时使用 `doctor --verify`。它只读模型目录；通过不代表已验证生图权限、积分余额或实际生成。网络错误不等于登录失效。
- 网关地址只由 `asynx-config` 下发，没有默认网关。

默认配置位置：macOS/Linux 为 `~/.config/asynx/config.json`，Windows 为 `%APPDATA%\Asynx\config.json`，只保存轮询间隔等普通设置，不保存 JWT。同一系统用户的多份 Skill 默认共用；显式 `ASYNX_CONFIG_PATH` 可指定另一份文件。

`ASYNX_PARTNER_TOKEN` 和 `ASYNX_BASE_URL` 已移除；设置后返回 `legacy_credentials_removed`，请用户取消这些变量。无界面 CI 也经桥接取得 JWT，见 `docs/agent-jwt-contract.md` 的「无界面 CI」。`ASYNX_NO_LOGIN_UI=1` 会禁用自动拉起登录页。普通终端的临时环境变量只影响该进程及子进程，桌面 Agent 不一定继承，重启后通常不保留。长期使用优先保存配置。

旧版曾按 `XDG_CONFIG_HOME` 保存配置，新版安装器和配置命令会尝试迁移到固定位置，并保留旧文件。不因发现多份文件就直接删除，按诊断显示的实际来源处理。

新提交的单任务默认等待 20 秒后首次查询，之后约每 4 秒查询一次。需要调整时，可在上述配置文件中设置 `"initial_poll_delay_seconds": 20`（0–300 秒）；环境变量 `ASYNX_INITIAL_POLL_DELAY_SECONDS` 优先。普通调用无需传参。后台提交、已有任务查询/恢复及批次推进不额外增加这段等待；提交响应已是终态时也直接返回。

## 安装与卸载

仓库地址为 [fitchgc/asset-skills](https://github.com/fitchgc/asset-skills)。以下命令只在克隆的仓库根目录运行，安装后的 Skill 包不包含安装器：

```bash
python3 install.py
python3 install.py --target both
python3 install.py --uninstall
```

支持 `--target codex|claude|both`；默认自动检测。Codex 安装到 `~/.agents/skills/plaiers-asynx-image-pro`，Claude Code 安装到 `~/.claude/skills/plaiers-asynx-image-pro`。Windows 使用用户主目录下相同相对路径。

`--skip-config` 跳过登录和验证；`--no-verify` 只跳过连接验证，不能据此宣称已可用。依赖安装在 Skill 的隔离目录中。非交互卸载需要 `--yes`，卸载保留登录会话、任务记录和图片。

其他 Agent 应使用其支持的 Skill 安装方式，不能假设本安装器能自动检测它们。

## 更新

```bash
python3 "<skill-dir>/scripts/asynx.py" update status
```

`update status`（`update check` 等同）向安装时记录的 Plaiers 站点技能库查询：返回 `installed_version`（已装的锁定版本）、`current_version`（技能库当前版本）、`withdrawn`（已装版本是否已撤回）和 `update_available`。它会联网，只在用户要求检查更新时运行；其他命令不会在后台检查更新。

有新版本时，结果里的 `update.install_command` 是 Plaiers 安装器（`install-capability-skill.mjs`）安装该锁定版本的命令。**先征得用户同意再运行**；安装器会备份旧副本，失败时自动回滚。本 Skill 不会改写自己的文件，`update apply` / `update rollback` 已移除。

已装版本被撤回时，照实告知用户；当前副本保持不变，不会被自动删除，是否替换或移除由用户决定。不是通过技能库安装的副本（如仓库 `install.py` 的本地安装）返回 `not_registry_install`，需在仓库根目录 `git pull` 后重新运行安装器。

## 旧版 asx 迁移

名称与安装目录改为 `plaiers-asynx-image-pro`。旧版的更新命令不能完成更名，必须重新安装；不要仅改目录名。

收到 `legacy_installation_notice` 时，先交付任务结果，再简短提醒。只有用户要求安装、升级或清理旧入口时，才处理以下路径：

- Codex：`~/.agents/skills/asx`、`~/.codex/skills/asx`。
- Claude Code：`~/.claude/skills/asx`。
- Windows：用户主目录下对应的相对路径。

确认目标目录中的 `SKILL.md` 和 `scripts/asynx.py` 属于 Asynx，无法确认则说明情况。等待旧版命令退出后，将目标入口移到所有 Agent 的 `skills` 扫描目录之外备份，保留本地改动；符号链接只移动链接本身，不移动目标。然后运行新版仓库安装器。

只处理安装入口，保留登录会话、任务数据库、缓存和图片，无需迁移数据。macOS/Linux 的默认数据目录包括 `~/.config/asynx`、`~/.local/state/asx` 和 `~/.cache/asx`；Windows 为 `%APPDATA%\Asynx` 和 `%LOCALAPPDATA%\Asynx`。

完成后报告新入口和备份位置；安装失败时保留备份并说明恢复方式。没有相关指令时仅提示，不移动文件，也不在普通生图前扫描旧目录。
