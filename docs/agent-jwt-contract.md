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
