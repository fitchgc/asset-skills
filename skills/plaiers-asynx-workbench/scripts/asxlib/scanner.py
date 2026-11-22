from __future__ import annotations

import json
import mimetypes
import os
import sqlite3
from pathlib import Path
from typing import Any

if __package__ and not __package__.startswith("src."):
    from .config import cache_path
    from .errors import AsxError
    from .state import (
        SCHEMA_VERSION,
        compute_file_sha256,
        connect_db,
        get_asset_by_id,
        get_asset_by_path,
        get_asset_lineage,
        list_all_assets,
        relink_moved_asset,
    )
else:
    from shared.asxlib.config import cache_path
    from shared.asxlib.errors import AsxError
    from shared.asxlib.state import (
        SCHEMA_VERSION,
        compute_file_sha256,
        connect_db,
        get_asset_by_id,
        get_asset_by_path,
        get_asset_lineage,
        list_all_assets,
        relink_moved_asset,
    )

ASSET_CATEGORIES = ("image", "audio", "frame_sequence", "video", "3d")

EXCLUDED_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        ".venv",
        "__pycache__",
        "dist",
        ".scratch",
        ".pytest_cache",
        ".pi",
        "venv",
        ".mypy_cache",
    }
)

IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tga"})
AUDIO_EXTENSIONS = frozenset({".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac"})
VIDEO_EXTENSIONS = frozenset({".mp4", ".webm", ".mov", ".mkv"})
MODEL3D_EXTENSIONS = frozenset({".glb", ".gltf", ".obj", ".fbx", ".usdz"})
FRAME_SEQUENCE_OPERATIONS = frozenset({"spritesheet", "frame_sequence", "render_sequence"})

_SHA256_MEMORY_CACHE: dict[str, tuple[int, int, str]] = {}


def _sha256_cache_file() -> Path:
    return cache_path().with_name("asset_scanner_sha256.json")


class _Sha256DigestCache:
    """Caches SHA-256 digests keyed by (canonical_path, mtime_ns, size)."""

    def __init__(self, *, use_cache: bool = True) -> None:
        self.use_cache = use_cache
        self._entries: dict[str, tuple[int, int, str]] = {}
        self._dirty = False
        if use_cache:
            self._entries.update(_SHA256_MEMORY_CACHE)
            self._load_disk()

    def _load_disk(self) -> None:
        path = _sha256_cache_file()
        try:
            if not path.is_file():
                return
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return
            for key, val in raw.items():
                if (
                    isinstance(key, str)
                    and isinstance(val, dict)
                    and isinstance(val.get("mtime_ns"), int)
                    and isinstance(val.get("size"), int)
                    and isinstance(val.get("sha256"), str)
                ):
                    self._entries[key] = (val["mtime_ns"], val["size"], val["sha256"])
        except (OSError, json.JSONDecodeError):
            return

    def peek(self, file_path: Path, mtime_ns: int, size: int) -> str | None:
        cached = self._entries.get(str(file_path))
        if cached is not None and cached[0] == mtime_ns and cached[1] == size:
            return cached[2]
        return None

    def get_or_compute(self, file_path: Path, mtime_ns: int, size: int) -> str | None:
        key = str(file_path)
        if self.use_cache:
            cached = self._entries.get(key)
            if cached is not None and cached[0] == mtime_ns and cached[1] == size:
                return cached[2]
        digest = compute_file_sha256(file_path)
        if digest is not None and self.use_cache:
            self._entries[key] = (mtime_ns, size, digest)
            _SHA256_MEMORY_CACHE[key] = (mtime_ns, size, digest)
            self._dirty = True
        return digest

    def flush(self) -> None:
        if not self.use_cache or not self._dirty:
            return
        path = _sha256_cache_file()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            serialized = {
                k: {"mtime_ns": v[0], "size": v[1], "sha256": v[2]}
                for k, v in self._entries.items()
            }
            path.write_text(json.dumps(serialized, ensure_ascii=False), encoding="utf-8")
            self._dirty = False
        except OSError:
            pass


def _classify_extension(suffix: str) -> str | None:
    ext = suffix.lower()
    if ext in IMAGE_EXTENSIONS:
        return "image"
    if ext in AUDIO_EXTENSIONS:
        return "audio"
    if ext in VIDEO_EXTENSIONS:
        return "video"
    if ext in MODEL3D_EXTENSIONS:
        return "3d"
    return None


def _parse_frame_sequence_manifest(manifest_path: Path) -> dict[str, Any] | None:
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    frames = raw.get("frames")
    if not isinstance(frames, (dict, list)):
        return None
    return raw


def _resolve_companion_atlas(
    manifest_path: Path, manifest_data: dict[str, Any], dir_files: dict[str, Path]
) -> Path | None:
    meta = manifest_data.get("meta")
    if isinstance(meta, dict):
        image_ref = meta.get("image")
        if isinstance(image_ref, str) and image_ref.strip():
            candidate = (manifest_path.parent / image_ref.strip()).resolve()
            if candidate.is_file() and candidate.suffix.lower() in IMAGE_EXTENSIONS:
                return candidate
            by_name = dir_files.get(Path(image_ref.strip()).name)
            if by_name is not None and by_name.suffix.lower() in IMAGE_EXTENSIONS:
                return by_name

    name = manifest_path.name
    if name.endswith(".manifest.json"):
        stem = name[: -len(".manifest.json")]
        for candidate_name in (f"{stem}.png", f"{stem}.webp", "atlas.png", "atlas.webp"):
            if candidate_name in dir_files:
                return dir_files[candidate_name]

    for default_name in ("atlas.png", "atlas.webp"):
        if default_name in dir_files:
            return dir_files[default_name]

    image_candidates = [
        p
        for fname, p in sorted(dir_files.items())
        if Path(fname).suffix.lower() in {".png", ".webp"}
    ]
    if len(image_candidates) == 1:
        return image_candidates[0]
    return None


def _extract_manifest_metadata(manifest_path: Path, manifest_data: dict[str, Any]) -> dict[str, Any]:
    frames = manifest_data.get("frames")
    frame_count = len(frames) if isinstance(frames, (dict, list)) else 0
    meta = manifest_data.get("meta") if isinstance(manifest_data.get("meta"), dict) else {}
    info: dict[str, Any] = {
        "manifest_path": str(manifest_path.resolve()),
        "frame_count": frame_count,
    }
    for key in ("fps", "columns", "rows", "app", "image"):
        if key in meta:
            info[key] = meta[key]
    if isinstance(meta.get("size"), dict):
        info["atlas_size"] = meta["size"]
    return info


def _extract_prompt(ledger_row: dict[str, Any]) -> str | None:
    req = ledger_row.get("request")
    if isinstance(req, dict):
        inp = req.get("input")
        if isinstance(inp, dict):
            for key in ("prompt", "text"):
                val = inp.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip()
        for key in ("prompt", "text"):
            val = req.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    meta = ledger_row.get("metadata")
    if isinstance(meta, dict):
        for key in ("prompt", "text"):
            val = meta.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return None


_DISCOVER_EXTENSIONS = (
    IMAGE_EXTENSIONS | AUDIO_EXTENSIONS | VIDEO_EXTENSIONS | MODEL3D_EXTENSIONS
)


def _discover_workspace_files(
    root_path: Path,
    extra_paths: frozenset[str] = frozenset(),
) -> tuple[list[Path], dict[str, dict[str, Any]]]:
    """Walk workspace root, skip excluded dirs, and pair frame sequence atlases."""
    discovered_files: list[Path] = []
    claimed_atlases: dict[str, dict[str, Any]] = {}

    for dirpath, dirnames, filenames in os.walk(root_path, followlinks=False):
        dirnames[:] = [d for d in sorted(dirnames) if d not in EXCLUDED_DIRS]
        current_dir = Path(dirpath)
        dir_file_map: dict[str, Path] = {}
        manifest_candidates: list[Path] = []

        for fname in sorted(filenames):
            # 只处理资产扩展名 / manifest / 账本已登记的文件，避免对无关文件 resolve+stat
            suffix = os.path.splitext(fname)[1].lower()
            is_manifest = fname == "manifest.json" or fname.endswith(".manifest.json")
            if (
                suffix not in _DISCOVER_EXTENSIONS
                and not is_manifest
                and os.path.join(dirpath, fname) not in extra_paths
            ):
                continue
            fpath = current_dir / fname
            if os.path.islink(fpath):
                fpath = fpath.resolve()
            if not fpath.is_file():
                continue
            dir_file_map[fname] = fpath
            if is_manifest:
                manifest_candidates.append(fpath)

        for manifest_path in manifest_candidates:
            manifest_data = _parse_frame_sequence_manifest(manifest_path)
            if manifest_data is None:
                continue
            atlas_path = _resolve_companion_atlas(manifest_path, manifest_data, dir_file_map)
            if atlas_path is None:
                continue
            atlas_key = str(atlas_path.resolve())
            if atlas_key not in claimed_atlases:
                claimed_atlases[atlas_key] = _extract_manifest_metadata(
                    manifest_path, manifest_data
                )

        for fname in sorted(dir_file_map):
            discovered_files.append(dir_file_map[fname])

    return discovered_files, claimed_atlases


def _load_lineage_maps(
    connection: sqlite3.Connection,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    rows = connection.execute("SELECT * FROM asset_lineage ORDER BY id").fetchall()
    parents_by_child_id: dict[str, list[dict[str, Any]]] = {}
    parents_by_child_path: dict[str, list[dict[str, Any]]] = {}
    children_by_parent_id: dict[str, list[dict[str, Any]]] = {}
    children_by_parent_path: dict[str, list[dict[str, Any]]] = {}

    for row in rows:
        edge = dict(zip(row.keys(), tuple(row)))
        c_id = edge.get("child_artifact_id")
        c_path = edge.get("child_path")
        p_id = edge.get("parent_artifact_id")
        p_path = edge.get("parent_path")
        if c_id:
            parents_by_child_id.setdefault(str(c_id), []).append(edge)
        if c_path:
            parents_by_child_path.setdefault(str(c_path), []).append(edge)
        if p_id:
            children_by_parent_id.setdefault(str(p_id), []).append(edge)
        if p_path:
            children_by_parent_path.setdefault(str(p_path), []).append(edge)

    return (
        parents_by_child_id,
        parents_by_child_path,
        children_by_parent_id,
        children_by_parent_path,
    )


def _dedupe_edges(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[int] = set()
    result: list[dict[str, Any]] = []
    for edge in edges:
        eid = int(edge.get("id") or 0)
        if eid and eid in seen:
            continue
        if eid:
            seen.add(eid)
        result.append(edge)
    return result


def _relink_if_moved(
    connection: sqlite3.Connection,
    file_path: Path,
    file_sha256: str | None,
    *,
    by_path: dict[str, dict[str, Any]],
    by_sha_missing: dict[str, list[dict[str, Any]]],
    claimed_artifact_ids: set[str],
    manifest_path: str | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    resolved = str(file_path)
    direct = by_path.get(resolved)
    if direct is not None:
        claimed_artifact_ids.add(str(direct["artifact_id"]))
        return direct, None

    if not file_sha256:
        return None, None

    for candidate in by_sha_missing.get(file_sha256, []):
        art_id = str(candidate["artifact_id"])
        if art_id in claimed_artifact_ids:
            continue
        old_path = str(candidate.get("path") or "")
        if old_path and Path(old_path).is_file():
            continue
        claimed_artifact_ids.add(art_id)
        relinked = relink_moved_asset(
            connection,
            art_id,
            resolved,
            sha256=file_sha256,
            manifest_path=manifest_path,
        )
        # Preserve joined task fields from candidate
        for task_key in (
            "remote_task_id",
            "task_operation",
            "billing",
            "model",
            "modality",
            "request",
            "base_url",
        ):
            if task_key in candidate:
                relinked[task_key] = candidate[task_key]
        by_path[resolved] = relinked
        return relinked, old_path

    return None, None


def scan_workspace(
    root: str | Path = ".",
    *,
    category: str = "all",
    managed_only: bool = False,
    external_only: bool = False,
    source: str = "all",
    query: str | None = None,
    connection: sqlite3.Connection | None = None,
    use_cache: bool = True,
    lazy_sha: bool = False,
) -> dict[str, Any]:
    """Scan a workspace directory, classify 5 asset categories, join ledger, and relink moved files."""
    if category != "all" and category not in ASSET_CATEGORIES:
        raise AsxError(
            f"Unsupported asset category: {category}",
            code="invalid_category",
            details={"allowed": ["all", *ASSET_CATEGORIES]},
        )
    if source not in {"all", "managed", "external"}:
        raise AsxError(
            f"Unsupported source filter: {source}",
            code="invalid_source_filter",
        )

    root_path = Path(root).expanduser().resolve()
    if not root_path.is_dir():
        raise AsxError(
            f"Workspace root directory does not exist: {root_path}",
            code="workspace_not_found",
            details={"root": str(root_path)},
        )

    sha_cache = _Sha256DigestCache(use_cache=use_cache)

    owns_conn = connection is None
    conn = connect_db() if connection is None else connection
    try:
        ledger_rows = list_all_assets(conn, state="available")
        discovered_files, claimed_atlases = _discover_workspace_files(
            root_path,
            frozenset(str(r.get("path")) for r in ledger_rows if r.get("path")),
        )
        by_path: dict[str, dict[str, Any]] = {}
        by_sha_missing: dict[str, list[dict[str, Any]]] = {}
        for row in ledger_rows:
            r_path = str(row.get("path") or "")
            if r_path and r_path not in by_path:
                by_path[r_path] = row
            r_sha = row.get("sha256")
            if isinstance(r_sha, str) and r_sha:
                if not r_path or not Path(r_path).is_file():
                    by_sha_missing.setdefault(r_sha, []).append(row)

        claimed_artifact_ids: set[str] = set()
        candidates: list[dict[str, Any]] = []

        for fpath in discovered_files:
            resolved_str = str(fpath)
            atlas_info = claimed_atlases.get(resolved_str)
            ext_category = "frame_sequence" if atlas_info is not None else _classify_extension(fpath.suffix)
            if ext_category is None:
                # Still check if it's directly indexed in ledger as an asset
                if resolved_str not in by_path:
                    continue
                ext_category = by_path[resolved_str].get("category") or by_path[resolved_str].get("modality")
                if ext_category not in ASSET_CATEGORIES:
                    continue

            try:
                st = fpath.stat()
            except OSError:
                continue

            if lazy_sha and (resolved_str in by_path or not by_sha_missing):
                # 无需 sha 即可确定账本归属：仅使用已缓存/账本中的值，不读文件内容
                file_sha = sha_cache.peek(fpath, st.st_mtime_ns, st.st_size) or (
                    (by_path.get(resolved_str) or {}).get("sha256")
                )
            else:
                file_sha = sha_cache.get_or_compute(fpath, st.st_mtime_ns, st.st_size)
            manifest_path_for_relink = atlas_info["manifest_path"] if atlas_info else None

            ledger_row, relinked_from = _relink_if_moved(
                conn,
                fpath,
                file_sha,
                by_path=by_path,
                by_sha_missing=by_sha_missing,
                claimed_artifact_ids=claimed_artifact_ids,
                manifest_path=manifest_path_for_relink,
            )

            # Determine final category: claimed atlas or ledger frame_sequence wins over static image
            final_category = ext_category
            if atlas_info is not None:
                final_category = "frame_sequence"
            elif ledger_row is not None:
                row_cat = ledger_row.get("category")
                row_op = ledger_row.get("operation") or ledger_row.get("task_operation")
                if row_cat == "frame_sequence" or row_op in FRAME_SEQUENCE_OPERATIONS:
                    final_category = "frame_sequence"

            candidates.append(
                {
                    "path": resolved_str,
                    "fpath": fpath,
                    "stat": st,
                    "sha256": file_sha,
                    "category": final_category,
                    "atlas_info": atlas_info,
                    "ledger_row": ledger_row,
                    "relinked_from": relinked_from,
                }
            )

        sha_cache.flush()

        (
            parents_by_child_id,
            parents_by_child_path,
            children_by_parent_id,
            children_by_parent_path,
        ) = _load_lineage_maps(conn)

        all_items: list[dict[str, Any]] = []
        counts_by_category = {cat: 0 for cat in ASSET_CATEGORIES}
        managed_count = 0
        external_count = 0
        relinked_count = 0

        for cand in candidates:
            resolved_str = cand["path"]
            fpath: Path = cand["fpath"]
            st = cand["stat"]
            final_category = cand["category"]
            atlas_info = cand["atlas_info"]
            ledger_row = cand["ledger_row"]
            relinked_from = cand["relinked_from"]

            counts_by_category[final_category] += 1
            if relinked_from:
                relinked_count += 1

            rel_path = fpath.relative_to(root_path).as_posix()
            guessed_mime, _ = mimetypes.guess_type(resolved_str)

            merged_meta: dict[str, Any] = {}
            if ledger_row is not None and isinstance(ledger_row.get("metadata"), dict):
                merged_meta.update(ledger_row["metadata"])
            if atlas_info is not None:
                merged_meta.update(atlas_info)

            if ledger_row is not None:
                managed = True
                managed_count += 1
                source_kind = str(ledger_row.get("source_kind") or "cloud_task")
                artifact_id = str(ledger_row["artifact_id"])
                local_task_id = str(ledger_row["local_task_id"])
                remote_task_id = ledger_row.get("remote_task_id")
                task_id = str(remote_task_id) if remote_task_id else local_task_id
                model = ledger_row.get("model")
                operation = ledger_row.get("operation") or ledger_row.get("task_operation")
                prompt = _extract_prompt(ledger_row)
                mime = ledger_row.get("mime") or guessed_mime
                created_at = ledger_row.get("created_at")
                updated_at = ledger_row.get("updated_at")

                raw_parents = (
                    parents_by_child_id.get(artifact_id, [])
                    + parents_by_child_path.get(resolved_str, [])
                )
                raw_children = (
                    children_by_parent_id.get(artifact_id, [])
                    + children_by_parent_path.get(resolved_str, [])
                )
            else:
                managed = False
                external_count += 1
                source_kind = "external"
                artifact_id = None
                local_task_id = None
                remote_task_id = None
                task_id = None
                model = None
                operation = None
                prompt = None
                mime = guessed_mime
                created_at = None
                updated_at = None

                raw_parents = parents_by_child_path.get(resolved_str, [])
                raw_children = children_by_parent_path.get(resolved_str, [])

            parents = _dedupe_edges(raw_parents)
            children = _dedupe_edges(raw_children)

            all_items.append(
                {
                    "path": resolved_str,
                    "relative_path": rel_path,
                    "name": fpath.name,
                    "ext": fpath.suffix.lower(),
                    "category": final_category,
                    "managed": managed,
                    "source_kind": source_kind,
                    "artifact_id": artifact_id,
                    "task_id": task_id,
                    "billing": ledger_row.get("billing") if ledger_row else None,
                    "local_task_id": local_task_id,
                    "remote_task_id": remote_task_id,
                    "model": model,
                    "operation": operation,
                    "prompt": prompt,
                    "mime": mime,
                    "bytes": st.st_size,
                    "mtime_ns": st.st_mtime_ns,
                    "sha256": cand["sha256"],
                    "parents": parents,
                    "children": children,
                    "metadata": merged_meta,
                    "created_at": created_at,
                    "updated_at": updated_at,
                    **({"relinked_from": relinked_from} if relinked_from else {}),
                }
            )
    finally:
        if owns_conn:
            conn.close()

    # Apply requested filters
    effective_source = source
    if managed_only:
        effective_source = "managed"
    elif external_only:
        effective_source = "external"

    filtered: list[dict[str, Any]] = []
    q = query.strip().casefold() if isinstance(query, str) and query.strip() else None
    for item in all_items:
        if category != "all" and item["category"] != category:
            continue
        if effective_source == "managed" and not item["managed"]:
            continue
        if effective_source == "external" and item["managed"]:
            continue
        if q is not None:
            haystack = " ".join(
                str(v)
                for v in (
                    item.get("name"),
                    item.get("relative_path"),
                    item.get("path"),
                    item.get("prompt"),
                    item.get("model"),
                    item.get("operation"),
                    item.get("task_id"),
                    item.get("artifact_id"),
                    item.get("category"),
                )
                if v
            ).casefold()
            if q not in haystack:
                continue
        filtered.append(item)

    return {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "root": str(root_path),
        "counts": {
            "total": len(all_items),
            "filtered": len(filtered),
            "managed": managed_count,
            "external": external_count,
            "relinked": relinked_count,
            "by_category": counts_by_category,
        },
        "assets": filtered,
    }


def _enrich_asset_summary(
    connection: sqlite3.Connection,
    *,
    artifact_id: str | None = None,
    path: str | None = None,
) -> dict[str, Any] | None:
    row: dict[str, Any] | None = None
    if artifact_id:
        row = get_asset_by_id(connection, artifact_id)
    if row is None and path:
        row = get_asset_by_path(connection, path)
    if row is None:
        return None

    task_row = connection.execute(
        "SELECT remote_task_id, operation AS task_operation, model, modality, request_json "
        "FROM tasks WHERE local_id = ?",
        (row["local_task_id"],),
    ).fetchone()
    enriched = dict(row)
    if task_row is not None:
        enriched["remote_task_id"] = task_row["remote_task_id"]
        enriched["task_id"] = task_row["remote_task_id"] or row["local_task_id"]
        enriched["model"] = task_row["model"]
        enriched["modality"] = task_row["modality"]
        enriched["task_operation"] = task_row["task_operation"]
        try:
            enriched["request"] = json.loads(task_row["request_json"])
        except (TypeError, json.JSONDecodeError):
            enriched["request"] = None
    enriched["managed"] = True
    enriched["prompt"] = _extract_prompt(enriched)
    p = str(enriched.get("path") or "")
    enriched["exists"] = bool(p and Path(p).is_file())
    enriched["name"] = Path(p).name if p else None
    return enriched


def asset_lineage_report(
    target: str | Path,
    *,
    root: str | Path | None = None,
    connection: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Return full upstream/downstream lineage and metadata for an artifact_id or file path."""
    raw = str(target).strip()
    if not raw:
        raise AsxError("Target artifact_id or path is required", code="invalid_lineage_target")

    owns_conn = connection is None
    conn = connect_db() if connection is None else connection
    try:
        # Resolve relative file path against root when provided
        lookup_target: str | Path = raw
        if get_asset_by_id(conn, raw) is None:
            candidate_path = Path(raw).expanduser()
            if not candidate_path.is_absolute() and root is not None:
                candidate_path = Path(root).expanduser().resolve() / candidate_path
            resolved_candidate = candidate_path.resolve()
            if resolved_candidate.is_file():
                # If moved and not yet scanned, attempt SHA-256 relinking on the spot
                if get_asset_by_path(conn, resolved_candidate) is None:
                    file_sha = compute_file_sha256(resolved_candidate)
                    if file_sha:
                        missing_rows = conn.execute(
                            "SELECT * FROM assets WHERE sha256 = ? AND state = 'available' "
                            "ORDER BY updated_at DESC, rowid DESC",
                            (file_sha,),
                        ).fetchall()
                        for m_row in missing_rows:
                            old_p = str(m_row["path"] or "")
                            if not old_p or not Path(old_p).is_file():
                                relink_moved_asset(
                                    conn,
                                    str(m_row["artifact_id"]),
                                    resolved_candidate,
                                    sha256=file_sha,
                                )
                                break
                lookup_target = resolved_candidate

        dag = get_asset_lineage(conn, lookup_target)
        enriched_asset = None
        if dag.get("asset") is not None:
            enriched_asset = _enrich_asset_summary(
                conn,
                artifact_id=str(dag["asset"]["artifact_id"]),
                path=dag["path"],
            )
        elif not dag["parents"] and not dag["children"] and not Path(dag["path"]).exists():
            raise AsxError(f"Asset not found: {raw}", code="asset_not_found")

        return {
            "ok": True,
            "schema_version": SCHEMA_VERSION,
            "target": raw,
            "path": dag["path"],
            "asset": enriched_asset,
            "parents": dag["parents"],
            "children": dag["children"],
            "ancestors": dag["ancestors"],
            "descendants": dag["descendants"],
        }
    finally:
        if owns_conn:
            conn.close()
