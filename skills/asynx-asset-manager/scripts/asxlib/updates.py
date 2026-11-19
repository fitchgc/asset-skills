"""Registry-driven update status (see ADR-0007).

The installed package never rewrites its own files and never checks for updates in the background.
`update status` asks the Plaiers skill registry (the site recorded in the local credential context)
whether the installed pinned version is still published and what the current version is. Applying an
update is an explicit, user-confirmed run of the Plaiers installer with the new pinned version.
"""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .auth import context_path, load_credential_context
from .constants import VERSION
from .errors import AsxError

REGISTRY_TIMEOUT_SECONDS = 10
MAX_REGISTRY_RESPONSE_BYTES = 1024 * 1024
_ID = re.compile(r"plaiers/[a-z0-9]+(?:-[a-z0-9]+)*\Z")
_VERSION = re.compile(r"[0-9a-f]{64}\Z")
_INSTALLER = "install-capability-skill.mjs"
_DEV_ORIGIN = "https://dev.plaiers.ai"
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, D102
        return None


def _registry_origin(origin: str) -> str:
    parts = urlsplit(origin)
    if origin == _DEV_ORIGIN or (parts.scheme == "http" and parts.hostname in _LOOPBACK):
        return origin
    raise AsxError(
        "技能库目前只在 dev 站点提供，当前安装记录的站点不支持更新查询",
        code="registry_unsupported_origin",
        details={"origin": origin},
    )


def installed_identity() -> dict[str, Any]:
    """Pinned registry identity recorded by the Plaiers installer next to the package."""
    context = load_credential_context()
    try:
        raw = json.loads(Path(context["path"]).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:  # validated just above; a race leaves it unreadable
        raise AsxError("无法读取本机安装记录", code="invalid_install_context") from exc
    skill_id, version = raw.get("skill_id"), raw.get("skill_version")
    if not isinstance(skill_id, str) or not _ID.fullmatch(skill_id) or not isinstance(version, str) or not _VERSION.fullmatch(version):
        raise AsxError(
            "本 Skill 不是通过技能库安装的锁定版本，无法查询更新。请通过 plaiers-community 用 search_skills / get_skill 选定版本后重新安装。",
            code="not_registry_install",
            details={"context_path": context["path"]},
        )
    return {"id": skill_id, "version": version, "origin": context["origin"], "jwt_command": context["jwt_command"]}


def _get(origin: str, params: dict[str, str]) -> tuple[int, dict[str, Any]]:
    url = f"{origin}/api/skills/get?{urlencode(params)}"
    request = Request(url, headers={"Accept": "application/json", "User-Agent": f"asx-skill/{VERSION}"})
    try:
        with build_opener(_NoRedirect()).open(request, timeout=REGISTRY_TIMEOUT_SECONDS) as response:
            status, body = response.status, response.read(MAX_REGISTRY_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        status, body = exc.code, exc.read(MAX_REGISTRY_RESPONSE_BYTES + 1)
        exc.close()
    except (URLError, OSError, TimeoutError) as exc:
        raise AsxError("暂时无法连接技能库，仍可使用当前版本", code="registry_unavailable", details={"origin": origin}) from exc
    if len(body) > MAX_REGISTRY_RESPONSE_BYTES:
        raise AsxError("技能库响应过大", code="registry_unavailable", details={"origin": origin})
    try:
        data = json.loads(body.decode("utf-8")) if body else {}
    except (UnicodeError, ValueError):
        data = {}
    return status, data if isinstance(data, dict) else {}


def install_command(identity: dict[str, Any], version: str) -> list[str]:
    """Plaiers installer argv for a pinned version, reusing the recorded credential selection."""
    bridge = identity["jwt_command"]
    installer = str(Path(bridge[1]).with_name(_INSTALLER))
    return [bridge[0], installer, "install", "--id", identity["id"], "--version", version, *bridge[2:]]


def update_status() -> dict[str, Any]:
    identity = installed_identity()
    origin = _registry_origin(identity["origin"])
    pinned_status, _ = _get(origin, {"id": identity["id"], "version": identity["version"]})
    current_status, current = _get(origin, {"id": identity["id"]})
    if pinned_status == 404 and current_status == 404:
        raise AsxError(
            "技能库未开放或已下架本 Skill，仍可使用当前版本",
            code="registry_disabled",
            details={"origin": origin, "id": identity["id"]},
        )
    if pinned_status not in {200, 404, 410} or current_status not in {200, 404, 410}:
        raise AsxError("暂时无法确认是否有更新，仍可使用当前版本", code="registry_unavailable", details={"origin": origin})
    current_version = current.get("version") if current_status == 200 else None
    if current_version is not None and (not isinstance(current_version, str) or not _VERSION.fullmatch(current_version) or current.get("id") != identity["id"]):
        raise AsxError("技能库返回了无效的版本信息", code="registry_unavailable", details={"origin": origin})
    withdrawn = pinned_status == 410
    update_available = current_version is not None and current_version != identity["version"]
    result: dict[str, Any] = {
        "ok": True,
        "id": identity["id"],
        "origin": origin,
        "installed_version": identity["version"],
        "current_version": current_version,
        "withdrawn": withdrawn,
        "installed_available": pinned_status == 200,
        "update_available": update_available,
        "package_version": VERSION,
    }
    if withdrawn:
        result["withdrawn_notice"] = "已安装的版本已被撤回。当前副本保持不变，不会被自动删除；是否替换或移除请先征得用户同意。"
    if update_available:
        command = install_command(identity, current_version)
        result["update"] = {
            "requires_user_consent": True,
            "install_command": command,
            "install_command_shell": " ".join(shlex.quote(part) for part in command),
            "message": "有新的锁定版本。征得用户同意后，用 Plaiers 安装器安装该版本；本 Skill 不会改写自己的文件。",
        }
    return result


def update_apply_removed() -> dict[str, Any]:
    """`update apply` / `update rollback` no longer exist: the package never rewrites itself."""
    raise AsxError(
        "Skill 不再自行更新或回滚。请先运行 update status，征得用户同意后用其中给出的 Plaiers 安装器命令（install-capability-skill.mjs）安装新的锁定版本；安装器会备份旧副本，失败时自动回滚。",
        code="update_apply_removed",
        details={"status_command": "update status"},
    )


def update_status_for_doctor() -> dict[str, Any]:
    """Explicit `doctor --updates`: report problems inside the payload instead of failing the diagnosis."""
    try:
        return update_status()
    except AsxError as exc:
        return exc.payload()
