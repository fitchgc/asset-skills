from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import random
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .config import cache_path, validate_base_url
from .constants import (
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
from .errors import AsxError


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


def _decode_json(raw: bytes, *, context: str) -> dict[str, Any]:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AsxError(
            f"{context} 响应不是合法的 JSON 数据",
            code="invalid_api_response",
        ) from exc
    if not isinstance(data, dict):
        raise AsxError(f"{context} 响应格式不正确，应为 JSON 对象", code="invalid_api_response")
    return data


def _api_error(status: int, raw: bytes, headers: Any) -> AsxError:
    message = f"HTTP {status}"
    code = f"http_{status}"
    details: dict[str, Any] | None = None
    try:
        data = json.loads(raw.decode("utf-8"))
        if isinstance(data, dict):
            error = data.get("error")
            if isinstance(error, dict):
                code = str(error.get("code") or code)
                message = str(error.get("message") or message)
                if isinstance(error.get("details"), dict):
                    details = error["details"]
            elif isinstance(data.get("message"), str):
                message = data["message"]
    except Exception:
        pass
    return AsxError(message, code=code, details=details, exit_code=2)


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
    return parts.scheme, parts.hostname or "", parts.port


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


def _unwrap_payload(payload: Any) -> Any:
    if isinstance(payload, dict) and "data" in payload and payload.get("code") in {"ok", 0, None}:
        data = payload["data"]
        if isinstance(data, (dict, list)):
            return data
    return payload


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


class AsynxClient:
    def __init__(self, base_url: str, api_key: str) -> None:
        self.base_url = validate_base_url(base_url)
        self.api_key = api_key

    def _url(self, path: str) -> str:
        return urljoin(self.base_url.rstrip("/") + "/", path.lstrip("/"))

    def request_json(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        timeout: float = REQUEST_TIMEOUT_SECONDS,
        idempotency_key: str | None = None,
    ) -> tuple[dict[str, Any], str | None]:
        url = self._url(path)
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "User-Agent": f"asx-skill/{VERSION}",
            "Accept": "application/json",
        }
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key

        opener = build_opener(_NoRedirect())
        for _redirect in range(MAX_REDIRECTS + 1):
            for attempt in range(MAX_HTTP_ATTEMPTS):
                request = Request(url, data=payload, headers=headers, method=method)
                try:
                    with opener.open(request, timeout=timeout) as response:
                        raw = response.read(MAX_API_RESPONSE_BYTES + 1)
                        if len(raw) > MAX_API_RESPONSE_BYTES:
                            raise AsxError("API 响应体超出限制", code="response_too_large")
                        req_id = response.headers.get("X-Request-Id") or response.headers.get("request-id")
                        return _decode_json(raw, context="API"), req_id
                except HTTPError as exc:
                    if exc.code in {301, 302, 303, 307, 308}:
                        location = exc.headers.get("Location")
                        if not location:
                            raise AsxError("跳转未提供 Location 头部", code="invalid_api_redirect") from exc
                        url = _validate_api_redirect(urljoin(url, location), base_url=self.base_url)
                        break
                    raw = exc.read(MAX_API_RESPONSE_BYTES)
                    if exc.code in TRANSIENT_HTTP_STATUSES and attempt < MAX_HTTP_ATTEMPTS - 1:
                        time.sleep(_retry_after(exc.headers, attempt))
                        continue
                    raise _api_error(exc.code, raw, exc.headers) from exc
                except (URLError, TimeoutError) as exc:
                    if attempt < MAX_HTTP_ATTEMPTS - 1:
                        time.sleep(_retry_after(None, attempt))
                        continue
                    raise AsxError(f"网络连接失败：{exc}", code="network_error") from exc
            else:
                continue
            break
        raise AsxError("超过最大重定向次数", code="too_many_redirects")

    def models(self, modality: str | None = None) -> tuple[list[dict[str, Any]], str | None]:
        cache_file = cache_path()
        now = time.time()
        if cache_file.is_file():
            try:
                cached = json.loads(cache_file.read_text(encoding="utf-8"))
                if isinstance(cached, dict) and now - cached.get("cached_at", 0) < MODEL_CACHE_TTL_SECONDS:
                    models = _normalize_models_list(cached.get("models")) or _normalize_models_list(cached.get("items"))
                    if models:
                        filtered = [m for m in models if _matches_modality(m, modality)]
                        return filtered, cached.get("request_id")
            except Exception:
                pass

        data, req_id = self.request_json("GET", "/v1/tasks/models")
        models = (
            _normalize_models_list(data.get("models"))
            or _normalize_models_list(data.get("items"))
            or _normalize_models_list(data.get("data"))
            or _normalize_models_list(data)
        )
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(
                json.dumps({"cached_at": now, "request_id": req_id, "models": models}, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            pass
        filtered = [m for m in models if _matches_modality(m, modality)]
        return filtered, req_id

    def submit(
        self,
        task_type: str,
        payload: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> tuple[dict[str, Any], str | None]:
        body = {"task_type": task_type, **payload}
        data, req_id = self.request_json("POST", "/v1/tasks", body=body, idempotency_key=idempotency_key)
        return _unwrap_payload(data), req_id

    def task(self, task_id: str) -> tuple[dict[str, Any], str | None]:
        data, req_id = self.request_json("GET", f"/v1/tasks/{quote(task_id, safe='')}")
        return _unwrap_payload(data), req_id

    def cancel(self, task_id: str) -> tuple[dict[str, Any], str | None]:
        data, req_id = self.request_json("POST", f"/v1/tasks/{quote(task_id, safe='')}/cancel")
        return _unwrap_payload(data), req_id

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
        return _unwrap_payload(data), req_id

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

        url = (
            self._url(f"/v1/tasks/{quote(task_id, safe='')}/assets/{index}")
            if download_url is None
            else _validate_asset_url(
                download_url,
                allow_local=urlsplit(self.base_url).hostname in {"localhost", "127.0.0.1", "::1"},
            )
        )

        limit_bytes = max_bytes or DOWNLOAD_LIMIT_BY_MODALITY.get(
            modality or "image", MAX_ASSET_RESPONSE_BYTES
        )
        timeout = timeout_seconds or DOWNLOAD_TIMEOUT_BY_MODALITY.get(
            modality or "image", DOWNLOAD_TIMEOUT_SECONDS
        )

        headers = {"User-Agent": f"asx-skill/{VERSION}"}
        if _origin(url) == _origin(self.base_url):
            headers["Authorization"] = f"Bearer {self.api_key}"

        opener = build_opener(_NoRedirect())
        for _redirect in range(MAX_REDIRECTS + 1):
            for attempt in range(MAX_HTTP_ATTEMPTS):
                request = Request(url, headers=headers, method="GET")
                request_started = time.perf_counter()
                try:
                    with opener.open(request, timeout=timeout) as response:
                        response_started = time.perf_counter()
                        content_type = response.headers.get("Content-Type")

                        # Stream in chunks into temporary file in target directory
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
                            allow_local=urlsplit(self.base_url).hostname in {"localhost", "127.0.0.1", "::1"},
                        )
                        break
                    if exc.code in TRANSIENT_HTTP_STATUSES and attempt < MAX_HTTP_ATTEMPTS - 1:
                        time.sleep(_retry_after(exc.headers, attempt))
                        continue
                    raise _api_error(exc.code, exc.read(MAX_API_RESPONSE_BYTES), exc.headers) from exc
                except (URLError, TimeoutError) as exc:
                    if attempt < MAX_HTTP_ATTEMPTS - 1:
                        time.sleep(_retry_after(None, attempt))
                        continue
                    raise AsxError(f"Asset 下载失败：{exc}", code="asset_download_failed") from exc
            else:
                continue
            break
        raise AsxError("超过最大重定向次数", code="too_many_redirects")

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
