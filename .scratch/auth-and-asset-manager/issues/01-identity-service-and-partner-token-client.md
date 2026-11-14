# 01: Identity Service & Asset Gateway Dual-Protocol Client with Auto-Refresh

**What to build:** Upgrade authentication and gateway communication across all Asset Skills to use the Identity Service (`login_svr`) and Partner Token protocol, persisting `{ login_svr_url, token, refreshToken, asynx, user }` locally, enforcing the distinct HTTP header and response envelope contracts of both servers, automatically refreshing expired or rotated tokens proactively and reactively, provisioning partner identity on the Asset Gateway, and stripping partner credentials across cross-origin asset download redirects.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] User-level configuration persists `login_svr_url`, `token`, `refreshToken`, `asynx` (`baseUrl`, `tokenHeader`, `issuer`, `audience`, `kid`, `tokenExpiresIn`), and `user`, honoring `ASYNX_LOGIN_SVR_URL` (default `http://localhost:3006`) and optional headless overrides.
- [ ] Identity Service requests (`/api/user/asynx-config`, `/api/user/check-username`, `/api/user/register`, `/api/user/login`, `/api/user/refresh`, `/api/user/profile`, `/api/user/change-password`) validate HTTP `200` with numeric `code === 0` and surface structured errors for non-zero codes (`10`, `11`, `12`, `15`, `401`, `1001`–`1004`).
- [ ] Asset Gateway requests (`/v1/partner/identity`, `/v1/tasks/models`, `/v1/tasks/models/{name}/guide`, `/v1/tasks/quote`, `/v1/tasks`, `/v1/tasks/{task_id}`, `/v1/tasks/{task_id}/cancel`, `/v1/tasks/{task_id}/assets/{index}`, `/v1/files`) send exclusively `X-Asynx-Partner-Token: <token>` and never include an `Authorization` header, validating HTTP `200`/`202` and string `code === "ok"` (with direct top-level object support for `/v1/files`).
- [ ] Pre-flight token validation decodes the JWT header and payload and proactively invokes `POST /api/user/refresh` (atomically saving both the new `token` and new `refreshToken`) whenever `exp - now <= 60` seconds or `kid`, `iss`, or `aud` mismatch the active `asynx` configuration.
- [ ] Reactive token refresh catches HTTP `401` (`invalid_partner_token`) from any Asset Gateway request or task status poll, refreshes the session once via `POST /api/user/refresh`, and transparently retries the request without losing in-progress Generation Tasks.
- [ ] Asset downloads include `X-Asynx-Partner-Token` when requesting `/v1/tasks/{task_id}/assets/{index}` from the Asset Gateway and automatically strip the partner token header when following HTTP `302` redirects to a different storage origin.
- [ ] Partner identity provisioning (`GET /v1/partner/identity`) runs upon login and before task submission, surfacing quota exhaustion (`api_key.status === "quota_exhausted"`) clearly, and all existing Asset Skill test suites pass against loopback fake Identity Service and Asset Gateway servers.
