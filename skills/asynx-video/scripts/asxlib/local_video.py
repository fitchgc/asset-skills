from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .errors import AsxError

_VIDEO_FORMATS = {"mp4", "webm", "gif", "mov", "mkv"}


def _require_ffmpeg(tool: str = "ffmpeg") -> str:
    path = shutil.which(tool)
    if not path:
        raise AsxError(
            f"本地视频处理需要 {tool}。请在系统中安装 ffmpeg（macOS: brew install ffmpeg，Linux: apt install ffmpeg）",
            code="media_tool_missing",
        )
    return path


def video_info(input_path: str | Path) -> dict[str, Any]:
    path = Path(input_path).expanduser().resolve()
    if not path.is_file():
        raise AsxError(f"视频文件不存在：{path}", code="video_not_found")

    ffprobe_bin = _require_ffmpeg("ffprobe")
    cmd = [
        ffprobe_bin,
        "-v",
        "error",
        "-show_entries",
        "format=duration,size,bit_rate:stream=codec_name,width,height,r_frame_rate,nb_frames,codec_type",
        "-of",
        "json",
        str(path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(proc.stdout)
        format_info = data.get("format", {})
        streams = data.get("streams", [])

        v_stream = next((s for s in streams if s.get("codec_type") == "video"), {})
        a_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

        duration = float(format_info.get("duration") or 0.0)
        width = int(v_stream.get("width") or 0)
        height = int(v_stream.get("height") or 0)
        codec = v_stream.get("codec_name", path.suffix.removeprefix("."))

        r_fps = v_stream.get("r_frame_rate", "30/1")
        try:
            num, den = r_fps.split("/")
            fps = round(float(num) / float(den), 2)
        except Exception:
            fps = 30.0

        nb_frames = v_stream.get("nb_frames")
        frame_count = int(nb_frames) if nb_frames and nb_frames.isdigit() else int(duration * fps)

        return {
            "path": str(path),
            "format": path.suffix.removeprefix(".").lower(),
            "codec": codec,
            "width": width,
            "height": height,
            "duration_seconds": round(duration, 3),
            "fps": fps,
            "frame_count": frame_count,
            "has_audio": a_stream is not None,
            "size_bytes": path.stat().st_size,
        }
    except Exception as exc:
        raise AsxError(f"无法读取视频元数据：{exc}", code="video_probe_failed") from exc


def extract_frames(
    input_path: str | Path,
    output_dir: str | Path,
    *,
    fps: float | None = None,
    prefix: str = "frame",
) -> dict[str, Any]:
    src = Path(input_path).expanduser().resolve()
    dst_dir = Path(output_dir).expanduser().resolve()
    dst_dir.mkdir(parents=True, exist_ok=True)

    ffmpeg_bin = _require_ffmpeg("ffmpeg")
    pattern = str(dst_dir / f"{prefix}-%04d.png")

    cmd = [ffmpeg_bin, "-y", "-i", str(src)]
    if fps:
        cmd.extend(["-vf", f"fps={fps}"])
    cmd.append(pattern)

    try:
        subprocess.run(cmd, capture_output=True, check=True)
    except subprocess.CalledProcessError as exc:
        raise AsxError(f"视频抽帧失败：{exc.stderr.decode()}", code="extract_frames_failed") from exc

    frames = sorted(str(p) for p in dst_dir.glob(f"{prefix}-*.png"))
    return {
        "source": str(src),
        "output_dir": str(dst_dir),
        "frame_count": len(frames),
        "frames": frames,
    }


def video_spritesheet(
    input_path: str | Path,
    output_dir: str | Path,
    *,
    fps: float | None = 10.0,
    columns: int | None = None,
    frame_width: int | None = None,
    frame_height: int | None = None,
) -> dict[str, Any]:
    src = Path(input_path).expanduser().resolve()
    dst_dir = Path(output_dir).expanduser().resolve()
    dst_dir.mkdir(parents=True, exist_ok=True)

    extracted = extract_frames(src, dst_dir / ".tmp_frames", fps=fps)
    frame_paths = extracted["frames"]
    if not frame_paths:
        raise AsxError("未从视频中提取到任何帧", code="empty_frames")

    try:
        import PIL.Image
    except ImportError:
        raise AsxError("拼合精灵表需要 Pillow", code="pillow_missing")

    first_frame = PIL.Image.open(frame_paths[0])
    orig_w, orig_h = first_frame.size
    target_w = frame_width or orig_w
    target_h = frame_height or orig_h

    total_frames = len(frame_paths)
    cols = columns or math.ceil(math.sqrt(total_frames))
    rows = math.ceil(total_frames / cols)

    atlas_w = cols * target_w
    atlas_h = rows * target_h
    atlas_img = PIL.Image.new("RGBA", (atlas_w, atlas_h), (0, 0, 0, 0))

    manifest_frames: dict[str, Any] = {}
    frame_duration = round(1.0 / (fps or 10.0), 3)

    for i, f_path in enumerate(frame_paths):
        c = i % cols
        r = i // cols
        x = c * target_w
        y = r * target_h

        img = PIL.Image.open(f_path).convert("RGBA")
        if (img.width, img.height) != (target_w, target_h):
            img = img.resize((target_w, target_h), PIL.Image.Resampling.LANCZOS)

        atlas_img.paste(img, (x, y))
        frame_name = f"frame-{i:04d}.png"
        manifest_frames[frame_name] = {
            "frame": {"x": x, "y": y, "w": target_w, "h": target_h},
            "rotated": False,
            "trimmed": False,
            "spriteSourceSize": {"x": 0, "y": 0, "w": target_w, "h": target_h},
            "sourceSize": {"w": target_w, "h": target_h},
            "duration": frame_duration,
        }

    atlas_path = dst_dir / "atlas.png"
    manifest_path = dst_dir / "manifest.json"

    atlas_img.save(atlas_path, format="PNG")
    manifest = {
        "frames": manifest_frames,
        "meta": {
            "app": "asynx-video",
            "version": "1.0",
            "image": "atlas.png",
            "format": "RGBA8888",
            "size": {"w": atlas_w, "h": atlas_h},
            "scale": 1,
            "frame_count": total_frames,
            "columns": cols,
            "rows": rows,
            "fps": fps or 10.0,
        },
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    shutil.rmtree(dst_dir / ".tmp_frames", ignore_errors=True)

    return {
        "source": str(src),
        "atlas_path": str(atlas_path),
        "manifest_path": str(manifest_path),
        "frame_count": total_frames,
        "columns": cols,
        "rows": rows,
        "frame_size": {"w": target_w, "h": target_h},
        "atlas_size": {"w": atlas_w, "h": atlas_h},
    }


def video_trim(
    input_path: str | Path,
    output_path: str | Path,
    *,
    start: float = 0.0,
    end: float | None = None,
    duration: float | None = None,
) -> dict[str, Any]:
    src = Path(input_path).expanduser().resolve()
    dst = Path(output_path).expanduser().resolve()
    dst.parent.mkdir(parents=True, exist_ok=True)

    ffmpeg_bin = _require_ffmpeg("ffmpeg")
    cmd = [ffmpeg_bin, "-y", "-i", str(src), "-ss", str(start)]
    if end is not None:
        cmd.extend(["-to", str(end)])
    elif duration is not None:
        cmd.extend(["-t", str(duration)])

    cmd.extend(["-c", "copy", str(dst)])
    try:
        subprocess.run(cmd, capture_output=True, check=True)
    except subprocess.CalledProcessError as exc:
        raise AsxError(f"视频修剪失败：{exc.stderr.decode()}", code="video_trim_failed") from exc

    return video_info(dst)


def video_convert(
    input_path: str | Path,
    output_path: str | Path,
    output_format: str | None = None,
) -> dict[str, Any]:
    src = Path(input_path).expanduser().resolve()
    dst = Path(output_path).expanduser().resolve()
    dst.parent.mkdir(parents=True, exist_ok=True)

    fmt = output_format or dst.suffix.removeprefix(".").lower()
    if fmt not in _VIDEO_FORMATS:
        raise AsxError(f"不支持的视频格式：{fmt}", code="unsupported_video_format")

    ffmpeg_bin = _require_ffmpeg("ffmpeg")
    cmd = [ffmpeg_bin, "-y", "-i", str(src)]

    if fmt == "gif":
        cmd.extend(["-vf", "fps=15,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse"])
    cmd.append(str(dst))

    try:
        subprocess.run(cmd, capture_output=True, check=True)
    except subprocess.CalledProcessError as exc:
        raise AsxError(f"视频转换失败：{exc.stderr.decode()}", code="video_convert_failed") from exc

    return video_info(dst)


def video_mux(
    video_path: str | Path,
    audio_path: str | Path,
    output_path: str | Path,
) -> dict[str, Any]:
    v_src = Path(video_path).expanduser().resolve()
    a_src = Path(audio_path).expanduser().resolve()
    dst = Path(output_path).expanduser().resolve()
    dst.parent.mkdir(parents=True, exist_ok=True)

    ffmpeg_bin = _require_ffmpeg("ffmpeg")
    cmd = [
        ffmpeg_bin,
        "-y",
        "-i",
        str(v_src),
        "-i",
        str(a_src),
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-shortest",
        str(dst),
    ]
    try:
        subprocess.run(cmd, capture_output=True, check=True)
    except subprocess.CalledProcessError as exc:
        raise AsxError(f"音视频合成失败：{exc.stderr.decode()}", code="video_mux_failed") from exc

    return video_info(dst)


def execute_local_video(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    cmd = args.video_command
    if cmd == "info":
        return {"ok": True, "video": video_info(args.input)}, 0
    if cmd == "extract-frames":
        return {"ok": True, "frames": extract_frames(args.input, args.output_dir, fps=args.fps, prefix=args.prefix)}, 0
    if cmd == "spritesheet":
        return {"ok": True, "spritesheet": video_spritesheet(args.input, args.output_dir, fps=args.fps, columns=args.columns, frame_width=args.width, frame_height=args.height)}, 0
    if cmd == "trim":
        return {"ok": True, "video": video_trim(args.input, args.output, start=args.start, end=args.end, duration=args.duration)}, 0
    if cmd == "convert":
        return {"ok": True, "video": video_convert(args.input, args.output, args.format)}, 0
    if cmd == "mux":
        return {"ok": True, "video": video_mux(args.video, args.audio, args.output)}, 0
    raise AssertionError("unreachable")
