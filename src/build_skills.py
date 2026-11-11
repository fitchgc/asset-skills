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
        "skill_name": "asynx-image-pro",
        "src_module": "image",
    },
    "audio": {
        "skill_name": "asynx-audio",
        "src_module": "audio",
    },
    "video": {
        "skill_name": "asynx-video",
        "src_module": "video",
    },
    "3d": {
        "skill_name": "asynx-3d",
        "src_module": "model3d",
    },
}


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

    # 2. Copy shared/vendor to scripts/vendor if present
    shared_vendor = SHARED_DIR / "vendor"
    target_vendor = scripts_dir / "vendor"
    if shared_vendor.exists() and not target_vendor.exists():
        shutil.copytree(shared_vendor, target_vendor)

    # 3. Copy shared/asxlib/ to scripts/asxlib/
    for py_file in (SHARED_DIR / "asxlib").glob("*.py"):
        shutil.copy2(py_file, asxlib_dir / py_file.name)

    # Create __init__.py in asxlib if missing
    init_py = asxlib_dir / "__init__.py"
    if not init_py.exists():
        init_py.write_text('"""Asynx skill library."""\n', encoding="utf-8")

    # 4. Copy modality-specific modules from src/<modality>/
    modality_src = SRC_DIR / src_module_name
    if modality_src.exists():
        for py_file in modality_src.glob("*.py"):
            shutil.copy2(py_file, asxlib_dir / py_file.name)

    # 5. Copy UI bundle if present for this modality
    ui_src = SRC_DIR / "ui" / modality_key
    target_ui = asxlib_dir / "ui"
    if ui_src.exists():
        if target_ui.exists():
            shutil.rmtree(target_ui)
        shutil.copytree(ui_src, target_ui)

    # 6. Generate .asx-install.json receipt
    version = get_version()
    file_hashes: dict[str, str] = {}
    for path in sorted(skill_dir.rglob("*")):
        if path.is_file() and not path.name.startswith("."):
            rel_path = path.relative_to(skill_dir).as_posix()
            file_hashes[rel_path] = file_sha256(path)

    receipt = {
        "files": file_hashes,
        "repository": "dawnx/asynx-skills",
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
            if path.is_file():
                rel_path = path.relative_to(skill_dir).as_posix()
                zf.write(path, f"{skill_name}/{rel_path}")

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
