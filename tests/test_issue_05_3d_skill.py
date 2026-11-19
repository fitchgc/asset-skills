from __future__ import annotations

import io
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "shared"))
from tests.interpreter_pillow import add_interpreter_pillow  # noqa: E402

add_interpreter_pillow()

import PIL.Image

from shared.asxlib.state import connect_db, create_task_intent, list_tasks, upsert_asset
from tests.fake_gateway import run_fake_gateway


def make_test_glb(path: Path) -> None:
    # 1. Create a tiny 16x16 PNG for embedded texture
    img = PIL.Image.new("RGBA", (16, 16), (255, 0, 128, 255))
    png_buf = io.BytesIO()
    img.save(png_buf, format="PNG")
    png_bytes = png_buf.getvalue()

    # 2. Geometry buffer: 3 vertices (triangle), 3 indices (uint16)
    # Vertices: (0,0,0), (1,0,0), (0,1,0) -> 9 floats = 36 bytes
    vert_bytes = struct.pack("<9f", 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0)
    # Indices: 0, 1, 2 -> 3 uint16 = 6 bytes + 2 bytes padding to 4-byte align
    idx_bytes = struct.pack("<3HH", 0, 1, 2, 0)
    # Animation input: time 0.0, 1.0 -> 2 floats = 8 bytes
    anim_time_bytes = struct.pack("<2f", 0.0, 1.0)
    # Animation output: rot 0, rot 1 -> 8 floats = 32 bytes
    anim_rot_bytes = struct.pack("<8f", 0.0, 0.0, 0.0, 1.0, 0.0, 0.707, 0.0, 0.707)

    # Bin buffer layout:
    # 0..36: positions
    # 36..44: indices
    # 44..52: anim time
    # 52..84: anim rot
    # 84..: texture png
    bin_buf = bytearray()
    bin_buf.extend(vert_bytes)
    bin_buf.extend(idx_bytes)
    bin_buf.extend(anim_time_bytes)
    bin_buf.extend(anim_rot_bytes)
    tex_offset = len(bin_buf)
    bin_buf.extend(png_bytes)
    while len(bin_buf) % 4 != 0:
        bin_buf.append(0)

    gltf_dict = {
        "asset": {"version": "2.0"},
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": "TestNode"}],
        "meshes": [
            {
                "primitives": [
                    {
                        "attributes": {"POSITION": 0},
                        "indices": 1,
                        "material": 0,
                    }
                ]
            }
        ],
        "materials": [
            {
                "name": "KnightArmor",
                "pbrMetallicRoughness": {"baseColorTexture": {"index": 0}},
            }
        ],
        "textures": [{"source": 0}],
        "images": [{"bufferView": 4, "mimeType": "image/png", "name": "base_color"}],
        "accessors": [
            {
                "bufferView": 0,
                "componentType": 5126,  # FLOAT
                "count": 3,
                "type": "VEC3",
                "min": [0.0, 0.0, 0.0],
                "max": [1.0, 1.0, 0.0],
            },
            {
                "bufferView": 1,
                "componentType": 5123,  # UNSIGNED_SHORT
                "count": 3,
                "type": "SCALAR",
            },
            {
                "bufferView": 2,
                "componentType": 5126,
                "count": 2,
                "type": "SCALAR",
                "min": [0.0],
                "max": [1.0],
            },
            {
                "bufferView": 3,
                "componentType": 5126,
                "count": 2,
                "type": "VEC4",
            },
        ],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": 36},
            {"buffer": 0, "byteOffset": 36, "byteLength": 6},
            {"buffer": 0, "byteOffset": 44, "byteLength": 8},
            {"buffer": 0, "byteOffset": 52, "byteLength": 32},
            {"buffer": 0, "byteOffset": tex_offset, "byteLength": len(png_bytes)},
        ],
        "buffers": [{"byteLength": len(bin_buf)}],
        "animations": [
            {
                "name": "walk_cycle",
                "channels": [{"sampler": 0, "target": {"node": 0, "path": "rotation"}}],
                "samplers": [{"input": 2, "output": 3}],
            }
        ],
    }

    json_bytes = json.dumps(gltf_dict).encode("utf-8")
    while len(json_bytes) % 4 != 0:
        json_bytes += b" "

    # Assemble GLB
    # Header: 12 bytes
    # Chunk 0: 8 bytes + len(json_bytes)
    # Chunk 1: 8 bytes + len(bin_buf)
    total_len = 12 + 8 + len(json_bytes) + 8 + len(bin_buf)
    header = struct.pack("<4sII", b"glTF", 2, total_len)
    chunk0_hdr = struct.pack("<II", len(json_bytes), 0x4E4F534A)
    chunk1_hdr = struct.pack("<II", len(bin_buf), 0x004E4942)

    with open(path, "wb") as f:
        f.write(header)
        f.write(chunk0_hdr)
        f.write(json_bytes)
        f.write(chunk1_hdr)
        f.write(bin_buf)


def make_test_obj(path: Path) -> None:
    content = """# Wavefront OBJ test fixture
mtllib test.mtl
v 0.0 0.0 0.0
v 1.0 0.0 0.0
v 0.0 1.0 0.0
v 1.0 1.0 0.0
usemtl SwordMaterial
f 1 2 3
f 2 4 3
"""
    path.write_text(content, encoding="utf-8")


class TestAsynx3DSkill(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.state_db = Path(self.temp_dir) / "state.db"
        self.config_file = Path(self.temp_dir) / "config.json"
        self.fixture_glb = Path(self.temp_dir) / "test.glb"
        self.fixture_obj = Path(self.temp_dir) / "test.obj"
        make_test_glb(self.fixture_glb)
        make_test_obj(self.fixture_obj)

        self.env = {
            **os.environ,
            "ASYNX_STATE_PATH": str(self.state_db),
            "ASYNX_CONFIG_PATH": str(self.config_file),
            "ASYNX_PARTNER_TOKEN": "pt_3d_test_key",
        }
        self.server, self.gateway_url, self.thread = run_fake_gateway()
        self.env["ASYNX_BASE_URL"] = self.gateway_url
        self.asynx_bin = REPO_ROOT / "skills" / "asynx-3d" / "scripts" / "asynx.py"

        # Provide fake GLB asset on gateway
        self.server.asset_data["task_1:0"] = self.fixture_glb.read_bytes()
        self.server.asset_mimes["task_1:0"] = "model/gltf-binary"

    def tearDown(self) -> None:
        self.server.shutdown()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def run_cli(self, *args: str) -> dict[str, Any]:
        proc = subprocess.run(
            [sys.executable, str(self.asynx_bin), *args],
            capture_output=True,
            text=True,
            env=self.env,
        )
        self.assertEqual(proc.returncode, 0, f"CLI stderr: {proc.stderr}\nstdout: {proc.stdout}")
        return json.loads(proc.stdout)

    def test_pure_python_model3d_operations(self) -> None:
        """Pure-Python model3d info, validate, and extract-textures without Blender."""
        # 1. info on GLB
        info = self.run_cli("model3d", "info", str(self.fixture_glb))
        self.assertTrue(info["ok"])
        m = info["model"]
        self.assertEqual(m["format"], "glb")
        self.assertEqual(m["vertex_count"], 3)
        self.assertEqual(m["face_count"], 1)
        self.assertTrue(m["has_pbr"])
        self.assertIn("KnightArmor", m["materials"])
        self.assertEqual(len(m["animation_clips"]), 1)
        self.assertEqual(m["animation_clips"][0]["name"], "walk_cycle")
        self.assertEqual(m["animation_clips"][0]["duration_seconds"], 1.0)

        # 2. info on OBJ
        obj_info = self.run_cli("model3d", "info", str(self.fixture_obj))
        self.assertTrue(obj_info["ok"])
        self.assertEqual(obj_info["model"]["format"], "obj")
        self.assertEqual(obj_info["model"]["vertex_count"], 4)
        self.assertEqual(obj_info["model"]["face_count"], 2)
        self.assertIn("SwordMaterial", obj_info["model"]["materials"])

        # 3. validate constraints on GLB
        val = self.run_cli("model3d", "validate", str(self.fixture_glb), "--max-faces", "10", "--require-pbr", "--require-animations")
        self.assertTrue(val["ok"])
        self.assertTrue(val["valid"])

        # 4. extract-textures from GLB
        tex_dir = Path(self.temp_dir) / "extracted_tex"
        tex_res = self.run_cli("model3d", "extract-textures", str(self.fixture_glb), "--output-dir", str(tex_dir))
        self.assertTrue(tex_res["ok"])
        self.assertEqual(tex_res["textures"]["count"], 1)
        extracted_file = Path(tex_res["textures"]["textures"][0]["path"])
        self.assertTrue(extracted_file.is_file())
        self.assertEqual(extracted_file.suffix, ".png")

    def test_cloud_3d_pipeline_and_cross_modal(self) -> None:
        """Cloud 3D generation, cross-modal image-to-3d, and rigging/animation chaining."""
        out_dir = Path(self.temp_dir) / "3d_out"

        # 1. Text to 3D
        gen_res = self.run_cli("generate", "--prompt", "magic sword", "--output-dir", str(out_dir))
        self.assertTrue(gen_res["ok"])
        self.assertEqual(gen_res["modality"], "3d")

        conn = connect_db(self.state_db)
        tasks = list_tasks(conn, modality="3d")
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["modality"], "3d")

        # 2. Simulate an asynx-image-pro task in ledger
        img_file = Path(self.temp_dir) / "character.png"
        PIL.Image.new("RGBA", (100, 100), (0, 200, 255, 255)).save(img_file)
        create_task_intent(
            conn,
            operation="generate",
            model="img-model",
            idempotency_key="char_key",
            request={"input": {"prompt": "character front view"}},
            modality="image",
            local_id="task_img_char",
        )
        upsert_asset(conn, "task_img_char", 0, path=str(img_file), state="available")
        conn.close()

        # 3. Cross-modal Image-to-3D using --from-task
        self.server.asset_data["task_2:0"] = self.fixture_glb.read_bytes()
        cross_res = self.run_cli("generate", "--from-task", "task_img_char", "--output-dir", str(out_dir))
        self.assertTrue(cross_res["ok"])
        self.assertEqual(cross_res["modality"], "3d")
        sub = self.server.submissions[-1]
        self.assertEqual(sub["task_type"], "model.generate")
        self.assertEqual(sub["model"], "tripo-h3.1")
        self.assertTrue(sub["input"]["image"].startswith("data:image/"))
        self.assertNotIn("prompt", sub["input"])

        # 4. Rig check: verdict only, no files
        rc_res = self.run_cli("rig-check", "--from-task", "task_2", "--output-dir", str(out_dir))
        self.assertTrue(rc_res["ok"])
        self.assertEqual(rc_res["data"], {"riggable": True, "recommended_rig_type": "biped"})
        self.assertEqual(rc_res["files"], [])
        sub = self.server.submissions[-1]
        self.assertEqual(sub["task_type"], "model.rig_check")
        self.assertEqual(sub["input"], {"source": {"task_id": "task_2"}})

        # 5. Rig, animate, convert
        self.server.asset_data["task_4:0"] = self.fixture_glb.read_bytes()
        rig_res = self.run_cli("rig", "--from-task", "task_2", "--rig-type", "biped", "--skeleton-profile", "mixamo", "--output-dir", str(out_dir))
        self.assertTrue(rig_res["ok"])
        sub = self.server.submissions[-1]
        self.assertEqual(sub["task_type"], "model.rig")
        self.assertEqual(sub["input"]["rig"], {"type": "biped", "skeleton_profile": "mixamo"})
        self.assertEqual(sub["input"]["output"], {"formats": ["glb"]})

        self.server.asset_data["task_5:0"] = self.fixture_glb.read_bytes()
        anim_res = self.run_cli("animate", "--from-task", "task_4", "--animation", "walk", "--output-dir", str(out_dir))
        self.assertTrue(anim_res["ok"])
        sub = self.server.submissions[-1]
        self.assertEqual(sub["task_type"], "model.retarget")
        self.assertEqual(sub["input"]["animations"], ["preset:walk"])
        self.assertEqual(sub["input"]["source"], {"task_id": "task_4"})

        self.server.asset_data["task_6:0"] = self.fixture_glb.read_bytes()
        conv_res = self.run_cli("convert", "--from-task", "task_5", "--output-format", "fbx", "--output-dir", str(out_dir))
        self.assertTrue(conv_res["ok"])
        sub = self.server.submissions[-1]
        self.assertEqual(sub["task_type"], "model.convert")
        self.assertEqual(sub["input"]["output"], {"formats": ["fbx"]})

    def test_local_model_file_uploads_as_file_id(self) -> None:
        out_dir = Path(self.temp_dir) / "3d_file_out"
        self.server.asset_data["task_1:0"] = self.fixture_glb.read_bytes()
        res = self.run_cli("rig", "--model-file", str(self.fixture_glb), "--output-dir", str(out_dir))
        self.assertTrue(res["ok"])
        sub = self.server.submissions[-1]
        self.assertEqual(sub["task_type"], "model.rig")
        self.assertEqual(sub["input"]["source"], {"file_id": "file_1"})
        upload = self.server.uploaded_files["file_1"]
        self.assertIn(b'name="purpose"\r\n\r\nmodel', upload)

        proc = subprocess.run(
            [sys.executable, str(self.asynx_bin), "animate", "--model-file", str(self.fixture_glb), "--animation", "walk"],
            capture_output=True, text=True, env=self.env,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("file_source_not_supported", proc.stdout)


if __name__ == "__main__":
    unittest.main()
