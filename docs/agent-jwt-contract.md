# Agent JWT 客户端契约

本仓库的 Skill 通过 SageIn Agent 授权获取访问 Asynx 的短期 JWT。本文只描述客户端依赖的公开接口行为。

## 前置条件

用户通过 `sagein-community` 完成设备授权，并勾选 `skill_jwt` 权限。授权成功后，本机保存有 `sagein_pat_...` 的凭据文件：

- 默认路径：`~/.config/sagein-community/credentials/<sha256(origin)[:16]>.json`
- 覆盖：`SAGEIN_COMMUNITY_CREDENTIAL_FILE`
- 站点 origin：默认 `https://sagein.ai`，可用 `SAGEIN_COMMUNITY_ORIGIN` 覆盖
- 凭据文件中的 `access_token` 字段即 PAT。

## 接口

### `GET {origin}/api/agent/asynx-config`（公开）

```json
{ "code": 0, "data": {
  "baseUrl": "…", "tokenHeader": "X-Asynx-Partner-Token",
  "issuer": "…", "audience": "…", "kid": "…", "tokenExpiresIn": 86400 } }
```

### `POST {origin}/api/agent/jwt`

- 请求头：`Authorization: Bearer <PAT>`
- 成功：返回 `data.token`（三段式 JWT）。
- `401`：PAT 无效、过期或已撤销 → 客户端报 `need_login`。
- `403`：缺少 `skill_jwt` 权限 → 客户端报 `scope_missing`。

## 客户端行为

1. 调用 Asynx 网关时，仅使用请求头 `tokenHeader`（默认 `X-Asynx-Partner-Token`）携带 JWT，不发送 `Authorization`。
2. JWT 缓存于 `~/.config/asynx/config.json`；距过期不足 60 秒，或 JWT 的 `kid`/`iss`/`aud` 与 asynx-config 不一致时，用 PAT 重新换取。
3. 网关返回 `401 invalid_partner_token` 时，刷新并重试一次。
4. PAT 只发送给 SageIn origin，不发送给 Asynx 网关，也不进入日志、对话或命令参数。
5. `ASYNX_PARTNER_TOKEN` 环境变量可直接提供 JWT（优先于上述流程，不刷新）。
