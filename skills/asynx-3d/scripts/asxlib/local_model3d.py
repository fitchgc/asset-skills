from __future__ import annotations

import argparse
import base64
import json
import os
import struct
from pathlib import Path
from typing import Any

from .errors import AsxError


def _parse_glb(data: bytes) -> tuple[dict[str, Any], bytes]:
    if len(data) < 12:
        raise AsxError("文件小于 GLB 容器最小长度", code="invalid_glb_file")
    magic, version, length = struct.unpack_from("<4sII", data, 0)
    if magic != b"glTF":
        raise AsxError("非法的 GLB 魔数，不是 glTF 2.0 二进制文件", code="invalid_glb_magic")

    offset = 12
    json_data: dict[str, Any] = {}
    bin_buffer = b""

    while offset < len(data):
        if offset + 8 > len(data):
            break
        chunk_len, chunk_type = struct.unpack_from("<II", data, offset)
        offset += 8
        chunk_bytes = data[offset : offset + chunk_len]
        offset += chunk_len

        if chunk_type == 0x4E4F534A:  # "JSON"
            try:
                json_data = json.loads(chunk_bytes.decode("utf-8"))
            except Exception as exc:
                raise AsxError(f"无法解析 GLB JSON 块：{exc}", code="invalid_glb_json") from exc
        elif chunk_type == 0x004E4942:  # "BIN\0"
            bin_buffer = chunk_bytes

    return json_data, bin_buffer


def _parse_obj(text: str) -> dict[str, Any]:
    vertices = []
    faces = 0
    materials = set()

    min_x = min_y = min_z = float("inf")
    max_x = max_y = max_z = float("-inf")

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        tag = parts[0]

        if tag == "v" and len(parts) >= 4:
            try:
                x, y, z = float(parts[1]), float(parts[2]), float(parts[3])
                vertices.append((x, y, z))
                min_x = min(min_x, x)
                min_y = min(min_y, y)
                min_z = min(min_z, z)
                max_x = max(max_x, x)
                max_y = max(max_y, y)
                max_z = max(max_z, z)
            except ValueError:
                pass
        elif tag == "f":
            # Count triangles. Quad = 2 triangles; polygon with N vertices = N - 2 triangles
            poly_verts = len(parts) - 1
            if poly_verts >= 3:
                faces += poly_verts - 2
        elif tag == "usemtl" and len(parts) >= 2:
            materials.add(parts[1])

    if vertices:
        bbox = {
            "min": [round(min_x, 4), round(min_y, 4), round(min_z, 4)],
            "max": [round(max_x, 4), round(max_y, 4), round(max_z, 4)],
            "extents": [
                round(max_x - min_x, 4),
                round(max_y - min_y, 4),
                round(max_z - min_z, 4),
            ],
        }
    else:
        bbox = {"min": [0, 0, 0], "max": [0, 0, 0], "extents": [0, 0, 0]}

    return {
        "format": "obj",
        "vertex_count": len(vertices),
        "face_count": faces,
        "bounding_box": bbox,
        "materials": sorted(materials),
        "texture_count": 0,
        "joint_count": 0,
        "animation_clips": [],
    }


def model3d_info(input_path: str | Path) -> dict[str, Any]:
    path = Path(input_path).expanduser().resolve()
    if not path.is_file():
        raise AsxError(f"3D 模型文件不存在：{path}", code="model_not_found")

    size_bytes = path.stat().st_size
    suffix = path.suffix.lower()

    if suffix in {".gltf", ".glb"}:
        data = path.read_bytes()
        if suffix == ".glb":
            gltf_json, _ = _parse_glb(data)
        else:
            try:
                gltf_json = json.loads(data.decode("utf-8"))
            except Exception as exc:
                raise AsxError(f"glTF 文件非有效 JSON：{exc}", code="invalid_gltf_json") from exc

        accessors = gltf_json.get("accessors", [])
        meshes = gltf_json.get("meshes", [])
        materials = gltf_json.get("materials", [])
        textures = gltf_json.get("textures", [])
        skins = gltf_json.get("skins", [])
        animations = gltf_json.get("animations", [])

        vertex_count = 0
        face_count = 0
        min_x = min_y = min_z = float("inf")
        max_x = max_y = max_z = float("-inf")

        for mesh in meshes:
            for prim in mesh.get("primitives", []):
                attrs = prim.get("attributes", {})
                pos_idx = attrs.get("POSITION")
                if pos_idx is not None and pos_idx < len(accessors):
                    acc = accessors[pos_idx]
                    vertex_count += acc.get("count", 0)
                    acc_min = acc.get("min", [])
                    acc_max = acc.get("max", [])
                    if len(acc_min) >= 3 and len(acc_max) >= 3:
                        min_x = min(min_x, acc_min[0])
                        min_y = min(min_y, acc_min[1])
                        min_z = min(min_z, acc_min[2])
                        max_x = max(max_x, acc_max[0])
                        max_y = max(max_y, acc_max[1])
                        max_z = max(max_z, acc_max[2])

                idx = prim.get("indices")
                if idx is not None and idx < len(accessors):
                    acc = accessors[idx]
                    face_count += acc.get("count", 0) // 3
                elif pos_idx is not None and pos_idx < len(accessors):
                    # Non-indexed triangles
                    face_count += accessors[pos_idx].get("count", 0) // 3

        if min_x != float("inf"):
            bbox = {
                "min": [round(min_x, 4), round(min_y, 4), round(min_z, 4)],
                "max": [round(max_x, 4), round(max_y, 4), round(max_z, 4)],
                "extents": [
                    round(max_x - min_x, 4),
                    round(max_y - min_y, 4),
                    round(max_z - min_z, 4),
                ],
            }
        else:
            bbox = {"min": [0, 0, 0], "max": [0, 0, 0], "extents": [0, 0, 0]}

        clips = []
        for anim in animations:
            name = anim.get("name") or "unnamed_animation"
            duration = 0.0
            for sampler in anim.get("samplers", []):
                input_acc_idx = sampler.get("input")
                if input_acc_idx is not None and input_acc_idx < len(accessors):
                    acc = accessors[input_acc_idx]
                    acc_max = acc.get("max", [0.0])
                    if acc_max:
                        duration = max(duration, float(acc_max[0]))
            clips.append({"name": name, "duration_seconds": round(duration, 3)})

        joint_count = sum(len(s.get("joints", [])) for s in skins)
        material_names = [m.get("name", f"mat_{i}") for i, m in enumerate(materials)]

        has_pbr = any("pbrMetallicRoughness" in m for m in materials)

        return {
            "path": str(path),
            "format": suffix.removeprefix("."),
            "vertex_count": vertex_count,
            "face_count": face_count,
            "bounding_box": bbox,
            "materials": material_names,
            "has_pbr": has_pbr,
            "texture_count": len(textures),
            "joint_count": joint_count,
            "animation_clips": clips,
            "size_bytes": size_bytes,
        }

    elif suffix == ".obj":
        text = path.read_text(encoding="utf-8", errors="replace")
        info = _parse_obj(text)
        info["path"] = str(path)
        info["size_bytes"] = size_bytes
        info["has_pbr"] = False
        return info

    else:
        # Generic fallback
        return {
            "path": str(path),
            "format": suffix.removeprefix("."),
            "size_bytes": size_bytes,
        }


def model3d_validate(
    input_path: str | Path,
    *,
    max_faces: int | None = None,
    require_pbr: bool = False,
    max_materials: int | None = None,
    require_animations: bool = False,
) -> dict[str, Any]:
    info = model3d_info(input_path)
    violations: list[dict[str, Any]] = []

    face_count = info.get("face_count", 0)
    if max_faces is not None and face_count > max_faces:
        violations.append({
            "rule": "max_faces",
            "message": f"面数 ({face_count}) 超出上限 ({max_faces})",
            "actual": face_count,
            "expected": max_faces,
        })

    if require_pbr and not info.get("has_pbr"):
        violations.append({
            "rule": "require_pbr",
            "message": "模型缺少 PBR (Metallic-Roughness) 材质定义",
            "actual": False,
            "expected": True,
        })

    mat_count = len(info.get("materials", []))
    if max_materials is not None and mat_count > max_materials:
        violations.append({
            "rule": "max_materials",
            "message": f"材质槽数量 ({mat_count}) 超出上限 ({max_materials})",
            "actual": mat_count,
            "expected": max_materials,
        })

    clip_count = len(info.get("animation_clips", []))
    if require_animations and clip_count == 0:
        violations.append({
            "rule": "require_animations",
            "message": "模型未包含任何动画剪辑 (Animation Clips)",
            "actual": 0,
            "expected": 1,
        })

    return {
        "ok": len(violations) == 0,
        "valid": len(violations) == 0,
        "path": info["path"],
        "violations": violations,
        "model": info,
    }


def model3d_extract_textures(
    input_path: str | Path,
    output_dir: str | Path,
) -> dict[str, Any]:
    path = Path(input_path).expanduser().resolve()
    dst_dir = Path(output_dir).expanduser().resolve()
    dst_dir.mkdir(parents=True, exist_ok=True)

    if path.suffix.lower() != ".glb":
        raise AsxError("当前仅支持从 GLB 文件中直接提取内嵌贴图", code="unsupported_format")

    data = path.read_bytes()
    gltf_json, bin_buffer = _parse_glb(data)

    images = gltf_json.get("images", [])
    buffer_views = gltf_json.get("bufferViews", [])

    extracted = []
    for i, img in enumerate(images):
        mime = img.get("mimeType", "image/png")
        ext = "png" if "png" in mime else "jpg" if "jpeg" in mime or "jpg" in mime else "webp"
        name = img.get("name") or f"texture_{i:02d}"
        file_name = f"{name}.{ext}"
        out_file = dst_dir / file_name

        if "bufferView" in img and bin_buffer:
            bv_idx = img["bufferView"]
            if bv_idx < len(buffer_views):
                bv = buffer_views[bv_idx]
                byte_offset = bv.get("byteOffset", 0)
                byte_length = bv.get("byteLength", 0)
                img_bytes = bin_buffer[byte_offset : byte_offset + byte_length]
                out_file.write_bytes(img_bytes)
                extracted.append({
                    "name": name,
                    "path": str(out_file),
                    "mime": mime,
                    "size_bytes": len(img_bytes),
                })
        elif "uri" in img:
            uri = img["uri"]
            if uri.startswith("data:"):
                header, encoded = uri.split(",", 1)
                img_bytes = base64.b64decode(encoded)
                out_file.write_bytes(img_bytes)
                extracted.append({
                    "name": name,
                    "path": str(out_file),
                    "mime": mime,
                    "size_bytes": len(img_bytes),
                })

    return {
        "source": str(path),
        "output_dir": str(dst_dir),
        "count": len(extracted),
        "textures": extracted,
    }


def execute_local_model3d(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    cmd = args.model3d_command
    if cmd == "info":
        return {"ok": True, "model": model3d_info(args.input)}, 0
    if cmd == "validate":
        res = model3d_validate(
            args.input,
            max_faces=getattr(args, "max_faces", None),
            require_pbr=getattr(args, "require_pbr", False),
            max_materials=getattr(args, "max_materials", None),
            require_animations=getattr(args, "require_animations", False),
        )
        return res, 0 if res["valid"] else 2
    if cmd == "extract-textures":
        return {"ok": True, "textures": model3d_extract_textures(args.input, args.output_dir)}, 0
    raise AssertionError("unreachable")
