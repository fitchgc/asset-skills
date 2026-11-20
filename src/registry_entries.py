#!/usr/bin/env python3
"""Emit Plaiers skill-registry publish entries for the built packages (issue 08). Publishes nothing.

For each package this writes ``dist/registry/<slug>.json`` = ``{metadata, files}``, the input accepted
by Plaiers ``scripts/manage-skill-registry.mjs publish``. It also writes ``dist/registry/size-report.md``.
The validation mirrors the publisher (``lib/skill-registry-publish.mjs`` ``validateEntry`` plus
``safeSkillPath`` and the 4 MiB payload limit) and adds a credential scan, so a bad package fails
here, before anything is uploaded.

IDs follow the namespace confirmed on PR #10 (Q2): ``plaiers/asynx-*`` with ``name`` and the installed
directory ``plaiers-asynx-*``, which is also the package directory name under ``skills/``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src import build_skills  # noqa: E402

# Registry ID = ID_PREFIX + slug; name / directory = NAME_PREFIX + slug (PR #10 Q2).
ID_PREFIX = "plaiers/"
NAME_PREFIX = "plaiers-"


def slug_for(key: str) -> str:
    return build_skills.MODALITIES[key]["skill_name"].removeprefix(NAME_PREFIX)
REGISTRY_DIR = build_skills.DIST_DIR / "registry"
DEV_ORIGIN = "https://dev.plaiers.ai"
AUTHOR = "Asynx Asset Skills (fitchgc/asset-skills)"
SOURCE = "https://github.com/fitchgc/asset-skills"
LICENSE = "UNLICENSED"  # the repository has no LICENSE file yet; the owner decides before publishing (issue 12)

PACKAGES: dict[str, dict[str, Any]] = {
    "image": {"title": "Asynx 图片生成与编辑", "keywords": ["asynx", "image", "图片", "生成", "编辑", "参考图", "批量"],
              "capabilities": ["filesystem", "terminal", "network"],
              "scope": "image generate/edit/query/download via Asset Gateway; local workbench needs the workbench package"},
    "audio": {"title": "Asynx 音频生成", "keywords": ["asynx", "audio", "音频", "音效", "音乐", "语音"],
              "capabilities": ["filesystem", "terminal", "network"],
              "scope": "sfx/music/speech generate/query/download; local audio processing needs the workbench package"},
    "video": {"title": "Asynx 视频生成", "keywords": ["asynx", "video", "视频", "文生视频", "图生视频"],
              "capabilities": ["filesystem", "terminal", "network"],
              "scope": "video generate/edit/query/download; frame/atlas workbench needs the workbench package"},
    "3d": {"title": "Asynx 3D 资产", "keywords": ["asynx", "3d", "模型", "绑定", "动画", "rig"],
           "capabilities": ["filesystem", "terminal", "network"],
           "scope": "3D generate/rig-check/rig/animate/convert/query/download; viewer needs the workbench package"},
    "asset_manager": {"title": "Asynx 资产管理与本地工作台", "keywords": ["asynx", "asset", "资产", "工作台", "workbench", "血缘", "本地处理"],
                      "capabilities": ["filesystem", "terminal", "network", "browser"],
                      "scope": "asset manager, Local UI Workbench and local post-processing for all modalities (workbench package)"},
}

_ID_RE = re.compile(r"^plaiers/[a-z0-9]+(?:-[a-z0-9]+)*$")
_SECRET_PATTERNS = (
    ("sagein_pat_", re.compile(r"sagein_pat_[A-Za-z0-9_-]{8,}")),
    ("cg_pat_", re.compile(r"cg_pat_[A-Za-z0-9_-]{8,}")),
    ("JWT", re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("asx- key", re.compile(r"\basx-[A-Za-z0-9]{24,}\b")),
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
)


class RegistryEntryError(SystemExit):
    pass


def safe_skill_path(path: str) -> bool:
    """Mirror of Plaiers ``safeSkillPath``."""
    return (0 < len(path) <= 240 and not re.search(r"[\\%\x00-\x1f\x7f:]", path)
            and all(part and part not in {".", ".."} and not part.startswith(".") for part in path.split("/")))


def js_json(value: Any) -> str:
    """Byte-identical to JavaScript ``JSON.stringify(value)`` for the plain JSON used here."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def predicted_version(entry: dict[str, Any], origin: str = DEV_ORIGIN) -> str:
    """Version the publisher will assign on ``origin``. It normalizes CRLF and production origins first."""
    files = {
        path: text.replace("\r\n", "\n").replace("https://sagein.tanqunzhao.chatgpt.site", origin).replace("https://plaiers.ai", origin)
        for path, text in sorted(entry["files"].items())
    }
    return hashlib.sha256(js_json({"metadata": entry["metadata"], "files": files}).encode("utf-8")).hexdigest()


def _frontmatter_description(text: str) -> str:
    match = re.search(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    found = re.search(r"^description:\s*(.+)$", match.group(1), re.MULTILINE) if match else None
    if not found:
        raise RegistryEntryError("SKILL.md has no frontmatter description")
    return found.group(1).strip()



def validate_entry(entry: dict[str, Any]) -> None:
    """Mirror of the publisher's checks, plus a credential scan. Raises RegistryEntryError."""
    m = entry.get("metadata")
    problems: list[str] = []
    if not isinstance(m, dict) or not isinstance(m.get("id"), str) or not _ID_RE.match(m["id"]) or len(m["id"]) > 100 \
            or m.get("name") != m["id"].replace("/", "-", 1):
        raise RegistryEntryError("Invalid namespaced skill ID/name")
    if m.get("kind") != "capability":
        problems.append("kind must be capability")
    for key in ("title", "description", "author", "source", "license"):
        value = m.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > 2000:
            problems.append(f"Invalid metadata {key}")
    requirements = m.get("requirements") or {}
    for key, value in (("keywords", m.get("keywords")), ("gameTypes", m.get("gameTypes", [])), ("scenarios", m.get("scenarios", [])),
                       ("targets", requirements.get("targets")), ("capabilities", requirements.get("capabilities"))):
        if not isinstance(value, list) or len(value) > 100 or any(not isinstance(v, str) or not v or len(v) > 100 for v in value):
            problems.append(f"Invalid metadata {key}")
    verification = m.get("verification")
    if m.get("visibility") != "public" or not isinstance(requirements.get("paidServices"), bool) \
            or not isinstance(verification, dict) or not isinstance(verification.get("status"), str):
        problems.append("Only explicitly reviewed public metadata is supported")
    files = entry.get("files")
    if not isinstance(files, dict) or len(files) > 200:
        raise RegistryEntryError(f"{m['id']}: Invalid skill files")
    for path, content in files.items():
        if not safe_skill_path(path):
            problems.append(f"Unsupported package file: {path}")
        if not isinstance(content, str):
            problems.append(f"Non-text file: {path}")
            continue
        for label, pattern in _SECRET_PATTERNS:
            if pattern.search(content):
                problems.append(f"Credential-looking string ({label}) in {path}")
    if f"name: {m['name']}\n" not in str(files.get("SKILL.md", "")):
        problems.append(f"Wrong skill entrypoint {m['name']}")
    text_files = {path: content for path, content in files.items() if isinstance(content, str)}
    if len(js_json({"metadata": m, "files": text_files}).encode("utf-8")) > 4 * 1024 * 1024:
        problems.append("Package too large")
    if problems:
        raise RegistryEntryError(f"{m['id']}: " + "; ".join(problems))


def entry_for(key: str, skill_dir: Path) -> dict[str, Any]:
    info = PACKAGES[key]
    slug = slug_for(key)
    skill_id, name = ID_PREFIX + slug, NAME_PREFIX + slug
    if name != skill_dir.name:
        raise RegistryEntryError(f"{skill_id}: package directory {skill_dir.name} != {name}")
    files: dict[str, str] = {}
    for path in sorted(skill_dir.rglob("*")):
        if not path.is_file() or build_skills._is_cache_artifact(path):
            continue
        rel = path.relative_to(skill_dir).as_posix()
        if rel == ".asx-install.json":  # local install receipt; registry packages cannot carry dotfiles
            continue
        try:
            files[rel] = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise RegistryEntryError(f"{skill_id}: Non-text file: {rel}") from exc
    if f"name: {name}\n" not in files["SKILL.md"].split("---", 2)[1] + "\n":
        raise RegistryEntryError(f"{skill_id}: SKILL.md frontmatter name must be {name}")
    metadata = {
        "id": skill_id,
        "name": name,
        "kind": "capability",
        "title": info["title"],
        "description": _frontmatter_description(files["SKILL.md"]),
        "author": AUTHOR,
        "source": SOURCE,
        "license": LICENSE,
        "keywords": info["keywords"],
        "visibility": "public",
        "requirements": {"targets": [], "capabilities": info["capabilities"], "paidServices": True},
        "verification": {"status": "dev-preview", "scope": info["scope"]},
    }
    entry = {"metadata": metadata, "files": files}
    validate_entry(entry)
    return entry


def _runtime_rows() -> list[str]:
    from shared.asxlib.runtime_lock import LOCK

    rows = []
    for wheel in LOCK["wheels"]:
        rows.append(f"| `{wheel['filename']}` | {wheel['size']:,} |")
    return rows


def build_registry_entries(out_dir: Path = REGISTRY_DIR, *, rebuild: bool = True) -> list[dict[str, Any]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    report: list[dict[str, Any]] = []
    for key in PACKAGES:
        skill_dir = build_skills.build_skill(key) if rebuild else build_skills.SKILLS_DIR / build_skills.MODALITIES[key]["skill_name"]
        entry = entry_for(key, skill_dir)
        slug = slug_for(key)
        text = json.dumps(entry, ensure_ascii=False, indent=1) + "\n"
        (out_dir / f"{slug}.json").write_text(text, encoding="utf-8")
        report.append({"id": entry["metadata"]["id"], "name": entry["metadata"]["name"], "files": len(entry["files"]),
                       "payload_bytes": len(js_json(entry).encode("utf-8")), "entry_bytes": len(text.encode("utf-8")),
                       "dev_version": predicted_version(entry)})
    from shared.asxlib.runtime_lock import LOCK

    lines = [
        "# Registry entry size report",
        "",
        f"Generated by `src/registry_entries.py` for package version {build_skills.get_version()}. Nothing was published.",
        f"Limits: ≤ 200 files and ≤ 4 MiB payload per package, UTF-8 text only. `dev_version` is the expected registry version on {DEV_ORIGIN}.",
        "",
        "| ID | Files | Payload bytes | Entry file bytes | dev_version |",
        "| --- | ---: | ---: | ---: | --- |",
        *[f"| `{r['id']}` | {r['files']} | {r['payload_bytes']:,} | {r['entry_bytes']:,} | `{r['dev_version'][:16]}…` |" for r in report],
        "",
        f"## Shared runtime ({LOCK['requirement']}, not in any package)",
        "",
        "Downloaded once per (OS, architecture, Python) by `runtime prepare`. Wheel download sizes from the hash lock:",
        "",
        "| Wheel | Bytes |",
        "| --- | ---: |",
        *_runtime_rows(),
        "",
    ]
    (out_dir / "size-report.md").write_text("\n".join(lines), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Emit Plaiers registry publish entries (no upload)")
    parser.add_argument("--out-dir", type=Path, default=REGISTRY_DIR)
    args = parser.parse_args()
    for row in build_registry_entries(args.out_dir):
        print(f"{row['id']}: {row['files']} files, {row['payload_bytes']:,} bytes, dev version {row['dev_version']}")


if __name__ == "__main__":
    main()
