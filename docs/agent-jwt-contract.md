# Agent JWT 客户端契约

本仓库的 Skill 通过 Plaiers 社区的凭据桥接 `get-skill-jwt.mjs` 获取访问 Asynx 的短期 JWT。本文只描述客户端依赖的接口和本地文件。JWT 和 `skill_jwt` 只是访问凭据，不代表用户同意付费。

## 本机凭据上下文

Plaiers 安装器从注册表按固定版本取得本 Skill 后，在包根目录（`SKILL.md` 同级）写入 `.plaiers-context.json`。这个文件不属于包内容，不参与完整性校验：

```json
{
  "schema_version": 1,
  "origin": "https://dev.plaiers.ai",
  "agent_id": "codex-desktop-1",
  "jwt_command": ["node", "/abs/plaiers-community/scripts/get-skill-jwt.mjs",
    "--agent-id", "codex-desktop-1", "--origin", "https://dev.plaiers.ai",
    "--config-dir", "/abs/.config/sagein-community"],
  "skill_id": "plaiers/asynx-image-pro",
  "skill_version": "<64 位 SHA-256 锁定版本>"
}
```

- `jwt_command`：安装时记录的完整 argv。前两项是可执行文件和脚本，后面只允许成对出现的 `--agent-id`、`--origin`、`--config-dir`、`--credential-file`、`--account-id`。`--origin` 必须等于 `origin`，必须有 `--agent-id`。
- `skill_id` / `skill_version`：安装器记录的技能库 ID 与锁定版本，仅供 `update status` 查询技能库（见 ADR-0007），不影响取 JWT。缺失时 `update status` 返回 `not_registry_install`。
- 上下文里不得有 PAT。出现 `sagein_pat_` / `cg_pat_` 字样时，整个上下文被拒绝。
- `PLAIERS_SKILL_CONTEXT` 可指向另一份上下文文件，供测试和开发使用，格式和校验相同。
- 一个安装目录只属于一个身份（站点 + Agent + 凭据目录 + 凭据文件 + 账号）。Plaiers 安装器会拒绝为另一身份重装同一目录，原上下文保持不变（PR #10 P1）；不同 Agent 或账号各自用自己的 `--skills-dir` 安装。Skill 只按本目录的上下文取 JWT，不会在多个身份之间选择或切换。
- 找不到上下文或上下文无效时返回 `need_login`（`details.reason_code` 为 `missing_context` / `invalid_context`），并提示经 `search_skills` / `get_skill` 安装。没有任何回退：不扫描 `bindings/`，不读 `credentials/`，不读 `SAGEIN_COMMUNITY_*` 环境变量，没有默认 origin。

## 取得 JWT

1. 不经 shell 直接执行 `jwt_command`（路径含空格也不受影响），继承当前环境变量。stdout 只含 JWT。
   - 桥接按站点 + Agent + 账号 + PAT 分别缓存，并核对 JWT 的 `sub` 与绑定账号。Skill 自己不缓存 JWT，`~/.config/asynx/config.json` 不保存 JWT、网关配置或 origin。
   - 桥接退出码非 0 时：stderr 含 `skill_jwt` 报 `scope_missing`，其余报 `need_login`（`reason_code: bridge_failed`，`details.bridge_error` 是脱敏后的 stderr）。另有 `bridge_unavailable`、`bridge_timeout`。
2. `GET {origin}/api/agent/asynx-config`（公开）读取网关参数：

   ```json
   { "code": 0, "data": {
     "baseUrl": "…", "tokenHeader": "X-Asynx-Partner-Token",
     "issuer": "…", "audience": "…", "kid": "…", "tokenExpiresIn": 86400 } }
   ```

3. 校验 JWT：`kid` / `iss` / `aud` 与 asynx-config 一致，距 `exp` 超过 60 秒。不通过时报 `jwt_invalid`（`reason_code` 为 `kid_mismatch` 等），不改用其他凭据。

## 身份与本地任务状态

PR #10 P1（2026-11-20）：云端任务、批次、幂等键和积分确认属于一个身份，不随操作系统用户共用。详见 ADR-0008。

- 身份 = 站点 origin + Agent + 账号。账号取上下文里的 `--account-id`；没有固定时取桥接签发的 JWT 的 `sub`。Skill 不读取 `bindings/`，也不记住上一次的账号。
- 每个身份有自己的任务账本，账本内记录归属，每次打开都核对。归属不同返回 `ledger_owner_mismatch`；没有归属记录但已有云端任务返回 `ledger_owner_unknown`。`ASYNX_STATE_PATH` 指向哪里都一样核对。
- 一个进程内的会话只代表一个账号。桥接后来返回另一个账号的 JWT 时报 `jwt_invalid`（`reason_code: account_changed`）；与固定账号不一致时报 `account_mismatch`。
- 积分确认连同身份、网关和请求摘要一起记在账本里。任何提交（首次、`task recover`、`batch poll`）都要先核对这条记录：没有记录返回 `charge_consent_missing`，身份、网关或请求不一致返回 `charge_consent_mismatch`，都不提交。恢复沿用原幂等键，不重新报价。
- 已提交的任务只在原网关上查询、取消、下载；站点换了网关后返回 `task_gateway_mismatch`。
- 无法确认账号时（没有上下文，或桥接失败），只使用未绑定身份的本机账本做纯本地处理，不读取任何身份的任务。
- 失败请求日志和积分确认日志与账本放在同一身份目录下。Pillow 共享运行环境不含身份信息，仍按用户和平台共用。

## 调用网关

1. 只用请求头 `tokenHeader`（默认 `X-Asynx-Partner-Token`）携带 JWT，不发送 `Authorization`。
2. 同一进程内复用 JWT；临近过期时重新执行桥接。
3. 网关返回 `401 invalid_partner_token` 时，重新执行桥接一次。拿到新 JWT 就重试一次；拿到的还是同一个 JWT，报 `need_login`（`reason_code: gateway_rejected_jwt`），不再重试。
4. Python 代码不读取 PAT。PAT 不进入日志、错误、请求日志、对话或命令参数。
5. 不接受直接注入的 JWT 或网关地址，详见下文「无界面 CI」。

## 无界面 CI

PR #10 Q6（2026-11-20 确认）：交互使用和无界面 CI 一样，只经 `get-skill-jwt.mjs` 取得 JWT。`ASYNX_PARTNER_TOKEN`（直接注入 JWT）和 `ASYNX_BASE_URL`（改写网关）已移除；只要设置了其中任何一个，需要网关的命令都返回 `legacy_credentials_removed`（exit 2，`details.variables` 列出变量名），既不使用它们，也不悄悄忽略后改用上下文。网关地址只取自站点的 `asynx-config`；没有给出时返回 `asynx_config_invalid`，没有默认网关。

CI 做法：

1. 把社区凭据写进 CI secret，运行时落到权限 0600 的临时文件。不要放进项目、日志或命令参数。
2. 用 Plaiers 安装器显式绑定站点、Agent、账号和凭据文件：

   ```bash
   node <plaiers-community>/scripts/install-capability-skill.mjs install \
     --id plaiers/asynx-image-pro --version <sha256> \
     --origin https://dev.plaiers.ai --agent-id <agent> --account-id <account> \
     --credential-file "$RUNNER_TEMP/sagein-credential" --config-dir "$RUNNER_TEMP/sagein-config"
   ```

   写入的 `.plaiers-context.json` 只记录这些参数（凭据文件的路径，不含其内容）；也可以用 `PLAIERS_SKILL_CONTEXT` 指向它。
3. 桥接不扫描全部 bindings、不回退到旧凭据、不默认正式站，也不替你选账号。参数不足或与凭据不一致时明确失败。
4. 无界面执行同样要遵守费用确认：提交前必须带上与当前报价一致的 `--confirmed-quote`（见各 Skill 的「付费确认」）。

纯本地、不调用资产服务的处理命令不需要社区凭据。
