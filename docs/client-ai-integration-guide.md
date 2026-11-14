# 客户端 AI 接入指南（Login Server + Asynx）

本文档面向客户端 AI（Agent / Skill / 本地脚本 / 客户端 SDK），说明如何通过 **Login Server (`login_svr`)** 完成用户登录、获取与自动刷新 JWT 令牌、同步 Asynx 接入配置，并直接携带令牌向 **Asynx** 发起图片/视频生成、状态轮询与结果下载。

---

## 1. 整体调用流程

```text
┌──────────────┐   1. 登录 / 注册 / 刷新 Token         ┌──────────────┐
│              │ ───────────────────────────────────▶ │              │
│              │   返回: token (1天)                   │ Login Server │
│              │        refreshToken (30天)           │  (login_svr) │
│  客户端 AI    │        asynx.baseUrl 等接入配置       │              │
│ (Agent/Skill)│ ◀─────────────────────────────────── └──────────────┘
│              │
│              │   2. 携带请求头:                      ┌──────────────┐
│              │      X-Asynx-Partner-Token: <token>  │    Asynx     │
│              │ ───────────────────────────────────▶ │  ({baseUrl}) │
│              │   开通身份 / 查模型 / 提交任务 / 下载  │              │
└──────────────┘ ◀─────────────────────────────────── └──────────────┘
```

1. **只需配置一个入口地址**：客户端只需知道 `LOGIN_SVR_URL`（如 `http://localhost:3006`）。
2. **登录获取凭证与 Asynx 地址**：调用 `login_svr` 登录或注册接口，同时获得：
   - `token`：1 天（`86400` 秒）有效期的 `Ed25519` (`EdDSA`) 签名 JWT，已按 Asynx 规范内置 `kid`、`iss`、`aud`、`sub`、`iat`、`exp`。
   - `refreshToken`：30 天有效期的刷新令牌。
   - `asynx`：包含 `baseUrl`（Asynx API 根地址，以 `/api` 结尾）、`tokenHeader`（固定为 `X-Asynx-Partner-Token`）、`issuer`、`audience`、`kid`、`tokenExpiresIn`。
3. **直连请求 Asynx**：客户端使用 `asynx.baseUrl` 和 `X-Asynx-Partner-Token: <token>` 请求 Asynx 接口。

---

## 2. 两端协议核心差异对照（防踩坑必读）

`login_svr` 与 `Asynx` 的返回信封格式、HTTP 状态码约定及认证请求头**完全不同**，客户端 AI 必须分别处理：

| 维度 | Login Server (`login_svr`) | Asynx (`{baseUrl}`) |
| --- | --- | --- |
| **认证请求头** | `Authorization: Bearer <token>`（也兼容 `X-Asynx-Partner-Token`） | **必须且只能带** `X-Asynx-Partner-Token: <token>`<br>⚠️ **严禁同时携带 `Authorization`**，否则返回 `401 ambiguous_credentials` |
| **成功判断标准** | HTTP `200` 且响应 JSON 中数字 **`code === 0`** | HTTP `200` / `202` 且响应 JSON 中字符串 **`code === "ok"`**（文件上传/下载除外） |
| **成功响应结构** | `{ "code": 0, "data": { ... } }` | `{ "code": "ok", "message": null, "data": { ... }, "request_id": "req_..." }` |
| **失败响应结构** | HTTP `200`，`{ "code": <非0数字>, "msg": "<错误说明>" }` | HTTP `4xx`/`5xx`，`{ "code": "<错误码字符串>", "message": "...", "data": { ... }, "request_id": "..." }` |
| **令牌过期表现** | 返回 `{ "code": 401, "msg": "need auth" }` | 返回 HTTP `401`，`{ "code": "invalid_partner_token", ... }` |

---

## 3. Login Server (`login_svr`) 接口说明

假设 `LOGIN_SVR_URL` 为 `http://localhost:3006`，所有 POST 请求使用 `Content-Type: application/json`。

### 3.1 获取 Asynx 接入配置（无需登录）

当客户端本地已有缓存的 `token`，启动时可调用此接口获取最新 `baseUrl` 并校验本地 `token` 的签发参数是否仍然匹配：

```http
GET {LOGIN_SVR_URL}/api/user/asynx-config
```

响应：

```json
{
  "code": 0,
  "data": {
    "baseUrl": "https://asynx.example.com/api",
    "tokenHeader": "X-Asynx-Partner-Token",
    "issuer": "login_svr",
    "audience": "asynx",
    "kid": "k1",
    "tokenExpiresIn": 86400
  }
}
```

### 3.2 检查用户名是否可用（无需登录）

```http
GET {LOGIN_SVR_URL}/api/user/check-username?username=alice_01
```

- 用户名规则：`^[a-z0-9_]{3,32}$`（自动转小写）。
- 响应：`{ "code": 0, "data": { "exists": false, "available": true } }`

### 3.3 注册账号（无需登录）

```http
POST {LOGIN_SVR_URL}/api/user/register
Content-Type: application/json

{
  "username": "alice_01",
  "password": "your_password_123",
  "nickname": "Alice"
}
```

- 密码长度：`6–64` 位。
- 注册成功直接签发并返回 `token`、`refreshToken`、`asynx` 配置与 `user` 信息（结构与下方登录响应完全相同）。

### 3.4 账号登录（无需登录）

```http
POST {LOGIN_SVR_URL}/api/user/login
Content-Type: application/json

{
  "username": "alice_01",
  "password": "your_password_123"
}
```

响应：

```json
{
  "code": 0,
  "data": {
    "token": "eyJhbGciOiJFZERTQSIsInR5cCI6IkpXVCIsImtpZCI6ImsxIn0...",
    "refreshToken": "eyJhbGciOiJFZERTQSIsInR5cCI6IkpXVCJ9...",
    "asynx": {
      "baseUrl": "https://asynx.example.com/api",
      "tokenHeader": "X-Asynx-Partner-Token",
      "issuer": "login_svr",
      "audience": "asynx",
      "kid": "k1",
      "tokenExpiresIn": 86400
    },
    "user": {
      "id": "6732f0a1e4b0a1c2d3e4f567",
      "username": "alice_01",
      "nickname": "Alice",
      "locked": false,
      "deleted": false,
      "lastLogin": "2026-11-14T01:30:00.000Z",
      "createdAt": "2026-11-14T01:30:00.000Z"
    }
  }
}
```

### 3.5 刷新 Token（无需携带旧 Access Token）

当 `token` 临近过期（或收到 `401`）时，用 `refreshToken` 换取新的 `token` 和新的 `refreshToken`：

```http
POST {LOGIN_SVR_URL}/api/user/refresh
Content-Type: application/json

{
  "refreshToken": "<当前保存的 refreshToken>"
}
```

- 成功时返回新的 `{ token, refreshToken, asynx, user }`，客户端必须**同时覆盖保存新的 `token` 和 `refreshToken`**。
- 若返回 `code: 401` / `10` / `12` / `15`（如 `refreshToken` 已过期、用户改密导致 `tokenVersion` 递增、账号被锁定或注销），客户端应清理本地缓存并重新引导登录。

### 3.6 查询当前用户信息 & 修改密码（需登录）

- 查询个人信息：`GET {LOGIN_SVR_URL}/api/user/profile`（请求头带 `Authorization: Bearer <token>`）
- 修改密码：`POST {LOGIN_SVR_URL}/api/user/change-password`（请求体 `{ "oldPassword": "...", "newPassword": "..." }`）。修改密码会使 `tokenVersion + 1`，旧 Token 立即失效，接口会直接返回新的 `{ token, refreshToken, asynx, user }`。

### 3.7 `login_svr` 常见错误码

| `code` | `msg` | 含义与处理方式 |
| --- | --- | --- |
| `0` | — | 成功 |
| `10` / `11` | `need login` | 未传 Token 或账户不存在，需登录 |
| `12` | `account locked` | 账号已被锁定，停止重试并提示用户 |
| `15` | `account deleted` | 账号已注销，停止重试并提示用户 |
| `401` | `need auth` | Token 过期、签名无效或已改密失效；尝试调用 `/api/user/refresh`，若仍返回 `401` 则重新登录 |
| `1001` | `invalid username format` / `password length...` | 参数格式错误 |
| `1002` | `username already exists` | 用户名已被注册 |
| `1003` | `invalid username or password` | 登录用户名或密码错误 |
| `1004` | `invalid old password` / `new password must be different` | 修改密码时原密码错误或新旧密码相同 |

---

## 4. 客户端 Token 缓存与自动刷新策略

客户端 AI 建议将 `{ token, refreshToken, asynx, user }` 持久化在本地安全存储中，并遵循以下刷新规则：

1. **本地预判过期（主动刷新）**：
   - Base64Url 解码 `token` 的第 1 段（Header）和第 2 段（Payload）：
     - 若 ` payload.exp - 当前秒级时间戳 <= 60`（距离过期不足 60 秒）；
     - 或与 `/api/user/asynx-config` 对比发现 `header.kid !== asynx.kid`、`payload.iss !== asynx.issuer`、`payload.aud !== asynx.audience`；
   - 立即先调用 `POST /api/user/refresh` 换取新 `token`，再发起业务请求。
2. **401 被动兜底刷新（响应式刷新）**：
   - 调用 Asynx 收到 HTTP `401`（`code === "invalid_partner_token"`）时，说明 Token 已过期或密钥轮换（特别是视频等长任务轮询跨越了 Token 过期时间）。
   - 调用一次 `POST /api/user/refresh` 更新 `token` 后，用新 `token` 重试刚才的 Asynx 请求即可（正在执行的 Asynx 任务不受令牌过期影响）。

---

## 5. 向 Asynx 发起业务请求

以下所有请求均以 `asynx.baseUrl`（记为 `{BASE_URL}`）为前缀，且统一只携带请求头：

```http
X-Asynx-Partner-Token: <token>
```

### 5.1 开通用户身份并查询额度

建议在用户首次登录进入功能或提交任务前调用一次（首次调用自动在 Asynx 开通该用户身份，后续调用查询额度和并发）：

```http
GET {BASE_URL}/v1/partner/identity
X-Asynx-Partner-Token: <token>
```

成功响应 `data` 关键字段：
- `api_key.status`：`active`（正常）或 `quota_exhausted`（额度用尽，不能提新任务）
- `api_key.concurrency_limit`：该用户最大并发任务数（`null` 表示仅受主账号总并发限制）
- `usage.active_tasks`：当前进行中的任务数
- `budget.remaining_amount`：剩余可用额度（十进制字符串，单位 USD，`null` 表示不限额）

### 5.2 查询可用模型与参数指南

```http
GET {BASE_URL}/v1/tasks/models
X-Asynx-Partner-Token: <token>
```

- 返回 `data.items` 数组，包含模型名称 `name`（如 `"gpt-image-2"`）、模态 `modality`（`"image"` / `"video"`）、支持的任务类型 `task_types`（`"image.generate"`、`"image.edit"`、`"video.generate"`、`"video.edit"`）及计费说明。
- 如需查看某模型的详细参数取值范围（支持的尺寸、比例、时长等），可进一步请求：
  ```http
  GET {BASE_URL}/v1/tasks/models/{name}/guide
  X-Asynx-Partner-Token: <token>
  ```

### 5.3 任务询价（可选，不创建任务、不扣费）

```http
POST {BASE_URL}/v1/tasks/quote
X-Asynx-Partner-Token: <token>
Content-Type: application/json
```

请求体与提交任务完全一致，返回 `{ "model": "...", "currency": "USD", "estimated_amount": "1.25000000", "charge_amount": "1.25000000" }`。

### 5.4 提交生成/编辑任务

```http
POST {BASE_URL}/v1/tasks
X-Asynx-Partner-Token: <token>
Content-Type: application/json
Idempotency-Key: <唯一请求ID，如 UUID，1-128位无空格ASCII>
```

`image.generate` 请求体示例：

```json
{
  "task_type": "image.generate",
  "model": "gpt-image-2",
  "input": {
    "prompt": "a red bicycle on a beach",
    "count": 1,
    "image_size": "1K",
    "aspect_ratio": "1:1",
    "output_format": "png",
    "reference_images": []
  },
  "metadata": {
    "client_request_id": "req-001"
  }
}
```

- **幂等重试**：务必生成并携带 `Idempotency-Key`。若网络超时，使用**相同的 `Idempotency-Key` 和相同的请求体**重试，不会重复创建任务或重复扣费。
- **受理状态**：成功返回 HTTP **`202`**，此时 `data.status` 为 `"queued"`（仅代表入队受理，尚未生成完成），请记录 `data.id`（如 `"task_01M77..."`）进入轮询。
- **上传大图参考图（可选）**：若本地参考图不便使用 HTTPS 地址或 Base64 Data URL，可先通过 `multipart/form-data` 上传至 `POST {BASE_URL}/v1/files`（此接口响应不包在 `data` 信封内，直接返回 `{ "id": "file_...", ... }`）。

### 5.5 轮询任务状态

```http
GET {BASE_URL}/v1/tasks/{task_id}
X-Asynx-Partner-Token: <token>
```

- **轮询间隔**：建议每 **2–5 秒** 查询一次。
- **状态机判断**：
  - **未结束（继续轮询）**：`queued`、`running`、`delayed`、`canceling`
  - **成功结束**：`succeeded`（结果在 `data.result.assets` 数组中，只有 `succeeded` 会实际扣费）
  - **异常结束（停止轮询并报错）**：`failed`、`timeout`、`canceled`、`blocked`（具体原因读取 `data.error.code` 与 `data.error.message`，不扣费）
- **轮询中途遇到 401**：若返回 HTTP `401`（`invalid_partner_token`），立即调用 `login_svr` 的 `POST /api/user/refresh` 刷新 `token`，然后继续轮询同一个 `task_id`。

### 5.6 下载生成结果文件

当任务状态变为 `succeeded` 后，从 `data.result.assets[i]` 中获取资产信息并下载：

```http
GET {BASE_URL}/v1/tasks/{task_id}/assets/{index}
X-Asynx-Partner-Token: <token>
```

- **注意 1（必须带令牌）**：下载接口同样需要 `X-Asynx-Partner-Token: <token>` 请求头，不能把 `download_url` 直接当作公开链接交给无认证请求。
- **注意 2（跟随 302 重定向）**：接口可能直接返回文件字节流，或返回 **HTTP 302** 重定向到限时存储地址，客户端 HTTP 库需开启跟随重定向（如 `follow_redirects=True`），但重定向到外部存储域名时注意不要泄露无关认证头。
- **注意 3（及时保存到本地）**：云端文件在 `retention_expires_at` 后会清理，下载后请保存到客户端本地目录。

### 5.7 任务列表与取消任务

- **查询当前用户的任务列表**：`GET {BASE_URL}/v1/tasks?limit=50&status=succeeded`
- **取消尚未结束的任务**：`POST {BASE_URL}/v1/tasks/{task_id}/cancel`

---

## 6. 客户端 AI 完整参考实现（Python）

以下示例展示了从登录、自动刷新 Token、提交图片生成任务、轮询状态到下载结果文件的完整闭环（依赖：`pip install httpx`）：

```python
import base64
import json
import time
import uuid
import httpx


class AsynxClientSession:
    def __init__(self, login_svr_url: str, username: str, password: str):
        self.login_svr_url = login_svr_url.rstrip("/")
        self.username = username
        self.password = password
        self.token: str | None = None
        self.refresh_token: str | None = None
        self.asynx_config: dict = {}

    @staticmethod
    def _decode_jwt_segment(seg: str) -> dict:
        padding = "=" * (-len(seg) % 4)
        return json.loads(base64.urlsafe_b64decode(seg + padding))

    def _is_token_usable(self) -> bool:
        if not self.token or not self.asynx_config.get("baseUrl"):
            return False
        try:
            parts = self.token.split(".")
            header = self._decode_jwt_segment(parts[0])
            payload = self._decode_jwt_segment(parts[1])
            if header.get("kid") != self.asynx_config.get("kid"):
                return False
            if payload.get("iss") != self.asynx_config.get("issuer"):
                return False
            if payload.get("aud") != self.asynx_config.get("audience"):
                return False
            # 提前 60 秒刷新，规避时钟偏差与临界过期
            return int(payload.get("exp", 0)) - int(time.time()) > 60
        except Exception:
            return False

    def login(self) -> None:
        with httpx.Client(timeout=15) as client:
            resp = client.post(
                f"{self.login_svr_url}/api/user/login",
                json={"username": self.username, "password": self.password},
            ).json()
            if resp.get("code") != 0:
                raise RuntimeError(f"Login failed ({resp.get('code')}): {resp.get('msg')}")
            data = resp["data"]
            self.token = data["token"]
            self.refresh_token = data["refreshToken"]
            self.asynx_config = data["asynx"]

    def refresh_or_relogin(self) -> None:
        if not self.refresh_token:
            return self.login()
        with httpx.Client(timeout=15) as client:
            resp = client.post(
                f"{self.login_svr_url}/api/user/refresh",
                json={"refreshToken": self.refresh_token},
            ).json()
            if resp.get("code") == 0:
                data = resp["data"]
                self.token = data["token"]
                self.refresh_token = data["refreshToken"]
                self.asynx_config = data["asynx"]
            else:
                # refreshToken 已过期或失效，回退到重新登录
                self.login()

    def ensure_token(self) -> str:
        if not self._is_token_usable():
            self.refresh_or_relogin()
        assert self.token is not None
        return self.token

    def request_asynx(self, method: str, path: str, **kwargs) -> httpx.Response:
        for attempt in range(2):
            token = self.ensure_token()
            base_url = self.asynx_config["baseUrl"].rstrip("/")
            header_name = self.asynx_config.get("tokenHeader", "X-Asynx-Partner-Token")
            headers = {**(kwargs.pop("headers", None) or {}), header_name: token}

            with httpx.Client(base_url=base_url, timeout=30, follow_redirects=True) as api:
                resp = api.request(method, path, headers=headers, **kwargs)
                if resp.status_code == 401 and attempt == 0:
                    # 令牌在服务端轮换或过期，刷新后重试一次
                    self.refresh_or_relogin()
                    continue
                return resp
        raise RuntimeError("Asynx authentication failed after token refresh")

    def generate_image(self, prompt: str, model: str = "gpt-image-2") -> bytes:
        idem_key = f"req-{uuid.uuid4()}"
        submitted = self.request_asynx(
            "POST",
            "/v1/tasks",
            headers={"Idempotency-Key": idem_key},
            json={
                "task_type": "image.generate",
                "model": model,
                "input": {
                    "prompt": prompt,
                    "image_size": "1K",
                    "aspect_ratio": "1:1",
                },
            },
        )
        if submitted.status_code != 202:
            body = submitted.json()
            raise RuntimeError(f"Submit failed: {body.get('code')} - {body.get('message')}")

        task = submitted.json()["data"]
        task_id = task["id"]

        while task["status"] in {"queued", "running", "delayed", "canceling"}:
            time.sleep(3)
            poll_resp = self.request_asynx("GET", f"/v1/tasks/{task_id}")
            if poll_resp.status_code != 200:
                body = poll_resp.json()
                raise RuntimeError(f"Poll failed: {body.get('code')} - {body.get('message')}")
            task = poll_resp.json()["data"]

        if task["status"] != "succeeded":
            err = task.get("error") or {}
            raise RuntimeError(f"Task {task['status']}: {err.get('code')} - {err.get('message')}")

        asset_resp = self.request_asynx("GET", f"/v1/tasks/{task_id}/assets/0")
        asset_resp.raise_for_status()
        return asset_resp.content
```
