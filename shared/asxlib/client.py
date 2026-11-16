from __future__ import annotations

import hashlib
import ipaddress
import json
import mimetypes
import os
import random
import tempfile
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .config import cache_path, validate_base_url
from .constants import (
    DEFAULT_TOKEN_HEADER,
    DOWNLOAD_LIMIT_BY_MODALITY,
    DOWNLOAD_TIMEOUT_BY_MODALITY,
    DOWNLOAD_TIMEOUT_SECONDS,
    MAX_API_RESPONSE_BYTES,
    MAX_ASSET_RESPONSE_BYTES,
    MAX_HTTP_ATTEMPTS,
    MAX_REDIRECTS,
    MODEL_CACHE_TTL_SECONDS,
    REQUEST_TIMEOUT_SECONDS,
    TRANSIENT_HTTP_STATUSES,
    VERSION,
)
from .errors import AsxError, describe_response
from .request_log import log_failed_request

if TYPE_CHECKING:
    from .auth import AuthSession


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


def _decode_json(
    raw: bytes,
    *,
    context: str,
    method: str | None = None,
    url: str | None = None,
    status: int | None = None,
    content_type: str | None = None,
) -> dict[str, Any]:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AsxError(
            f"{context} 响应不是合法的 JSON 数据",
            code="invalid_api_response",
            http_status=status,
            details=describe_response(
                raw, method=method, url=url, status=status, content_type=content_type
            ),
        ) from exc
    if not isinstance(data, dict):
        raise AsxError(f"{context} 响应格式不正确，应为 JSON 对象", code="invalid_api_response")
    return data


def _extract_request_id(headers: Any, body: dict[str, Any] | None = None) -> str | None:
    if isinstance(body, dict) and isinstance(body.get("request_id"), str) and body["request_id"]:
        return body["request_id"]
    if headers:
        req_id = headers.get("X-Request-Id") or headers.get("request-id")
        if isinstance(req_id, str) and req_id:
            return req_id
    return None


def _api_error(status: int, raw: bytes, headers: Any) -> AsxError:
    message = f"HTTP {status}"
    code = f"http_{status}"
    details: dict[str, Any] | None = None
    req_id = _extract_request_id(headers)
    try:
        data = json.loads(raw.decode("utf-8"))
        if isinstance(data, dict):
            req_id = _extract_request_id(headers, data)
            error = data.get("error")
            if isinstance(error, dict):
                code = str(error.get("code") or code)
                message = str(error.get("message") or message)
                if isinstance(error.get("details"), dict):
                    details = error["details"]
            else:
                if isinstance(data.get("code"), str) and data["code"]:
                    code = data["code"]
                if isinstance(data.get("message"), str) and data["message"]:
                    message = data["message"]
                if isinstance(data.get("data"), dict) and data["data"]:
                    details = data["data"]
    except Exception:
        pass
    return AsxError(
        message,
        code=code,
        details=details,
        exit_code=2,
        http_status=status,
        request_id=req_id,
    )


def _retry_after(headers: Any, attempt: int) -> float:
    header = headers.get("Retry-After") if headers else None
    if header:
        try:
            return max(0.5, min(float(header), 30.0))
        except ValueError:
            pass
    backoff = 0.5 * (2**attempt)
    jitter = random.uniform(0, 0.25)
    return min(backoff + jitter, 10.0)


def _origin(value: str) -> tuple[str, str, int | None]:
    parts = urlsplit(value)
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    port = parts.port
    if port is None:
        if scheme == "https":
            port = 443
        elif scheme == "http":
            port = 80
    return scheme, host, port


def _validate_asset_url(value: str, *, allow_local: bool = False) -> str:
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"}:
        raise AsxError(f"非法的 Asset 下载协议：{parts.scheme}", code="invalid_asset_url")
    hostname = parts.hostname or ""
    if not hostname:
        raise AsxError("Asset URL 缺少主机名", code="invalid_asset_url")
    if not allow_local:
        try:
            ip = ipaddress.ip_address(hostname)
            if ip.is_private or ip.is_loopback or ip.is_link_local:
                raise AsxError("不允许向私有网络地址下载 Asset", code="invalid_asset_url")
        except ValueError:
            if hostname.lower() in {"localhost", "loopback"}:
                raise AsxError("不允许向本地地址下载 Asset", code="invalid_asset_url")
    return value


def _validate_api_redirect(value: str, *, base_url: str) -> str:
    parts = urlsplit(value)
    base_parts = urlsplit(base_url)
    if parts.scheme != base_parts.scheme or parts.netloc != base_parts.netloc:
        raise AsxError("不允许跳转到不同主机的 API 地址", code="invalid_api_redirect")
    return value


def _normalize_models_list(raw_models: Any) -> list[dict[str, Any]]:
    if isinstance(raw_models, list):
        return [m for m in raw_models if isinstance(m, dict)]
    if isinstance(raw_models, dict):
        for key in ("items", "models", "data"):
            val = raw_models.get(key)
            if isinstance(val, list):
                return [m for m in val if isinstance(m, dict)]
            if isinstance(val, dict):
                sub = _normalize_models_list(val)
                if sub:
                    return sub
    return []


def _validate_gateway_envelope(payload: dict[str, Any], *, status: int, req_id: str | None) -> Any:
    if status not in {200, 202}:
        raise AsxError(
            f"Asset Gateway 返回异常 HTTP 状态 {status}",
            code=f"http_{status}",
            http_status=status,
            request_id=req_id,
            exit_code=2,
        )
    if payload.get("code") != "ok":
        code = str(payload.get("code") or "invalid_api_response")
        message = str(payload.get("message") or f"Asset Gateway 返回错误 ({code})")
        details = payload.get("data") if isinstance(payload.get("data"), dict) else None
        raise AsxError(
            message,
            code=code,
            details=details,
            http_status=status,
            request_id=req_id,
            exit_code=2,
        )
    data = payload.get("data")
    if data is None:
        return {}
    return data


def _matches_modality(model: dict[str, Any], modality: str | None) -> bool:
    if not isinstance(model, dict):
        return False
    if not modality:
        return True
    raw_task_types = model.get("task_types")
    task_types = (
        [t for t in raw_task_types if isinstance(t, str)]
        if isinstance(raw_task_types, list)
        else []
    )
    model_modality = model.get("modality", "")
    if modality == "image":
        return any(t.startswith("image.") for t in task_types) or model_modality == "image"
    if modality == "audio":
        return any(t.startswith("audio.") for t in task_types) or model_modality == "audio"
    if modality == "video":
        return any(t.startswith("video.") for t in task_types) or model_modality == "video"
    if modality in {"3d", "model_3d", "model3d"}:
        return (
            any(t.startswith("3d.") for t in task_types)
            or model_modality in {"3d", "model_3d", "model3d"}
        )
    return True


def _encode_multipart(
    field_name: str,
    filename: str,
    content_type: str,
    data: bytes,
) -> tuple[bytes, str]:
    boundary = f"----AsynxBoundary{uuid.uuid4().hex}"
    parts = [
        f"--{boundary}\r\n".encode("ascii"),
        f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'.encode("utf-8"),
        f"Content-Type: {content_type}\r\n\r\n".encode("ascii"),
        data,
        f"\r\n--{boundary}--\r\n".encode("ascii"),
    ]
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


class AsynxClient:
    def __init__(
        self,
        base_url: str,
        partner_token: str | None = None,
        *,
        token_header: str = DEFAULT_TOKEN_HEADER,
        session: AuthSession | None = None,
    ) -> None:
        self.base_url = validate_base_url(base_url)
        self.partner_token = partner_token or (session.token if session is not None else "")
        self.token_header = (
            session.token_header if session is not None else (token_header or DEFAULT_TOKEN_HEADER)
        )
        self.session = session

    def _current_token(self) -> str:
        if self.session is not None:
            token = self.session.ensure_valid_token()
            self.partner_token = token
            self.base_url = self.session.base_url
            self.token_header = self.session.token_header
            return token
        return self.partner_token

    def _try_reactive_refresh(self, err: AsxError) -> bool:
        if self.session is None:
            return False
        if err.http_status != 401:
            return False
        if err.code == "ambiguous_credentials":
            return False
        if err.code not in {"invalid_partner_token", "http_401"}:
            return False
        new_token = self.session.refresh_session()
        self.partner_token = new_token
        self.base_url = self.session.base_url
        self.token_header = self.session.token_header
        return True

    def _url(self, path: str) -> str:
        return urljoin(self.base_url.rstrip("/") + "/", path.lstrip("/"))

    def _execute_gateway_raw(
        self,
        method: str,
        path: str,
        *,
        payload: bytes | None = None,
        content_type: str | None = None,
        timeout: float = REQUEST_TIMEOUT_SECONDS,
        idempotency_key: str | None = None,
    ) -> tuple[int, dict[str, Any], str | None]:
        for auth_attempt in range(2):
            token = self._current_token()
            url = self._url(path)
            opener = build_opener(_NoRedirect())
            retry_auth = False

            for _redirect in range(MAX_REDIRECTS + 1):
                redirected = False
                for attempt in range(MAX_HTTP_ATTEMPTS):
                    headers: dict[str, str] = {
                        "User-Agent": f"asx-skill/{VERSION}",
                        "Accept": "application/json",
                    }
                    if _origin(url) == _origin(self.base_url):
                        headers[self.token_header] = token
                    if payload is not None and content_type:
                        headers["Content-Type"] = content_type
                    if idempotency_key:
                        headers["Idempotency-Key"] = idempotency_key

                    request = Request(url, data=payload, headers=headers, method=method)
                    try:
                        with opener.open(request, timeout=timeout) as response:
                            status = getattr(response, "status", 200)
                            raw = response.read(MAX_API_RESPONSE_BYTES + 1)
                            if len(raw) > MAX_API_RESPONSE_BYTES:
                                raise AsxError("API 响应体超出限制", code="response_too_large")
                            try:
                                decoded = _decode_json(
                                    raw,
                                    context="API",
                                    method=method,
                                    url=url,
                                    status=status,
                                    content_type=response.headers.get("Content-Type"),
                                )
                            except AsxError as decode_err:
                                log_failed_request(
                                    method=method,
                                    url=url,
                                    request_headers=headers,
                                    request_body=payload,
                                    status=status,
                                    response_headers=response.headers,
                                    response_body=raw,
                                    error=decode_err.message,
                                    attempt=attempt + 1,
                                )
                                raise
                            req_id = _extract_request_id(response.headers, decoded)
                            return status, decoded, req_id
                    except HTTPError as exc:
                        if exc.code in {301, 302, 303, 307, 308}:
                            location = exc.headers.get("Location")
                            if not location:
                                raise AsxError(
                                    "跳转未提供 Location 头部", code="invalid_api_redirect"
                                ) from exc
                            url = _validate_api_redirect(urljoin(url, location), base_url=self.base_url)
                            redirected = True
                            break
                        raw = exc.read(MAX_API_RESPONSE_BYTES)
                        log_failed_request(
                            method=method,
                            url=url,
                            request_headers=headers,
                            request_body=payload,
                            status=exc.code,
                            response_headers=exc.headers,
                            response_body=raw,
                            attempt=attempt + 1,
                        )
                        if exc.code in TRANSIENT_HTTP_STATUSES and attempt < MAX_HTTP_ATTEMPTS - 1:
                            time.sleep(_retry_after(exc.headers, attempt))
                            continue
                        err = _api_error(exc.code, raw, exc.headers)
                        if auth_attempt == 0 and self._try_reactive_refresh(err):
                            retry_auth = True
                            break
                        raise err from exc
                    except (URLError, TimeoutError) as exc:
                        log_failed_request(
                            method=method,
                            url=url,
                            request_headers=headers,
                            request_body=payload,
                            error=str(exc),
                            attempt=attempt + 1,
                        )
                        if attempt < MAX_HTTP_ATTEMPTS - 1:
                            time.sleep(_retry_after(None, attempt))
                            continue
                        raise AsxError(f"网络连接失败：{exc}", code="network_error") from exc
                if retry_auth:
                    break
                if redirected:
                    continue
                raise AsxError("超过最大重定向次数", code="too_many_redirects")
            if retry_auth:
                continue
            raise AsxError("超过最大重定向次数", code="too_many_redirects")
        raise AsxError("Asynx 认证失败", code="invalid_partner_token", http_status=401, exit_code=2)

    def request_json(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        timeout: float = REQUEST_TIMEOUT_SECONDS,
        idempotency_key: str | None = None,
    ) -> tuple[Any, str | None]:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        status, decoded, req_id = self._execute_gateway_raw(
            method,
            path,
            payload=payload,
            content_type="application/json" if payload is not None else None,
            timeout=timeout,
            idempotency_key=idempotency_key,
        )
        try:
            data = _validate_gateway_envelope(decoded, status=status, req_id=req_id)
        except AsxError as envelope_err:
            log_failed_request(
                method=method,
                url=self._url(path),
                request_headers={
                    self.token_header: self.partner_token,
                    **({"Content-Type": "application/json"} if payload is not None else {}),
                    **({"Idempotency-Key": idempotency_key} if idempotency_key else {}),
                },
                request_body=payload,
                status=status,
                response_body=json.dumps(decoded, ensure_ascii=False),
                error=f"{envelope_err.code}: {envelope_err.message}",
            )
            raise
        return data, req_id

    def partner_identity(self) -> tuple[dict[str, Any], str | None]:
        data, req_id = self.request_json("GET", "/v1/partner/identity")
        if not isinstance(data, dict):
            raise AsxError("Partner identity 响应格式不正确", code="invalid_api_response", request_id=req_id)
        return data, req_id

    def models(self, modality: str | None = None) -> tuple[list[dict[str, Any]], str | None]:
        cache_file = cache_path()
        now = time.time()
        if cache_file.is_file():
            try:
                cached = json.loads(cache_file.read_text(encoding="utf-8"))
                if (
                    isinstance(cached, dict)
                    and cached.get("base_url") in {None, self.base_url}
                    and now - cached.get("cached_at", 0) < MODEL_CACHE_TTL_SECONDS
                ):
                    models = _normalize_models_list(cached.get("models")) or _normalize_models_list(
                        cached.get("items")
                    )
                    if models:
                        filtered = [m for m in models if _matches_modality(m, modality)]
                        return filtered, cached.get("request_id")
            except Exception:
                pass

        data, req_id = self.request_json("GET", "/v1/tasks/models")
        models = _normalize_models_list(data)
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(
                json.dumps(
                    {
                        "base_url": self.base_url,
                        "cached_at": now,
                        "request_id": req_id,
                        "models": models,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except Exception:
            pass
        filtered = [m for m in models if _matches_modality(m, modality)]
        return filtered, req_id

    def model_guide(self, name: str) -> tuple[dict[str, Any], str | None]:
        data, req_id = self.request_json("GET", f"/v1/tasks/models/{quote(name, safe='')}/guide")
        if not isinstance(data, dict):
            raise AsxError("Model guide 响应格式不正确", code="invalid_api_response", request_id=req_id)
        return data, req_id

    def quote(self, task_type: str, payload: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
        body = {"task_type": task_type, **payload}
        data, req_id = self.request_json("POST", "/v1/tasks/quote", body=body)
        if not isinstance(data, dict):
            raise AsxError("Quote 响应格式不正确", code="invalid_api_response", request_id=req_id)
        return data, req_id

    def upload_file(
        self,
        file_path: Path | str,
        *,
        filename: str | None = None,
        content_type: str | None = None,
        timeout: float = REQUEST_TIMEOUT_SECONDS,
    ) -> tuple[dict[str, Any], str | None]:
        path = Path(file_path).expanduser().resolve()
        if not path.is_file():
            raise AsxError(f"上传文件不存在：{path}", code="file_not_found")
        raw_bytes = path.read_bytes()
        resolved_name = filename or path.name
        guessed_type = content_type or mimetypes.guess_type(resolved_name)[0] or "application/octet-stream"
        multipart_body, multipart_ct = _encode_multipart("file", resolved_name, guessed_type, raw_bytes)
        status, decoded, req_id = self._execute_gateway_raw(
            "POST",
            "/v1/files",
            payload=multipart_body,
            content_type=multipart_ct,
            timeout=timeout,
        )
        if status not in {200, 201, 202}:
            raise AsxError(
                f"文件上传返回异常 HTTP 状态 {status}",
                code=f"http_{status}",
                http_status=status,
                request_id=req_id,
            )
        if isinstance(decoded.get("id"), str) and decoded["id"]:
            return decoded, req_id
        if decoded.get("code") == "ok" and isinstance(decoded.get("data"), dict):
            return decoded["data"], req_id
        raise AsxError(
            "文件上传响应缺少文件 id",
            code="invalid_api_response",
            http_status=status,
            request_id=req_id,
        )

    def submit(
        self,
        task_type: str,
        payload: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> tuple[dict[str, Any], str | None]:
        body = {"task_type": task_type, **payload}
        data, req_id = self.request_json("POST", "/v1/tasks", body=body, idempotency_key=idempotency_key)
        if not isinstance(data, dict):
            raise AsxError("提交任务响应格式不正确", code="invalid_api_response", request_id=req_id)
        return data, req_id

    def task(self, task_id: str) -> tuple[dict[str, Any], str | None]:
        data, req_id = self.request_json("GET", f"/v1/tasks/{quote(task_id, safe='')}")
        if not isinstance(data, dict):
            raise AsxError("任务查询响应格式不正确", code="invalid_api_response", request_id=req_id)
        return data, req_id

    def cancel(self, task_id: str) -> tuple[dict[str, Any], str | None]:
        data, req_id = self.request_json("POST", f"/v1/tasks/{quote(task_id, safe='')}/cancel")
        if not isinstance(data, dict):
            raise AsxError("取消任务响应格式不正确", code="invalid_api_response", request_id=req_id)
        return data, req_id

    def list_tasks(
        self,
        status: str | None = None,
        limit: int = 50,
    ) -> tuple[dict[str, Any], str | None]:
        params: list[str] = [f"limit={limit}"]
        if status:
            params.append(f"status={quote(status, safe='')}")
        query_string = "?" + "&".join(params)
        data, req_id = self.request_json("GET", f"/v1/tasks{query_string}")
        if not isinstance(data, dict):
            raise AsxError("任务列表响应格式不正确", code="invalid_api_response", request_id=req_id)
        return data, req_id

    def download_asset_to_file(
        self,
        task_id: str,
        index: int,
        destination_path: Path | str,
        *,
        download_url: str | None = None,
        max_bytes: int | None = None,
        timeout_seconds: float | None = None,
        modality: str | None = None,
        metrics: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Stream an asset directly to disk with SHA-256 calculation and size enforcement."""
        dest = Path(destination_path).resolve()
        dest.parent.mkdir(mode=0o755, parents=True, exist_ok=True)

        limit_bytes = max_bytes or DOWNLOAD_LIMIT_BY_MODALITY.get(
            modality or "image", MAX_ASSET_RESPONSE_BYTES
        )
        timeout = timeout_seconds or DOWNLOAD_TIMEOUT_BY_MODALITY.get(
            modality or "image", DOWNLOAD_TIMEOUT_SECONDS
        )
        allow_local = urlsplit(self.base_url).hostname in {"localhost", "127.0.0.1", "::1"}

        for auth_attempt in range(2):
            token = self._current_token()
            if download_url is None:
                url = self._url(f"/v1/tasks/{quote(task_id, safe='')}/assets/{index}")
            else:
                parsed_dl = urlsplit(download_url)
                resolved_dl = (
                    urljoin(self.base_url.rstrip("/") + "/", download_url)
                    if not parsed_dl.scheme and not parsed_dl.netloc
                    else download_url
                )
                url = _validate_asset_url(resolved_dl, allow_local=allow_local)
            opener = build_opener(_NoRedirect())
            retry_auth = False

            for _redirect in range(MAX_REDIRECTS + 1):
                redirected = False
                for attempt in range(MAX_HTTP_ATTEMPTS):
                    headers: dict[str, str] = {"User-Agent": f"asx-skill/{VERSION}"}
                    if _origin(url) == _origin(self.base_url):
                        headers[self.token_header] = token

                    request = Request(url, headers=headers, method="GET")
                    request_started = time.perf_counter()
                    try:
                        with opener.open(request, timeout=timeout) as response:
                            response_started = time.perf_counter()
                            content_type = response.headers.get("Content-Type")

                            hasher = hashlib.sha256()
                            total_bytes = 0
                            prefix = f".tmp-{dest.name}-"

                            with tempfile.NamedTemporaryFile(
                                "wb", dir=str(dest.parent), prefix=prefix, delete=False
                            ) as temp_file:
                                temp_path = Path(temp_file.name)
                                try:
                                    while True:
                                        chunk = response.read(64 * 1024)
                                        if not chunk:
                                            break
                                        total_bytes += len(chunk)
                                        if total_bytes > limit_bytes:
                                            raise AsxError(
                                                f"Asset 文件超出大小限制 ({limit_bytes} 字节)",
                                                code="asset_too_large",
                                            )
                                        hasher.update(chunk)
                                        temp_file.write(chunk)
                                    temp_file.flush()
                                    os.fsync(temp_file.fileno())

                                    cl_header = response.headers.get("Content-Length")
                                    if cl_header and cl_header.isdigit():
                                        expected_bytes = int(cl_header)
                                        if total_bytes < expected_bytes:
                                            raise AsxError(
                                                f"下载未完整接收：已接收 {total_bytes}/{expected_bytes} 字节",
                                                code="download_truncated",
                                            )
                                except Exception:
                                    if temp_path.exists():
                                        try:
                                            temp_path.unlink()
                                        except OSError:
                                            pass
                                    raise

                            temp_path.replace(dest)

                            if metrics is not None:
                                metrics.update(
                                    {
                                        "ttfb_seconds": response_started - request_started,
                                        "transfer_seconds": time.perf_counter() - response_started,
                                        "bytes": total_bytes,
                                        "sha256": hasher.hexdigest(),
                                    }
                                )

                            return {
                                "path": str(dest),
                                "bytes": total_bytes,
                                "sha256": hasher.hexdigest(),
                                "content_type": content_type,
                            }
                    except HTTPError as exc:
                        if exc.code in {301, 302, 303, 307, 308}:
                            location = exc.headers.get("Location")
                            if not location:
                                raise AsxError(
                                    "Asset 下载跳转未提供 Location 头部",
                                    code="invalid_asset_redirect",
                                ) from exc
                            url = _validate_asset_url(
                                urljoin(url, location),
                                allow_local=allow_local,
                            )
                            redirected = True
                            break
                        raw = exc.read(MAX_API_RESPONSE_BYTES)
                        log_failed_request(
                            method="GET",
                            url=url,
                            request_headers=headers,
                            request_body=None,
                            status=exc.code,
                            response_headers=exc.headers,
                            response_body=raw,
                            attempt=attempt + 1,
                        )
                        if exc.code in TRANSIENT_HTTP_STATUSES and attempt < MAX_HTTP_ATTEMPTS - 1:
                            time.sleep(_retry_after(exc.headers, attempt))
                            continue
                        err = _api_error(exc.code, raw, exc.headers)
                        if auth_attempt == 0 and self._try_reactive_refresh(err):
                            retry_auth = True
                            break
                        raise err from exc
                    except (URLError, TimeoutError) as exc:
                        log_failed_request(
                            method="GET",
                            url=url,
                            request_headers=headers,
                            request_body=None,
                            error=str(exc),
                            attempt=attempt + 1,
                        )
                        if attempt < MAX_HTTP_ATTEMPTS - 1:
                            time.sleep(_retry_after(None, attempt))
                            continue
                        raise AsxError(f"Asset 下载失败：{exc}", code="asset_download_failed") from exc
                if retry_auth:
                    break
                if redirected:
                    continue
                raise AsxError("超过最大重定向次数", code="too_many_redirects")
            if retry_auth:
                continue
            raise AsxError("超过最大重定向次数", code="too_many_redirects")
        raise AsxError("Asset 下载认证失败", code="invalid_partner_token", http_status=401, exit_code=2)

    def asset(
        self,
        task_id: str,
        index: int,
        download_url: str | None = None,
        *,
        metrics: dict[str, Any] | None = None,
    ) -> tuple[bytes, str | None]:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir) / "asset.bin"
            result = self.download_asset_to_file(
                task_id, index, tmppath, download_url=download_url, metrics=metrics
            )
            return tmppath.read_bytes(), result.get("content_type")
