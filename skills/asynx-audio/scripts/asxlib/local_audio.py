from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import struct
import subprocess
import wave
from pathlib import Path
from typing import Any

from .errors import AsxError

_AUDIO_FORMATS = {"wav", "mp3", "ogg", "m4a", "flac", "aac"}


def _require_ffmpeg(tool: str = "ffmpeg") -> str:
    path = shutil.which(tool)
    if not path:
        raise AsxError(
            f"本地音频处理需要 {tool}。请在系统中安装 ffmpeg（macOS: brew install ffmpeg，Linux: apt install ffmpeg）",
            code="media_tool_missing",
        )
    return path


def audio_info(input_path: str | Path) -> dict[str, Any]:
    path = Path(input_path).expanduser().resolve()
    if not path.is_file():
        raise AsxError(f"音频文件不存在：{path}", code="audio_not_found")

    size_bytes = path.stat().st_size
    # Try pure Python wave module first for WAV files
    try:
        with wave.open(str(path), "rb") as w:
            channels = w.getnchannels()
            sample_width = w.getsampwidth()
            framerate = w.getframerate()
            nframes = w.getnframes()
            duration = nframes / float(framerate) if framerate > 0 else 0.0

            # Compute peak / RMS amplitude on sample frames
            frames_to_read = min(nframes, framerate * 2)  # up to 2 seconds sample
            raw_frames = w.readframes(frames_to_read)
            peak = 0.0
            rms = 0.0
            if raw_frames and sample_width == 2:
                sample_count = len(raw_frames) // 2
                fmt = f"<{sample_count}h"
                samples = struct.unpack(fmt, raw_frames)
                peak = max(abs(s) for s in samples) / 32768.0
                rms = math.sqrt(sum(s * s for s in samples) / sample_count) / 32768.0

            return {
                "path": str(path),
                "format": "wav",
                "codec": "pcm_s16le" if sample_width == 2 else f"pcm_{sample_width*8}bit",
                "channels": channels,
                "sample_rate": framerate,
                "duration_seconds": round(duration, 3),
                "size_bytes": size_bytes,
                "peak_amplitude": round(peak, 3),
                "rms_amplitude": round(rms, 3),
            }
    except Exception:
        pass

    # Use ffprobe for compressed or non-standard audio
    ffprobe_bin = _require_ffmpeg("ffprobe")
    cmd = [
        ffprobe_bin,
        "-v",
        "error",
        "-show_entries",
        "format=duration,size,bit_rate:stream=codec_name,channels,sample_rate",
        "-of",
        "json",
        str(path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(proc.stdout)
        format_info = data.get("format", {})
        streams = data.get("streams", [])
        stream_info = streams[0] if streams else {}

        duration = float(format_info.get("duration", 0.0))
        channels = int(stream_info.get("channels", 1))
        sample_rate = int(stream_info.get("sample_rate", 44100))
        codec = stream_info.get("codec_name", path.suffix.removeprefix("."))

        return {
            "path": str(path),
            "format": path.suffix.removeprefix(".").lower(),
            "codec": codec,
            "channels": channels,
            "sample_rate": sample_rate,
            "duration_seconds": round(duration, 3),
            "size_bytes": size_bytes,
        }
    except Exception as exc:
        raise AsxError(f"无法读取音频元数据：{exc}", code="audio_probe_failed") from exc


def audio_convert(
    input_path: str | Path,
    output_path: str | Path,
    output_format: str | None = None,
) -> dict[str, Any]:
    src = Path(input_path).expanduser().resolve()
    dst = Path(output_path).expanduser().resolve()
    dst.parent.mkdir(parents=True, exist_ok=True)

    fmt = output_format or dst.suffix.removeprefix(".").lower()
    if fmt not in _AUDIO_FORMATS:
        raise AsxError(f"不支持的音频格式：{fmt}", code="unsupported_audio_format")

    ffmpeg_bin = _require_ffmpeg("ffmpeg")
    cmd = [ffmpeg_bin, "-y", "-i", str(src), str(dst)]
    try:
        subprocess.run(cmd, capture_output=True, check=True)
    except subprocess.CalledProcessError as exc:
        raise AsxError(f"音频转换失败：{exc.stderr.decode()}", code="audio_convert_failed") from exc

    return audio_info(dst)


def audio_trim(
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

    cmd.append(str(dst))
    try:
        subprocess.run(cmd, capture_output=True, check=True)
    except subprocess.CalledProcessError as exc:
        raise AsxError(f"音频修剪失败：{exc.stderr.decode()}", code="audio_trim_failed") from exc

    return audio_info(dst)


def audio_fade(
    input_path: str | Path,
    output_path: str | Path,
    *,
    fade_in: float = 0.0,
    fade_out: float = 0.0,
) -> dict[str, Any]:
    src = Path(input_path).expanduser().resolve()
    dst = Path(output_path).expanduser().resolve()
    dst.parent.mkdir(parents=True, exist_ok=True)

    info = audio_info(src)
    total_duration = info["duration_seconds"]

    filters = []
    if fade_in > 0:
        filters.append(f"afade=t=in:ss=0:d={fade_in}")
    if fade_out > 0:
        fade_out_start = max(0.0, total_duration - fade_out)
        filters.append(f"afade=t=out:st={fade_out_start}:d={fade_out}")

    if not filters:
        shutil.copy2(src, dst)
        return audio_info(dst)

    ffmpeg_bin = _require_ffmpeg("ffmpeg")
    filter_expr = ",".join(filters)
    cmd = [ffmpeg_bin, "-y", "-i", str(src), "-af", filter_expr, str(dst)]
    try:
        subprocess.run(cmd, capture_output=True, check=True)
    except subprocess.CalledProcessError as exc:
        raise AsxError(f"音频淡入淡出失败：{exc.stderr.decode()}", code="audio_fade_failed") from exc

    return audio_info(dst)


def audio_normalize(
    input_path: str | Path,
    output_path: str | Path,
    *,
    target_lufs: float = -14.0,
) -> dict[str, Any]:
    src = Path(input_path).expanduser().resolve()
    dst = Path(output_path).expanduser().resolve()
    dst.parent.mkdir(parents=True, exist_ok=True)

    ffmpeg_bin = _require_ffmpeg("ffmpeg")
    # EBU R128 loudness normalization
    filter_expr = f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11"
    cmd = [ffmpeg_bin, "-y", "-i", str(src), "-af", filter_expr, str(dst)]
    try:
        subprocess.run(cmd, capture_output=True, check=True)
    except subprocess.CalledProcessError as exc:
        raise AsxError(f"音频响度标准化失败：{exc.stderr.decode()}", code="audio_normalize_failed") from exc

    return audio_info(dst)


def audio_concat(
    input_paths: list[str | Path],
    output_path: str | Path,
) -> dict[str, Any]:
    if not input_paths:
        raise AsxError("拼接至少需要一个音频文件", code="missing_audio_inputs")

    dst = Path(output_path).expanduser().resolve()
    dst.parent.mkdir(parents=True, exist_ok=True)

    ffmpeg_bin = _require_ffmpeg("ffmpeg")
    cmd = [ffmpeg_bin, "-y"]
    filter_inputs = ""
    for idx, path in enumerate(input_paths):
        cmd.extend(["-i", str(Path(path).expanduser().resolve())])
        filter_inputs += f"[{idx}:a]"

    filter_expr = f"{filter_inputs}concat=n={len(input_paths)}:v=0:a=1[outa]"
    cmd.extend(["-filter_complex", filter_expr, "-map", "[outa]", str(dst)])

    try:
        subprocess.run(cmd, capture_output=True, check=True)
    except subprocess.CalledProcessError as exc:
        raise AsxError(f"音频拼接失败：{exc.stderr.decode()}", code="audio_concat_failed") from exc

    return audio_info(dst)


def execute_local_audio(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    cmd = args.audio_command
    if cmd == "info":
        return {"ok": True, "audio": audio_info(args.input)}, 0
    if cmd == "convert":
        return {"ok": True, "audio": audio_convert(args.input, args.output, args.format)}, 0
    if cmd == "trim":
        return {"ok": True, "audio": audio_trim(args.input, args.output, start=args.start, end=args.end, duration=args.duration)}, 0
    if cmd == "fade":
        return {"ok": True, "audio": audio_fade(args.input, args.output, fade_in=args.fade_in, fade_out=args.fade_out)}, 0
    if cmd == "normalize":
        return {"ok": True, "audio": audio_normalize(args.input, args.output, target_lufs=args.target_lufs)}, 0
    if cmd == "concat":
        return {"ok": True, "audio": audio_concat(args.inputs, args.output)}, 0
    raise AssertionError("unreachable")
