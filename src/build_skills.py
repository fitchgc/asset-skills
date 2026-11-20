#!/usr/bin/env python3
"""Build and bundle standalone Asynx Asset Skills from shared source."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
SHARED_DIR = REPO_ROOT / "shared"
SRC_DIR = REPO_ROOT / "src"
SKILLS_DIR = REPO_ROOT / "skills"
DIST_DIR = REPO_ROOT / "dist"

MODALITIES = {
    "image": {
        "skill_name": "plaiers-asynx-image-pro",
        "src_module": "image",
    },
    "audio": {
        "skill_name": "plaiers-asynx-audio",
        "src_module": "audio",
    },
    "video": {
        "skill_name": "plaiers-asynx-video",
        "src_module": "video",
    },
    "3d": {
        "skill_name": "plaiers-asynx-3d",
        "src_module": "model3d",
    },
    "asset_manager": {
        "skill_name": "plaiers-asynx-workbench",
        "src_module": "asset_manager",
    },
}

CROSS_MODAL_MODULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("image", ("images.py", "local_images.py", "image_results.py", "reference_media.py")),
    ("audio", ("audio_tasks.py", "local_audio.py")),
    ("video", ("video_tasks.py", "local_video.py")),
    ("model3d", ("model3d_tasks.py", "local_model3d.py")),
)

# The workbench package plaiers-asynx-workbench (asset manager + Local UI Workbench + local post-processing);
# core modality packages ship without these modules and load them from it on demand (ADR-0007).
WORKBENCH_KEY = "asset_manager"
WORKBENCH_ONLY_MODULES = frozenset({"ui_server.py", "thumb_cache.py", "local_images.py", "local_audio.py",
                                    "local_video.py", "local_model3d.py"})
# Plaiers skill registry limits (UTF-8 text only).
REGISTRY_MAX_FILES = 200
REGISTRY_MAX_BYTES = 4 * 1024 * 1024

_IGNORED_CACHE_PATTERNS = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", ".DS_Store")


def _is_cache_artifact(path: Path) -> bool:
    return "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"} or path.name == ".DS_Store"


def _prune_cache_artifacts(root: Path) -> None:
    if not root.exists():
        return
    for cache_dir in sorted(root.rglob("__pycache__"), reverse=True):
        if cache_dir.is_dir():
            shutil.rmtree(cache_dir, ignore_errors=True)
    for path in list(root.rglob("*")):
        if path.is_file() and _is_cache_artifact(path):
            try:
                path.unlink()
            except OSError:
                pass


class PackageCheckError(SystemExit):
    pass


def check_package(skill_dir: Path) -> dict[str, int]:
    """Fail the build unless the package fits the registry: ≤ 200 files, ≤ 4 MiB, every file UTF-8 text."""
    files = [p for p in sorted(skill_dir.rglob("*")) if p.is_file() and p.name != ".asx-install.json"]
    problems: list[str] = []
    total = 0
    for path in files:
        data = path.read_bytes()
        total += len(data)
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            problems.append(f"non-UTF-8 file: {path.relative_to(skill_dir).as_posix()}")
    if len(files) > REGISTRY_MAX_FILES:
        problems.append(f"{len(files)} files > {REGISTRY_MAX_FILES}")
    if total > REGISTRY_MAX_BYTES:
        problems.append(f"{total} bytes > {REGISTRY_MAX_BYTES}")
    if problems:
        raise PackageCheckError(f"{skill_dir.name}: " + "; ".join(problems))
    return {"files": len(files), "bytes": total}


def get_version() -> str:
    from shared.asxlib.constants import VERSION
    return VERSION


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(64 * 1024):
            h.update(chunk)
    return h.hexdigest()


def build_skill(modality_key: str) -> Path:
    info = MODALITIES[modality_key]
    skill_name = info["skill_name"]
    src_module_name = info["src_module"]

    skill_dir = SKILLS_DIR / skill_name
    scripts_dir = skill_dir / "scripts"
    asxlib_dir = scripts_dir / "asxlib"

    # Clean target scripts/asxlib
    if asxlib_dir.exists():
        shutil.rmtree(asxlib_dir)
    asxlib_dir.mkdir(parents=True, exist_ok=True)

    # 1. Copy shared/scripts/ (asynx.py, asx_runtime.py)
    for script_file in (SHARED_DIR / "scripts").glob("*.py"):
        shutil.copy2(script_file, scripts_dir / script_file.name)
        if script_file.name == "asynx.py":
            (scripts_dir / script_file.name).chmod(0o755)

    # 2. Third-party packages are never bundled; Pillow comes from the shared runtime
    #    (`asynx.py runtime prepare`). Drop any stale bundled copy.
    stale_vendor = scripts_dir / "vendor"
    if stale_vendor.exists():
        shutil.rmtree(stale_vendor)

    # 3. Copy shared/asxlib/ to scripts/asxlib/
    is_workbench = modality_key == WORKBENCH_KEY
    for py_file in sorted((SHARED_DIR / "asxlib").glob("*.py")):
        if is_workbench or py_file.name not in WORKBENCH_ONLY_MODULES:
            shutil.copy2(py_file, asxlib_dir / py_file.name)

    # Create __init__.py in asxlib if missing
    init_py = asxlib_dir / "__init__.py"
    if not init_py.exists():
        init_py.write_text('"""Asynx skill library."""\n', encoding="utf-8")

    # 4. Copy cross-modal modules for asset_manager before modality-specific modules
    #    so asset_manager's cli.py is never overwritten and standalone packages are self-contained.
    if modality_key == "asset_manager":
        for src_subdir, filenames in CROSS_MODAL_MODULES:
            mod_dir = SRC_DIR / src_subdir
            for fname in filenames:
                src_file = mod_dir / fname
                if src_file.is_file():
                    shutil.copy2(src_file, asxlib_dir / fname)

    # 5. Copy modality-specific modules from src/<modality>/
    modality_src = SRC_DIR / src_module_name
    if modality_src.exists():
        for py_file in sorted(modality_src.glob("*.py")):
            if is_workbench or py_file.name not in WORKBENCH_ONLY_MODULES:
                shutil.copy2(py_file, asxlib_dir / py_file.name)

    # 6. UI bundle only in the workbench package: asset-manager UI + shared common assets + every
    #    modality workbench under ui/<modality>/. No fonts (system-font fallback, ADR-0007).
    target_ui = asxlib_dir / "ui"
    if is_workbench:
        target_ui.mkdir(parents=True, exist_ok=True)
        ui_src = SRC_DIR / "ui" / modality_key
        if ui_src.exists():
            shutil.copytree(ui_src, target_ui, dirs_exist_ok=True, ignore=_IGNORED_CACHE_PATTERNS)
        common_ui = SRC_DIR / "ui" / "common"
        if common_ui.is_dir():
            shutil.copytree(common_ui, target_ui, dirs_exist_ok=True, ignore=_IGNORED_CACHE_PATTERNS)
        for sub_ui in ("image", "video", "3d"):
            sub_ui_src = SRC_DIR / "ui" / sub_ui
            if sub_ui_src.is_dir():
                shutil.copytree(
                    sub_ui_src,
                    target_ui / sub_ui,
                    dirs_exist_ok=True,
                    ignore=_IGNORED_CACHE_PATTERNS,
                )

    _prune_cache_artifacts(skill_dir)
    check_package(skill_dir)

    # 7. Generate .asx-install.json receipt
    version = get_version()
    file_hashes: dict[str, str] = {}
    for path in sorted(skill_dir.rglob("*")):
        if path.is_file() and not path.name.startswith(".") and not _is_cache_artifact(path):
            rel_path = path.relative_to(skill_dir).as_posix()
            file_hashes[rel_path] = file_sha256(path)

    receipt = {
        "files": file_hashes,
        "schema": 1,
        "version": version,
    }
    (skill_dir / ".asx-install.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"Built skill: {skill_name} -> {skill_dir}")
    return skill_dir


def package_skill(modality_key: str) -> tuple[Path, Path]:
    skill_dir = build_skill(modality_key)
    skill_name = MODALITIES[modality_key]["skill_name"]
    version = get_version()

    DIST_DIR.mkdir(parents=True, exist_ok=True)
    archive_path = DIST_DIR / f"{skill_name}.zip"
    versioned_archive_path = DIST_DIR / f"{skill_name}-{version}.zip"

    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(skill_dir.rglob("*")):
            if path.is_file() and not _is_cache_artifact(path):
                rel_path = path.relative_to(skill_dir).as_posix()
                zi = zipfile.ZipInfo.from_file(path, f"{skill_name}/{rel_path}")
                zi.date_time = (2025, 1, 1, 0, 0, 0)
                zf.writestr(zi, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED)

    shutil.copy2(archive_path, versioned_archive_path)

    archive_hash = file_sha256(archive_path)
    manifest = {
        "skill": skill_name,
        "version": version,
        "archive": archive_path.name,
        "sha256": archive_hash,
        "size_bytes": archive_path.stat().st_size,
    }
    manifest_path = DIST_DIR / f"{skill_name}-{version}.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"Packaged: {archive_path} (SHA-256: {archive_hash[:16]}...)")
    return archive_path, manifest_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Build and package Asynx Asset Skills")
    parser.add_argument("--skill", choices=list(MODALITIES.keys()) + ["all"], default="all")
    parser.add_argument("--package", action="store_true", help="Build zip packages and manifests in dist/")
    args = parser.parse_args()

    targets = list(MODALITIES.keys()) if args.skill == "all" else [args.skill]
    for target in targets:
        if args.package:
            package_skill(target)
        else:
            build_skill(target)


if __name__ == "__main__":
    main()
