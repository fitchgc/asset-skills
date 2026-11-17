from __future__ import annotations

import importlib
import importlib.util
import json
import math
import secrets
import sys
from pathlib import Path
from typing import Any, Callable

if __package__ and not __package__.startswith("src."):
    from .errors import AsxError
    from .state import connect_db, new_local_task_id, record_artifact_with_lineage
    from .tasks import run_task_submission
else:
    from shared.asxlib.errors import AsxError
    from shared.asxlib.state import connect_db, new_local_task_id, record_artifact_with_lineage
    from shared.asxlib.tasks import run_task_submission


_CROSS_MODAL_SOURCE_SUBDIR = {
    "images": "image",
    "local_images": "image",
    "image_results": "image",
    "reference_media": "image",
    "audio_tasks": "audio",
    "local_audio": "audio",
    "video_tasks": "video",
    "local_video": "video",
    "model3d_tasks": "model3d",
    "local_model3d": "model3d",
}


_RIG_CHECK_CACHE: dict[tuple[str, int, int], dict[str, Any]] = {}


def _load_cross_module(module_name: str) -> Any:
    """Import sibling modality module in built skill mode or dev monorepo mode."""
    if __package__ and not __package__.startswith("src."):
        return importlib.import_module(f".{module_name}", package=__package__)

    target_pkg = "shared.asxlib"
    full_name = f"{target_pkg}.{module_name}"
    if full_name in sys.modules:
        return sys.modules[full_name]

    subdir = _CROSS_MODAL_SOURCE_SUBDIR[module_name]
    repo_root = Path(__file__).resolve().parents[2]
    module_path = repo_root / "src" / subdir / f"{module_name}.py"
    if not module_path.is_file():
        raise AsxError(f"Cross-modal module not found: {module_path}", code="module_not_found")

    spec = importlib.util.spec_from_file_location(full_name, module_path)
    if spec is None or spec.loader is None:
        raise AsxError(f"Unable to load cross-modal module: {module_name}", code="module_load_failed")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _parse_int(value: Any, name: str, *, default: int | None = None) -> int | None:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        raise AsxError(
            f"Parameter '{name}' must be an integer",
            code="invalid_parameters",
            exit_code=2,
        )
    try:
        return int(value)
    except (ValueError, TypeError) as exc:
        raise AsxError(
            f"Parameter '{name}' must be an integer",
            code="invalid_parameters",
            exit_code=2,
        ) from exc


def _parse_float(value: Any, name: str, *, default: float | None = None) -> float | None:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        raise AsxError(
            f"Parameter '{name}' must be a number",
            code="invalid_parameters",
            exit_code=2,
        )
    try:
        parsed = float(value)
    except (ValueError, TypeError) as exc:
        raise AsxError(
            f"Parameter '{name}' must be a number",
            code="invalid_parameters",
            exit_code=2,
        ) from exc
    if not math.isfinite(parsed):
        raise AsxError(
            f"Parameter '{name}' must be a finite number",
            code="invalid_parameters",
            exit_code=2,
        )
    return parsed


def confine_workspace_path(raw_path: str | Path, workspace_root: str | Path) -> Path:
    """Resolve raw_path relative to workspace_root and reject escapes outside workspace_root."""
    root = Path(workspace_root).expanduser().resolve()
    raw = Path(str(raw_path).strip()).expanduser()
    candidate = (root / raw).resolve() if not raw.is_absolute() else raw.resolve()
    if not candidate.is_relative_to(root):
        raise AsxError(
            f"Path is outside workspace root: {raw_path}",
            code="path_outside_workspace",
            details={"path": str(raw_path), "workspace_root": str(root)},
        )
    return candidate


def _require_existing_file(raw_path: Any, workspace_root: Path, *, field: str = "source_path") -> Path:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise AsxError(f"Missing required {field}", code=f"missing_{field}")
    resolved = confine_workspace_path(raw_path, workspace_root)
    if not resolved.is_file():
        raise AsxError(f"File not found: {resolved}", code="file_not_found")
    return resolved


def _resolve_output_dir(workspace_root: Path, body: dict[str, Any], *, subdir: str = "generated-assets") -> Path:
    raw_dir = body.get("output_dir")
    if isinstance(raw_dir, str) and raw_dir.strip():
        dest = confine_workspace_path(raw_dir, workspace_root)
    else:
        dest = (workspace_root / subdir).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def _resolve_output_file(
    workspace_root: Path,
    body: dict[str, Any],
    *,
    default_stem: str,
    default_ext: str,
) -> Path:
    raw_out = body.get("output_path")
    if isinstance(raw_out, str) and raw_out.strip():
        out_path = confine_workspace_path(raw_out, workspace_root)
    else:
        dest_dir = _resolve_output_dir(workspace_root, body)
        ext = default_ext.lstrip(".")
        out_path = dest_dir / f"{default_stem}_{secrets.token_hex(4)}.{ext}"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    return out_path


def _resolve_sequence_dir(workspace_root: Path, body: dict[str, Any], *, stem: str) -> Path:
    raw_dir = body.get("output_dir")
    if isinstance(raw_dir, str) and raw_dir.strip():
        dest = confine_workspace_path(raw_dir, workspace_root)
    else:
        dest = (workspace_root / "generated-assets" / f"{stem}_seq_{secrets.token_hex(4)}").resolve()
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def _slice_image_to_frame_sequence(
    source_image: Path,
    dest_dir: Path,
    *,
    rows: int = 2,
    columns: int = 2,
    fps: float = 10.0,
    frame_width: int | None = None,
    frame_height: int | None = None,
    remove_background: bool = False,
    threshold: int = 30,
    modality: str = "image",
    operation: str = "frame_sequence",
) -> dict[str, Any]:
    if rows <= 0 or columns <= 0:
        raise AsxError("rows and columns must be positive integers", code="invalid_slice_grid")
    if fps <= 0:
        raise AsxError("fps must be positive", code="invalid_fps")

    local_images = _load_cross_module("local_images")
    pil_image_mod = local_images._pillow()[0]

    img, resolved_src, _fmt, _frames = local_images._open_image(str(source_image))
    rgba = img.convert("RGBA")

    if remove_background:
        bg = rgba.getpixel((0, 0))
        bg_r, bg_g, bg_b = bg[0], bg[1], bg[2]
        pixels = rgba.load()
        for y in range(rgba.height):
            for x in range(rgba.width):
                pr, pg, pb, _pa = pixels[x, y]
                if math.sqrt((pr - bg_r) ** 2 + (pg - bg_g) ** 2 + (pb - bg_b) ** 2) < threshold:
                    pixels[x, y] = (pr, pg, pb, 0)

    cell_w = frame_width or max(1, rgba.width // columns)
    cell_h = frame_height or max(1, rgba.height // rows)

    atlas_w = columns * cell_w
    atlas_h = rows * cell_h
    atlas_img = pil_image_mod.new("RGBA", (atlas_w, atlas_h), (0, 0, 0, 0))

    manifest_frames: dict[str, Any] = {}
    frame_duration = round(1.0 / fps, 3)
    idx = 0
    for r in range(rows):
        top = r * rgba.height // rows
        bottom = (r + 1) * rgba.height // rows
        for c in range(columns):
            left = c * rgba.width // columns
            right = (c + 1) * rgba.width // columns
            tile = rgba.crop((left, top, right, bottom))
            if (tile.width, tile.height) != (cell_w, cell_h):
                tile = tile.resize((cell_w, cell_h), pil_image_mod.Resampling.LANCZOS)
            ax = c * cell_w
            ay = r * cell_h
            atlas_img.paste(tile, (ax, ay))
            frame_name = f"frame-{idx:04d}.png"
            manifest_frames[frame_name] = {
                "frame": {"x": ax, "y": ay, "w": cell_w, "h": cell_h},
                "rotated": False,
                "trimmed": False,
                "spriteSourceSize": {"x": 0, "y": 0, "w": cell_w, "h": cell_h},
                "sourceSize": {"w": cell_w, "h": cell_h},
                "duration": frame_duration,
            }
            idx += 1

    dest_dir.mkdir(parents=True, exist_ok=True)
    atlas_path = (dest_dir / "atlas.png").resolve()
    manifest_path = (dest_dir / "manifest.json").resolve()
    atlas_img.save(atlas_path, format="PNG")

    manifest = {
        "frames": manifest_frames,
        "meta": {
            "app": "asynx-asset-manager",
            "version": "1.0",
            "image": "atlas.png",
            "format": "RGBA8888",
            "size": {"w": atlas_w, "h": atlas_h},
            "scale": 1,
            "frame_count": idx,
            "columns": columns,
            "rows": rows,
            "fps": fps,
        },
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    conn = connect_db()
    try:
        record_artifact_with_lineage(
            conn,
            file_path=str(atlas_path),
            modality=modality,
            operation=operation,
            source_kind="local_process",
            category="frame_sequence",
            mime="image/png",
            size_bytes=atlas_path.stat().st_size,
            metadata={
                "manifest_path": str(manifest_path),
                "frame_count": idx,
                "columns": columns,
                "rows": rows,
                "fps": fps,
                "frame_size": {"w": cell_w, "h": cell_h},
                "atlas_size": {"w": atlas_w, "h": atlas_h},
            },
            parent_paths=[str(resolved_src)],
        )
    finally:
        conn.close()

    return {
        "source": str(resolved_src),
        "atlas_path": str(atlas_path),
        "manifest_path": str(manifest_path),
        "frame_count": idx,
        "columns": columns,
        "rows": rows,
        "fps": fps,
        "frame_size": {"w": cell_w, "h": cell_h},
        "atlas_size": {"w": atlas_w, "h": atlas_h},
    }


def _find_companion_manifest(atlas_path: Path, body: dict[str, Any], workspace_root: Path) -> Path | None:
    raw_manifest = body.get("manifest_path")
    if isinstance(raw_manifest, str) and raw_manifest.strip():
        candidate = confine_workspace_path(raw_manifest, workspace_root)
        if candidate.is_file():
            return candidate
    for cand in (
        atlas_path.with_name("manifest.json"),
        atlas_path.with_suffix(".manifest.json"),
    ):
        if cand.is_file():
            return cand
    return None


def _run_cloud_task_and_attach(
    client: Any,
    *,
    task_type: str,
    body: dict[str, Any],
    model: str,
    output_dir: Path,
    modality: str,
    parent_paths: list[str],
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    idem = f"asx-mgr-{task_type.replace('.', '-')}-{secrets.token_hex(6)}"
    payload, exit_code = run_task_submission(
        client,
        task_type=task_type,
        body=body,
        model=model,
        output_dir=str(output_dir),
        idempotency_key=idem,
        modality=modality,
        parent_paths=parent_paths,
        metadata=metadata,
    )
    if not payload.get("ok") or exit_code != 0:
        err = payload.get("error") or {"code": "task_failed", "message": "Task execution failed"}
        raise AsxError(
            str(err.get("message") or "Task execution failed"),
            code=str(err.get("code") or "task_failed"),
            details={"result": payload},
        )
    return payload


def dispatch_manager_action(
    body: dict[str, Any],
    *,
    workspace_root: str | Path,
    require_client: Callable[[], Any],
) -> dict[str, Any]:
    action = str(body.get("action") or "").strip()
    if not action:
        raise AsxError("Missing action name", code="missing_action")

    root = Path(workspace_root).expanduser().resolve()
    source_path = _require_existing_file(body.get("source_path"), root, field="source_path")

    # ------------------------------------------------------------------
    # Image actions
    # ------------------------------------------------------------------
    if action == "image_edit":
        client = require_client()
        prompt = str(body.get("prompt") or "").strip()
        if not prompt:
            raise AsxError("prompt is required for image_edit", code="invalid_prompt")
        mask_path: str | None = None
        if body.get("mask_path"):
            mask_path = str(_require_existing_file(body["mask_path"], root, field="mask_path"))

        images_mod = _load_cross_module("images")
        references = [str(source_path)]
        req_body, resolved_model, _req_id = images_mod.build_task(
            client,
            task_type="image.edit",
            model_selector=body.get("model"),
            prompt=prompt,
            image_size=body.get("image_size"),
            aspect_ratio=body.get("aspect_ratio"),
            quality=body.get("quality"),
            count=_parse_int(body.get("count") or 1, "count", default=1),
            output_format=body.get("output_format"),
            references=references,
            mask=mask_path,
        )
        out_dir = _resolve_output_dir(root, body)
        parent_paths = [str(source_path), *([mask_path] if mask_path else [])]
        res = _run_cloud_task_and_attach(
            client,
            task_type="image.edit",
            body=req_body,
            model=resolved_model,
            output_dir=out_dir,
            modality="image",
            parent_paths=parent_paths,
            metadata={"prompt": prompt},
        )
        return {
            "ok": True,
            "action": action,
            "created_files": list(res.get("files") or []),
            "result": res,
        }

    if action == "image_local_tool":
        local_images = _load_cross_module("local_images")
        tool = str(body.get("tool") or "").strip().lower().replace("_", "-")
        fmt = body.get("output_format") or body.get("format")
        default_ext = str(fmt or ("png" if tool in {"remove-background", "trim", "pad", "alpha-bleed"} else source_path.suffix.lstrip(".") or "png")).lower()
        if default_ext == "jpeg":
            default_ext = "jpg"
        out_file = _resolve_output_file(
            root,
            body,
            default_stem=f"{source_path.stem}_{tool.replace('-', '_') or 'edited'}",
            default_ext=default_ext,
        )

        if tool == "remove-background":
            res = local_images._remove_background(
                str(source_path), str(out_file), _parse_int(body.get("threshold") or 30, "threshold", default=30), fmt
            )
        elif tool == "trim":
            res = local_images._trim_transparent(str(source_path), str(out_file), fmt)
        elif tool == "pad":
            res = local_images._pad(
                str(source_path),
                str(out_file),
                _parse_int(body.get("padding"), "padding"),
                _parse_int(body.get("width"), "width"),
                _parse_int(body.get("height"), "height"),
                str(body.get("background") or "transparent"),
                fmt,
            )
        elif tool == "resize":
            res = local_images._resize(
                str(source_path),
                str(out_file),
                _parse_int(body.get("width"), "width"),
                _parse_int(body.get("height"), "height"),
                fmt,
            )
        elif tool == "crop":
            box = str(body.get("box") or "").strip()
            if not box:
                raise AsxError("box is required for crop (left,top,right,bottom)", code="invalid_crop_box")
            res = local_images._crop(str(source_path), str(out_file), box, fmt)
        elif tool == "convert":
            img, _src, _fmt_name, _frames = local_images._open_image(str(source_path))
            res = local_images._save(img, str(out_file), fmt or default_ext)
        elif tool == "alpha-bleed":
            res = local_images._alpha_bleed_cleanup(str(source_path), str(out_file), fmt)
        else:
            raise AsxError(f"Unsupported local image tool: {tool}", code="unsupported_tool")

        local_images._record_local_images(
            [res],
            operation=tool,
            parents=[str(source_path)],
            metadata={k: res[k] for k in ("width", "height", "format", "box") if k in res},
        )
        return {
            "ok": True,
            "action": action,
            "tool": tool,
            "created_files": [res["path"]],
            "result": {"image": res},
        }

    if action == "image_grid_slice":
        seq_dir = _resolve_sequence_dir(root, body, stem=source_path.stem)
        atlas_res = _slice_image_to_frame_sequence(
            source_path,
            seq_dir,
            rows=_parse_int(body.get("rows") or 2, "rows", default=2),
            columns=_parse_int(body.get("columns") or 2, "columns", default=2),
            fps=_parse_float(body.get("fps") or 10.0, "fps", default=10.0),
            frame_width=_parse_int(body.get("frame_width") or None, "frame_width"),
            frame_height=_parse_int(body.get("frame_height") or None, "frame_height"),
            remove_background=bool(body.get("remove_background", False)),
            threshold=_parse_int(body.get("threshold") or 30, "threshold", default=30),
            modality="image",
            operation="frame_sequence",
        )
        return {
            "ok": True,
            "action": action,
            "created_files": [atlas_res["atlas_path"]],
            "atlas": atlas_res,
            "result": {"atlas": atlas_res},
        }

    if action == "image_ai_spritesheet":
        client = require_client()
        rows = _parse_int(body.get("rows") or 2, "rows", default=2)
        columns = _parse_int(body.get("columns") or 2, "columns", default=2)
        fps = _parse_float(body.get("fps") or 10.0, "fps", default=10.0)
        user_prompt = str(body.get("prompt") or "character walk animation cycle").strip()
        grid_prompt = (
            f"{user_prompt}. Generate a clean {columns}x{rows} uniform sprite sheet grid "
            f"({rows} rows by {columns} columns) with consistent proportions and centered frames."
        )
        seq_dir = _resolve_sequence_dir(root, body, stem=f"{source_path.stem}_ai")
        images_mod = _load_cross_module("images")
        task_type = str(body.get("task_type") or "image.edit")
        req_body, resolved_model, _req_id = images_mod.build_task(
            client,
            task_type=task_type,
            model_selector=body.get("model"),
            prompt=grid_prompt,
            image_size=body.get("image_size"),
            aspect_ratio=body.get("aspect_ratio"),
            quality=body.get("quality"),
            count=1,
            output_format="png",
            references=[str(source_path)],
        )
        sheet_res = _run_cloud_task_and_attach(
            client,
            task_type=task_type,
            body=req_body,
            model=resolved_model,
            output_dir=seq_dir,
            modality="image",
            parent_paths=[str(source_path)],
            metadata={"prompt": grid_prompt, "rows": rows, "columns": columns},
        )
        sheet_files = list(sheet_res.get("files") or [])
        if not sheet_files:
            raise AsxError("AI sprite sheet task produced no output file", code="missing_task_assets")
        sheet_path = Path(sheet_files[0]).resolve()
        atlas_res = _slice_image_to_frame_sequence(
            sheet_path,
            seq_dir,
            rows=rows,
            columns=columns,
            fps=fps,
            frame_width=_parse_int(body.get("frame_width") or None, "frame_width"),
            frame_height=_parse_int(body.get("frame_height") or None, "frame_height"),
            remove_background=bool(body.get("remove_background", False)),
            threshold=_parse_int(body.get("threshold") or 30, "threshold", default=30),
            modality="image",
            operation="frame_sequence",
        )
        return {
            "ok": True,
            "action": action,
            "sheet_path": str(sheet_path),
            "atlas": atlas_res,
            "created_files": [atlas_res["atlas_path"], str(sheet_path)],
            "result": {"sheet_task": sheet_res, "atlas": atlas_res},
        }

    if action in {"image_to_video", "video_generate"}:
        client = require_client()
        video_tasks = _load_cross_module("video_tasks")
        end_image = None
        if body.get("end_image_path"):
            end_image = str(_require_existing_file(body["end_image_path"], root, field="end_image_path"))
        prompt = str(body.get("prompt") or "Smoothly animate this scene").strip()
        req_body, resolved_model = video_tasks.build_video_task(
            client,
            task_type="video.generate",
            model_selector=body.get("model"),
            prompt=prompt,
            image=str(source_path),
            end_image=end_image,
            duration=_parse_float(body.get("duration"), "duration"),
            aspect_ratio=body.get("aspect_ratio"),
            resolution=body.get("resolution"),
            fps=_parse_int(body.get("fps"), "fps"),
            generate_audio=body.get("generate_audio"),
        )
        out_dir = _resolve_output_dir(root, body)
        parents = [str(source_path), *([end_image] if end_image else [])]
        res = _run_cloud_task_and_attach(
            client,
            task_type="video.generate",
            body=req_body,
            model=resolved_model,
            output_dir=out_dir,
            modality="video",
            parent_paths=parents,
            metadata={"prompt": prompt},
        )
        return {
            "ok": True,
            "action": action,
            "created_files": list(res.get("files") or []),
            "result": res,
        }

    if action in {"image_to_3d", "model3d_generate"}:
        client = require_client()
        model3d_tasks = _load_cross_module("model3d_tasks")
        prompt = str(body.get("prompt")).strip() if body.get("prompt") else None
        req_body, resolved_model = model3d_tasks.build_3d_task(
            client,
            task_type="model.generate",
            model_selector=body.get("model"),
            image=str(source_path),
            pbr=body.get("pbr"),
            texture=body.get("texture"),
            face_limit=_parse_int(body.get("face_limit"), "face_limit"),
        )
        out_dir = _resolve_output_dir(root, body)
        res = _run_cloud_task_and_attach(
            client,
            task_type="model.generate",
            body=req_body,
            model=resolved_model,
            output_dir=out_dir,
            modality="3d",
            parent_paths=[str(source_path)],
        )
        return {
            "ok": True,
            "action": action,
            "created_files": list(res.get("files") or []),
            "result": res,
        }

    # ------------------------------------------------------------------
    # Frame sequence actions
    # ------------------------------------------------------------------
    if action in {"frame_sequence_repack", "frame_sequence_reslice"}:
        manifest_file = _find_companion_manifest(source_path, body, root)
        manifest_meta: dict[str, Any] = {}
        if manifest_file:
            try:
                parsed_manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
                manifest_meta = (parsed_manifest.get("meta") if isinstance(parsed_manifest, dict) else None) or {}
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                manifest_meta = {}
        rows = _parse_int(body.get("rows") or manifest_meta.get("rows") or 2, "rows", default=2)
        columns = _parse_int(body.get("columns") or manifest_meta.get("columns") or 2, "columns", default=2)
        fps = _parse_float(body.get("fps") or manifest_meta.get("fps") or 10.0, "fps", default=10.0)
        seq_dir = _resolve_sequence_dir(root, body, stem=f"{source_path.stem}_repacked")
        atlas_res = _slice_image_to_frame_sequence(
            source_path,
            seq_dir,
            rows=rows,
            columns=columns,
            fps=fps,
            frame_width=_parse_int(body.get("frame_width") or None, "frame_width"),
            frame_height=_parse_int(body.get("frame_height") or None, "frame_height"),
            remove_background=bool(body.get("remove_background", False)),
            threshold=_parse_int(body.get("threshold") or 30, "threshold", default=30),
            modality="image",
            operation="frame_sequence",
        )
        return {
            "ok": True,
            "action": action,
            "created_files": [atlas_res["atlas_path"]],
            "atlas": atlas_res,
            "result": {"atlas": atlas_res},
        }

    if action == "frame_sequence_extract_frames":
        manifest_file = _find_companion_manifest(source_path, body, root)
        if not manifest_file:
            raise AsxError("Companion manifest.json not found for frame sequence", code="manifest_not_found")
        try:
            manifest_data = json.loads(manifest_file.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AsxError(f"Invalid manifest.json: {exc}", code="invalid_manifest") from exc
        if not isinstance(manifest_data, dict):
            raise AsxError("Manifest must be a JSON object", code="invalid_manifest")
        frames_obj = manifest_data.get("frames")
        if not isinstance(frames_obj, (dict, list)) or not frames_obj:
            raise AsxError("Manifest contains no frames", code="empty_manifest")

        local_images = _load_cross_module("local_images")
        img, _src, _fmt, _n = local_images._open_image(str(source_path))
        dest_dir = _resolve_output_dir(root, body, subdir=f"generated-assets/{source_path.stem}_frames_{secrets.token_hex(3)}")

        entries = list(frames_obj.items()) if isinstance(frames_obj, dict) else [
            (f"frame-{i:04d}.png", item) for i, item in enumerate(frames_obj)
        ]
        saved: list[dict[str, Any]] = []
        for idx, (fname, info) in enumerate(entries):
            rect = info.get("frame", info) if isinstance(info, dict) else {}
            if not isinstance(rect, dict) or not all(k in rect for k in ("x", "y", "w", "h")):
                raise AsxError(
                    f"Manifest frame '{fname}' has invalid rect",
                    code="invalid_parameters",
                    exit_code=2,
                )
            x = _parse_int(rect["x"], "frame.x")
            y = _parse_int(rect["y"], "frame.y")
            w = _parse_int(rect["w"], "frame.w")
            h = _parse_int(rect["h"], "frame.h")
            tile = img.crop((x, y, x + w, y + h))
            safe_name = Path(fname).stem or f"frame-{idx:04d}"
            out_path = dest_dir / f"{safe_name}.png"
            saved.append(local_images._save(tile, str(out_path), "png"))

        local_images._record_local_images(
            saved,
            operation="extract-frames",
            parents=[str(source_path)],
            metadata={"frame_count": len(saved), "manifest_path": str(manifest_file)},
        )
        created = [item["path"] for item in saved]
        return {
            "ok": True,
            "action": action,
            "created_files": created,
            "result": {"files": saved, "count": len(saved)},
        }

    # ------------------------------------------------------------------
    # Video actions
    # ------------------------------------------------------------------
    if action == "video_edit":
        client = require_client()
        video_tasks = _load_cross_module("video_tasks")
        prompt = str(body.get("prompt") or "").strip()
        if not prompt:
            raise AsxError("prompt is required for video_edit", code="invalid_prompt")
        req_body, resolved_model = video_tasks.build_video_task(
            client,
            task_type="video.edit",
            model_selector=body.get("model"),
            prompt=prompt,
            video=str(source_path),
            duration=_parse_float(body.get("duration"), "duration"),
            aspect_ratio=body.get("aspect_ratio"),
            resolution=body.get("resolution"),
            fps=_parse_int(body.get("fps"), "fps"),
        )
        out_dir = _resolve_output_dir(root, body)
        res = _run_cloud_task_and_attach(
            client,
            task_type="video.edit",
            body=req_body,
            model=resolved_model,
            output_dir=out_dir,
            modality="video",
            parent_paths=[str(source_path)],
            metadata={"prompt": prompt},
        )
        return {
            "ok": True,
            "action": action,
            "created_files": list(res.get("files") or []),
            "result": res,
        }

    if action == "video_trim":
        local_video = _load_cross_module("local_video")
        out_file = _resolve_output_file(
            root,
            body,
            default_stem=f"{source_path.stem}_trim",
            default_ext=source_path.suffix.lstrip(".") or "mp4",
        )
        res = local_video.video_trim(
            source_path,
            out_file,
            start=_parse_float(body.get("start") or 0.0, "start", default=0.0),
            end=_parse_float(body.get("end"), "end"),
            duration=_parse_float(body.get("duration"), "duration"),
        )
        local_video._record_local_video_outputs(
            [res["path"]],
            operation="trim",
            category="video",
            parents=[str(source_path)],
            metadata={"duration_seconds": res.get("duration_seconds")},
        )
        return {
            "ok": True,
            "action": action,
            "created_files": [res["path"]],
            "result": {"video": res},
        }

    if action == "video_convert":
        local_video = _load_cross_module("local_video")
        fmt = str(body.get("format") or body.get("output_format") or "mp4").lower()
        out_file = _resolve_output_file(
            root,
            body,
            default_stem=f"{source_path.stem}_conv",
            default_ext=fmt,
        )
        res = local_video.video_convert(source_path, out_file, output_format=fmt)
        local_video._record_local_video_outputs(
            [res["path"]],
            operation="convert",
            category="video",
            parents=[str(source_path)],
            metadata={"format": res.get("format")},
        )
        return {
            "ok": True,
            "action": action,
            "created_files": [res["path"]],
            "result": {"video": res},
        }

    if action == "video_mux":
        local_video = _load_cross_module("local_video")
        audio_path = _require_existing_file(body.get("audio_path"), root, field="audio_path")
        out_file = _resolve_output_file(
            root,
            body,
            default_stem=f"{source_path.stem}_mux",
            default_ext="mp4",
        )
        res = local_video.video_mux(source_path, audio_path, out_file)
        local_video._record_local_video_outputs(
            [res["path"]],
            operation="mux",
            category="video",
            parents=[str(source_path), str(audio_path)],
            metadata={"duration_seconds": res.get("duration_seconds")},
        )
        return {
            "ok": True,
            "action": action,
            "created_files": [res["path"]],
            "result": {"video": res},
        }

    if action == "video_spritesheet":
        local_video = _load_cross_module("local_video")
        seq_dir = _resolve_sequence_dir(root, body, stem=source_path.stem)
        res = local_video.video_spritesheet(
            source_path,
            seq_dir,
            fps=_parse_float(body.get("fps") or 10.0, "fps", default=10.0),
            columns=_parse_int(body.get("columns") or None, "columns"),
            frame_width=_parse_int(body.get("width") or None, "width"),
            frame_height=_parse_int(body.get("height") or None, "height"),
        )
        local_video._record_local_video_outputs(
            [res["atlas_path"]],
            operation="spritesheet",
            category="frame_sequence",
            parents=[str(source_path)],
            metadata={
                "manifest_path": res["manifest_path"],
                "frame_count": res["frame_count"],
                "columns": res["columns"],
                "rows": res["rows"],
                "frame_size": res["frame_size"],
                "atlas_size": res["atlas_size"],
            },
        )
        return {
            "ok": True,
            "action": action,
            "created_files": [res["atlas_path"]],
            "atlas": res,
            "result": {"spritesheet": res},
        }

    # ------------------------------------------------------------------
    # 3D actions
    # ------------------------------------------------------------------
    if action == "model3d_animations":
        model3d_tasks = _load_cross_module("model3d_tasks")
        return {
            "ok": True,
            "action": action,
            "created_files": [],
            "result": {"groups": {k: list(v) for k, v in model3d_tasks.ANIMATION_PRESETS.items()}},
        }

    if action == "model3d_auto_rig":
        local_model3d = _load_cross_module("local_model3d")
        info = local_model3d.model3d_info(source_path)
        if info.get("joint_count"):
            names = ", ".join(s["name"] for s in info.get("skeletons") or []) or "unknown"
            raise AsxError(f"模型已绑定骨骼：{names}", code="already_rigged")
        client = require_client()
        model3d_tasks = _load_cross_module("model3d_tasks")
        out_dir = _resolve_output_dir(root, body)
        st = source_path.stat()
        cache_key = (str(source_path), st.st_size, st.st_mtime_ns)
        verdict = _RIG_CHECK_CACHE.get(cache_key)
        if verdict is None:
            req_body, resolved_model = model3d_tasks.build_3d_task(
                client,
                task_type="model.rig_check",
                model_selector=None,
                model_file=str(source_path),
            )
            chk = _run_cloud_task_and_attach(
                client,
                task_type="model.rig_check",
                body=req_body,
                model=resolved_model,
                output_dir=out_dir,
                modality="3d",
                parent_paths=[str(source_path)],
            )
            verdict = chk.get("data") or {}
            _RIG_CHECK_CACHE[cache_key] = verdict
        if not verdict.get("riggable"):
            raise AsxError(
                "该模型无法自动绑定骨骼" + (f"：{verdict['reason']}" if verdict.get("reason") else ""),
                code="not_riggable",
                details={"rig_check": verdict},
            )
        rig_type = body.get("rig_type")
        if not rig_type or rig_type == "auto":
            rig_type = verdict.get("recommended_rig_type")
        req_body, resolved_model = model3d_tasks.build_3d_task(
            client,
            task_type="model.rig",
            model_file=str(source_path),
            rig_type=rig_type,
            skeleton_profile=body.get("skeleton_profile"),
        )
        res = _run_cloud_task_and_attach(
            client,
            task_type="model.rig",
            body=req_body,
            model=resolved_model,
            output_dir=out_dir,
            modality="3d",
            parent_paths=[str(source_path)],
        )
        return {
            "ok": True,
            "action": action,
            "created_files": list(res.get("files") or []),
            "result": {**res, "rig_check": verdict},
        }

    if action in {
        "model3d_rig_check",
        "model3d_rig",
        "model3d_animate",
        "model3d_retarget",
        "model3d_convert",
    }:
        client = require_client()
        model3d_tasks = _load_cross_module("model3d_tasks")
        task_map = {
            "model3d_rig_check": "model.rig_check",
            "model3d_rig": "model.rig",
            "model3d_animate": "model.retarget",
            "model3d_retarget": "model.retarget",
            "model3d_convert": "model.convert",
        }
        task_type = task_map[action]
        req_body, resolved_model = model3d_tasks.build_3d_task(
            client,
            task_type=task_type,
            model_selector=body.get("model"),
            model_file=str(source_path),
            rig_type=body.get("rig_type"),
            skeleton_profile=body.get("skeleton_profile"),
            animation=body.get("animation"),
            animations=body.get("animations") if isinstance(body.get("animations"), list) else None,
            output_format=body.get("output_format"),
        )
        out_dir = _resolve_output_dir(root, body)
        res = _run_cloud_task_and_attach(
            client,
            task_type=task_type,
            body=req_body,
            model=resolved_model,
            output_dir=out_dir,
            modality="3d",
            parent_paths=[str(source_path)],
        )
        return {
            "ok": True,
            "action": action,
            "created_files": list(res.get("files") or []),
            "result": res,
        }

    if action == "model3d_info":
        local_model3d = _load_cross_module("local_model3d")
        return {
            "ok": True,
            "action": action,
            "created_files": [],
            "result": {"model": local_model3d.model3d_info(source_path)},
        }

    if action == "model3d_validate":
        local_model3d = _load_cross_module("local_model3d")
        val = local_model3d.model3d_validate(
            source_path,
            max_faces=_parse_int(body.get("max_faces"), "max_faces"),
            require_pbr=bool(body.get("require_pbr", False)),
            max_materials=_parse_int(body.get("max_materials"), "max_materials"),
            require_animations=bool(body.get("require_animations", False)),
        )
        return {
            "ok": True,
            "action": action,
            "created_files": [],
            "result": val,
        }

    if action == "model3d_extract_textures":
        local_model3d = _load_cross_module("local_model3d")
        tex_dir = _resolve_output_dir(
            root,
            body,
            subdir=f"generated-assets/{source_path.stem}_textures_{secrets.token_hex(3)}",
        )
        res = local_model3d.model3d_extract_textures(source_path, tex_dir)
        textures = res.get("textures", [])
        if textures:
            conn = connect_db()
            try:
                local_task_id = new_local_task_id("local_proc")
                for idx, tex in enumerate(textures):
                    record_artifact_with_lineage(
                        conn,
                        file_path=tex["path"],
                        modality="3d",
                        operation="extract-textures",
                        source_kind="local_process",
                        category="image",
                        local_task_id=local_task_id,
                        remote_index=idx,
                        mime=tex.get("mime"),
                        size_bytes=tex.get("size_bytes"),
                        metadata={"name": tex.get("name")},
                        parent_paths=[str(source_path)],
                    )
            finally:
                conn.close()
        return {
            "ok": True,
            "action": action,
            "created_files": [t["path"] for t in textures],
            "result": {"textures": res},
        }

    # ------------------------------------------------------------------
    # Audio actions
    # ------------------------------------------------------------------
    if action == "audio_speech":
        client = require_client()
        audio_tasks = _load_cross_module("audio_tasks")
        text = str(body.get("text") or "").strip()
        if not text:
            raise AsxError("text is required for audio_speech", code="missing_text")
        req_body, resolved_model = audio_tasks.build_audio_task(
            client,
            task_type="audio.speech",
            model_selector=body.get("model"),
            text=text,
            voice=body.get("voice"),
            reference_audio=str(source_path),
            speed=_parse_float(body.get("speed"), "speed"),
            emotion=body.get("emotion"),
            output_format=body.get("output_format"),
        )
        out_dir = _resolve_output_dir(root, body)
        res = _run_cloud_task_and_attach(
            client,
            task_type="audio.speech",
            body=req_body,
            model=resolved_model,
            output_dir=out_dir,
            modality="audio",
            parent_paths=[str(source_path)],
            metadata={"text": text},
        )
        return {
            "ok": True,
            "action": action,
            "created_files": list(res.get("files") or []),
            "result": res,
        }

    if action == "audio_info":
        local_audio = _load_cross_module("local_audio")
        return {
            "ok": True,
            "action": action,
            "created_files": [],
            "result": {"audio": local_audio.audio_info(source_path)},
        }

    if action == "audio_trim":
        local_audio = _load_cross_module("local_audio")
        out_file = _resolve_output_file(
            root,
            body,
            default_stem=f"{source_path.stem}_trim",
            default_ext=source_path.suffix.lstrip(".") or "wav",
        )
        res = local_audio.audio_trim(
            source_path,
            out_file,
            start=_parse_float(body.get("start") or 0.0, "start", default=0.0),
            end=_parse_float(body.get("end"), "end"),
            duration=_parse_float(body.get("duration"), "duration"),
        )
        local_audio._record_local_audio(res, operation="trim", parents=[str(source_path)])
        return {
            "ok": True,
            "action": action,
            "created_files": [res["path"]],
            "result": {"audio": res},
        }

    if action == "audio_fade":
        local_audio = _load_cross_module("local_audio")
        out_file = _resolve_output_file(
            root,
            body,
            default_stem=f"{source_path.stem}_fade",
            default_ext=source_path.suffix.lstrip(".") or "wav",
        )
        res = local_audio.audio_fade(
            source_path,
            out_file,
            fade_in=_parse_float(body.get("fade_in") or 0.0, "fade_in", default=0.0),
            fade_out=_parse_float(body.get("fade_out") or 0.0, "fade_out", default=0.0),
        )
        local_audio._record_local_audio(res, operation="fade", parents=[str(source_path)])
        return {
            "ok": True,
            "action": action,
            "created_files": [res["path"]],
            "result": {"audio": res},
        }

    if action == "audio_normalize":
        local_audio = _load_cross_module("local_audio")
        out_file = _resolve_output_file(
            root,
            body,
            default_stem=f"{source_path.stem}_norm",
            default_ext=source_path.suffix.lstrip(".") or "wav",
        )
        res = local_audio.audio_normalize(
            source_path,
            out_file,
            target_lufs=_parse_float(body.get("target_lufs"), "target_lufs", default=-14.0),
        )
        local_audio._record_local_audio(res, operation="normalize", parents=[str(source_path)])
        return {
            "ok": True,
            "action": action,
            "created_files": [res["path"]],
            "result": {"audio": res},
        }

    if action == "audio_convert":
        local_audio = _load_cross_module("local_audio")
        fmt = str(body.get("format") or body.get("output_format") or "mp3").lower()
        out_file = _resolve_output_file(
            root,
            body,
            default_stem=f"{source_path.stem}_conv",
            default_ext=fmt,
        )
        res = local_audio.audio_convert(source_path, out_file, output_format=fmt)
        local_audio._record_local_audio(res, operation="convert", parents=[str(source_path)])
        return {
            "ok": True,
            "action": action,
            "created_files": [res["path"]],
            "result": {"audio": res},
        }

    raise AsxError(f"Unsupported asset manager action: {action}", code="unsupported_action")
