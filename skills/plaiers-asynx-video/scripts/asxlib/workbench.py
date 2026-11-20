"""Locate the optional workbench package (Local UI Workbench + local post-processing).

Core packages (image/audio/video/3d) ship only generation, query and download. Browser workbenches
and local editing (`ui ...`, `image|audio|video|model3d <local op>`) live once in the workbench
package (`plaiers-asynx-workbench`, which also carries the asset manager; ADR-0007). It is a separate registry capability that
the user installs only when wanted.

When a workbench feature is requested, the workbench's ``scripts/asxlib`` directory is appended to
this package's ``__path__``: core modules keep precedence, and workbench-only modules
(``local_*``, ``ui_server``, ``thumb_cache``) import from the workbench and share the core's state,
auth and errors. The version is checked only at that point (lockstep, never a hard requirement).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from . import __path__ as _package_path
from .constants import VERSION
from .errors import AsxError

# Registry ID and installed directory name of the workbench package (confirmed on PR #10, Q2).
WORKBENCH_SKILL_ID = "plaiers/asynx-workbench"
WORKBENCH_DIR_NAMES = ("plaiers-asynx-workbench",)
WORKBENCH_DIR_ENV = "ASYNX_WORKBENCH_DIR"
_MARKER = "ui_server.py"
_HERE = Path(__file__).resolve().parent


def _is_workbench(asxlib_dir: Path) -> bool:
    return (asxlib_dir / _MARKER).is_file() and (asxlib_dir / "ui").is_dir()


def _candidates() -> list[Path]:
    override = os.environ.get(WORKBENCH_DIR_ENV, "").strip()
    if override:
        return [Path(override).expanduser() / "scripts" / "asxlib"]
    skills_dir = _HERE.parents[2]  # <skills>/<this skill>/scripts/asxlib
    return [skills_dir / name / "scripts" / "asxlib" for name in WORKBENCH_DIR_NAMES]


def _version(asxlib_dir: Path) -> str | None:
    try:
        text = (asxlib_dir / "constants.py").read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r'^VERSION\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return match.group(1) if match else None


def not_installed(feature: str) -> AsxError:
    return AsxError(
        f"「{feature}」属于可选的本地工作台包，当前未安装。"
        f"生成、查询、下载不受影响；如需此功能，经用户同意后用 get_skill 获取 {WORKBENCH_SKILL_ID} 并用 Plaiers 安装器安装。",
        code="workbench_not_installed",
        exit_code=2,
        details={"feature": feature, "get_skill_id": WORKBENCH_SKILL_ID, "looked_in": [str(p.parents[1]) for p in _candidates()]},
    )


def require_workbench(feature: str) -> Path:
    """Make workbench modules importable as ``asxlib.<module>``; return the workbench ``asxlib`` dir."""
    if _is_workbench(_HERE):
        return _HERE
    for candidate in _candidates():
        if not _is_workbench(candidate):
            continue
        found = _version(candidate)
        if found != VERSION:
            raise AsxError(
                f"本地工作台包版本 {found or '未知'} 与当前 Skill {VERSION} 不一致；请用 Plaiers 安装器把两者装成同一版本。",
                code="workbench_version_mismatch",
                exit_code=2,
                details={"feature": feature, "workbench_version": found, "skill_version": VERSION,
                         "get_skill_id": WORKBENCH_SKILL_ID, "workbench_dir": str(candidate.parents[1])},
            )
        resolved = str(candidate.resolve())
        if resolved not in _package_path:
            _package_path.append(resolved)
        return candidate
    raise not_installed(feature)


def workbench_ui_dir(feature: str, modality: str | None) -> Path:
    """Static UI root for a modality workbench (``ui/<modality>``) or the asset-manager UI (``ui``)."""
    ui_root = require_workbench(feature) / "ui"
    if modality and (ui_root / modality / "index.html").is_file():
        return ui_root / modality
    return ui_root
