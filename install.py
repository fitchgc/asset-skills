#!/usr/bin/env python3
"""Install Asynx Agent Asset Skills and configure API keys."""

from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import NoReturn

ROOT = Path(__file__).resolve().parent
SKILLS_DIR = ROOT / "skills"
PILLOW_REQUIREMENT = "Pillow>=10.2,<13"

SKILL_NAME_ALIASES: dict[str, str] = {
    "image": "asynx-image",
    "img": "asynx-image",
    "asynx-image": "asynx-image",
    "audio": "asynx-audio",
    "snd": "asynx-audio",
    "asynx-audio": "asynx-audio",
    "video": "asynx-video",
    "vid": "asynx-video",
    "asynx-video": "asynx-video",
    "3d": "asynx-3d",
    "model3d": "asynx-3d",
    "asynx-3d": "asynx-3d",
}

ALL_SKILL_NAMES: list[str] = [
    "asynx-image",
    "asynx-audio",
    "asynx-video",
    "asynx-3d",
]

SKILLS_NEEDING_PILLOW = {"asynx-image", "asynx-video", "asynx-3d"}


def _resolve_skill_names(selection: str) -> list[str]:
    raw = [item.strip().lower() for item in selection.split(",") if item.strip()]
    if not raw or "all" in raw:
        return list(ALL_SKILL_NAMES)
    resolved: list[str] = []
    for item in raw:
        if item in SKILL_NAME_ALIASES:
            canonical = SKILL_NAME_ALIASES[item]
            if canonical not in resolved:
                resolved.append(canonical)
        else:
            valid_keys = ", ".join(sorted(set(SKILL_NAME_ALIASES.keys()) | {"all"}))
            raise ValueError(f"未知技能名称：{item}。可选技能：{valid_keys}")
    return resolved


def _ensure_skill_built(skill_name: str) -> Path:
    skill_source = SKILLS_DIR / skill_name
    if not skill_source.exists():
        build_script = ROOT / "src" / "build_skills.py"
        if build_script.exists():
            modality_map = {
                "asynx-image": "image",
                "asynx-audio": "audio",
                "asynx-video": "video",
                "asynx-3d": "3d",
            }
            modality = modality_map.get(skill_name)
            if modality:
                spec = importlib.util.spec_from_file_location("build_skills", build_script)
                if spec and spec.loader:
                    builder = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(builder)
                    builder.build_skill(modality)
    if not skill_source.exists():
        raise RuntimeError(f"未找到 Skill 源码目录：{skill_source}")
    return skill_source


def _load_client_module(skill_root: Path) -> ModuleType:
    package = (skill_root / "scripts" / "asxlib").resolve()
    module_name = "_asynx_installed"
    for name in tuple(sys.modules):
        if name == module_name or name.startswith(f"{module_name}."):
            del sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        module_name,
        package / "__init__.py",
        submodule_search_locations=[str(package)],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"已安装的 Skill 加载失败，请重新运行安装命令。位置：{package}")
    client = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = client
    try:
        spec.loader.exec_module(client)
    except Exception:
        for name in tuple(sys.modules):
            if name == module_name or name.startswith(f"{module_name}."):
                del sys.modules[name]
        raise
    module_path = Path(client.__file__ or "").resolve()
    if not module_path.is_relative_to(package):
        raise RuntimeError(f"加载到的 Skill 位置不对，请重新运行安装命令。位置：{module_path}")
    return client


def _detected_targets(home: Path) -> list[str]:
    detected: list[str] = []
    if (home / ".codex").exists() or (home / ".agents").exists():
        detected.append("codex")
    if (home / ".claude").exists():
        detected.append("claude")
    return detected


def _prompt_target() -> list[str]:
    if not sys.stdin.isatty():
        raise RuntimeError(
            "未检测到 Codex 或 Claude Code，请在安装命令后指定 --target codex、--target claude 或 --target both。"
        )
    print("请选择安装目标：codex = Codex，claude = Claude Code，both = 两者都安装。", file=sys.stderr)
    while True:
        print("请输入选择（直接回车默认安装到 Codex）：", end="", file=sys.stderr, flush=True)
        value = input().strip().casefold() or "codex"
        if value in {"codex", "claude"}:
            return [value]
        if value == "both":
            return ["codex", "claude"]
        print("请输入 codex、claude 或 both。", file=sys.stderr)


def _resolve_targets(selection: str, home: Path) -> list[str]:
    if selection == "codex":
        return ["codex"]
    if selection == "claude":
        return ["claude"]
    if selection == "both":
        return ["codex", "claude"]
    detected = _detected_targets(home)
    return detected or _prompt_target()


def _target_path(target: str, home: Path, skill_name: str = "asynx-image") -> Path:
    if target == "codex":
        return home / ".agents" / "skills" / skill_name
    if target == "claude":
        return home / ".claude" / "skills" / skill_name
    raise ValueError(f"不支持的安装目标：{target}，请使用 codex、claude 或 both。")


def _install_dependencies(destination: Path) -> None:
    vendor = destination / "scripts" / "vendor"
    vendor.mkdir(parents=True, exist_ok=True)
    print(f"正在为 {destination.name} 安装处理依赖，首次安装可能需要一些时间……", file=sys.stderr)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--upgrade",
            "--target",
            str(vendor),
            PILLOW_REQUIREMENT,
        ],
        check=False,
        capture_output=True,
    )
    if result.returncode:
        # If vendor already has files (e.g. copied or bundled), warn instead of hard fail
        if any(vendor.iterdir()):
            print(
                f"警告：依赖联网安装未成功，将尝试使用内置预编译依赖。如遇图片处理异常请检查网络或执行 pip install {PILLOW_REQUIREMENT}。",
                file=sys.stderr,
            )
            return
        raise RuntimeError("依赖安装失败，请检查网络连接以及当前 Python 是否已安装 pip，然后重新运行安装命令。")


def _check_legacy_installations(home: Path, targets: list[str]) -> None:
    candidates = [_target_path(target, home, "asx") for target in targets]
    if "codex" in targets:
        candidates.append(home / ".codex" / "skills" / "asx")
    existing = [path for path in candidates if path.exists() or path.is_symlink()]
    if existing:
        paths = "\n".join(str(path) for path in existing)
        raise RuntimeError(
            "检测到旧版 asx，需先移除旧入口才能继续安装 Asynx Skills：\n"
            f"{paths}\n"
            "可让 Agent 协助确认，将旧入口移出 skills 目录备份后再继续安装。\n"
            "密钥、任务记录和已生成文件均需保留，无需迁移；请勿仅修改旧目录名称。"
        )


def _check_system_tools(skills: list[str]) -> None:
    needs_ffmpeg = any(s in {"asynx-audio", "asynx-video"} for s in skills)
    if needs_ffmpeg and shutil.which("ffmpeg") is None:
        print(
            "提示：未检测到系统 ffmpeg 工具。音频合成/格式转换与视频高级处理需依赖 ffmpeg。\n"
            "建议安装：\n"
            "  - macOS: brew install ffmpeg\n"
            "  - Ubuntu/Debian: sudo apt install ffmpeg\n"
            "  - Windows: winget install Gyan.FFmpeg 或 choco install ffmpeg\n"
            "（注：仅生成与查询等云端功能不受影响）",
            file=sys.stderr,
        )


def _install_skill(
    skill_name: str,
    destination: Path,
    *,
    install_dependencies: bool = True,
) -> None:
    skill_source = _ensure_skill_built(skill_name)
    if destination.exists() and not destination.is_dir():
        raise RuntimeError(f"安装位置被同名文件占用，请先检查该文件：{destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Keep the lock outside the replaced directory and ready for read-only CLI calls.
    with (destination.parent / ".asx-runtime.lock").open("ab"):
        pass

    # Copy files
    shutil.copytree(
        skill_source,
        destination,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "vendor"),
    )

    script = destination / "scripts" / "asynx.py"
    if script.exists():
        script.chmod(script.stat().st_mode | 0o111)

    if install_dependencies and skill_name in SKILLS_NEEDING_PILLOW:
        _install_dependencies(destination)

    client = _load_client_module(destination)
    update_files = importlib.import_module(f"{client.__name__}.update_files")
    update_files.record_install(destination, files=update_files.inventory(skill_source))


def _uninstall_skill(destination: Path) -> bool:
    if destination.is_symlink():
        destination.unlink()
        return True
    if not destination.exists():
        return False
    if not destination.is_dir() or not destination.name.startswith("asynx-") or destination.parent.name != "skills":
        raise RuntimeError(f"该路径不是预期的 Skill 安装目录，未执行删除：{destination}")
    shutil.rmtree(destination)
    return True


def _installed_targets(home: Path, skills: list[str]) -> list[str]:
    installed: list[str] = []
    for target in ("codex", "claude"):
        if any(_target_path(target, home, skill).exists() for skill in skills):
            installed.append(target)
    return installed


def _configured(client: ModuleType) -> bool:
    key = os.environ.get("ASYNX_API_KEY")
    if isinstance(key, str) and key.strip():
        return True
    config = client.read_config()
    configured = config.get("api_key")
    if not isinstance(configured, str) or not configured.strip():
        return False
    try:
        client.validate_api_key(configured)
    except client.AsxError:
        return False
    return True


def _verify(client: ModuleType) -> int:
    try:
        api_key, base_url = client.load_credentials()
        models, _request_id = client.AsynxClient(base_url, api_key).models()
    except client.AsxError as exc:
        if exc.http_status == 401 or exc.code in {"missing_api_key", "invalid_api_key"}:
            message = "API Key 未通过验证，请确认密钥完整有效，并在自己的终端重新配置。"
        elif exc.http_status == 403:
            message = "当前密钥无权访问模型目录，请检查密钥权限，或联系服务管理员。"
        elif exc.code == "network_error":
            message = "暂时无法连接 Asynx，请检查网络或服务地址后重试；这不代表密钥有误。"
        elif exc.http_status == 429:
            message = "请求过于频繁，请稍后重新执行连接检查。"
        else:
            message = "模型目录检查未能完成，请稍后重试；若持续失败，可将下方错误代码提供给服务支持。"
        print(f"Skill 已安装，但连接检查未通过。{message}", file=sys.stderr)
        print(f"错误代码：{exc.code}", file=sys.stderr)
        if exc.request_id:
            print(f"请求 ID：{exc.request_id}", file=sys.stderr)
        print("无需重装 Skill，可回到 Agent 对话中请求检查配置与连接。", file=sys.stderr)
        return 1

    asset_task_types = {
        "image.generate",
        "image.edit",
        "video.generate",
        "audio.sfx",
        "audio.music",
        "audio.tts",
        "model3d.generate",
    }
    capable = [
        model
        for model in models
        if isinstance(model, dict) and asset_task_types.intersection(model.get("task_types", []))
    ]
    if not capable:
        print("Skill 已安装、连接正常，但当前没有可用的多模态模型。请稍后重试或联系服务支持。", file=sys.stderr)
        return 1
    print(f"连接检查通过，可访问 {len(capable)} 个多模态资产模型。本次未提交生成任务，实际生成权限与余额尚未验证。")
    return 0


class _InstallerParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        self.exit(2, "安装参数无效，请运行 python3 install.py --help 查看用法。\n")


def _parser() -> argparse.ArgumentParser:
    parser = _InstallerParser(description="安装或卸载 Asynx Agent Asset Skills")
    parser.add_argument(
        "--target",
        choices=("auto", "codex", "claude", "both"),
        default="auto",
        help="安装目标 Agent，默认自动检测 Codex 和 Claude Code",
    )
    parser.add_argument(
        "--skill",
        default="all",
        help="安装指定技能，默认全部安装 (all)。可选：image, audio, video, 3d 或逗号分隔组合",
    )
    parser.add_argument(
        "--skip-config",
        action="store_true",
        help="安装但不询问 API Key",
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="跳过密钥和模型目录检查（只读，不提交任务）",
    )
    parser.add_argument(
        "--uninstall",
        action="store_true",
        help="卸载已安装的 Skill，保留密钥、任务记录和已生成文件",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="卸载时跳过确认，仅可与 --uninstall 一起使用",
    )
    return parser


def run(
    argv: list[str] | None = None,
    *,
    home: Path | None = None,
    install_dependencies: bool = True,
) -> int:
    args = _parser().parse_args(argv)
    user_home = (home or Path.home()).resolve()
    target_skills = _resolve_skill_names(args.skill)

    if args.uninstall:
        targets = (
            _installed_targets(user_home, target_skills)
            if args.target == "auto"
            else _resolve_targets(args.target, user_home)
        )
        if not targets:
            print("未找到由本安装器安装的 Asynx Skill。")
            return 0
        if not args.yes:
            if not sys.stdin.isatty():
                raise RuntimeError(
                    "当前终端无法确认卸载。如需卸载，请在命令中添加 --yes；密钥、任务记录和已生成文件将保留。"
                )
            skills_str = ", ".join(target_skills)
            print(f"将卸载目标：{', '.join(targets)} 下的 [{skills_str}]。密钥、任务记录和已生成文件将保留。", file=sys.stderr)
            print("确认卸载？[y/N] ", end="", file=sys.stderr, flush=True)
            if input().strip().casefold() not in {"y", "yes"}:
                print("已取消卸载。", file=sys.stderr)
                return 0

        for target in targets:
            label = "Codex" if target == "codex" else "Claude Code"
            for skill in target_skills:
                destination = _target_path(target, user_home, skill)
                if _uninstall_skill(destination):
                    print(f"已从 {label} 卸载：{destination}")
        return 0

    targets = _resolve_targets(args.target, user_home)
    _check_legacy_installations(user_home, targets)
    _check_system_tools(target_skills)

    first_installed: Path | None = None
    for target in targets:
        label = "Codex" if target == "codex" else "Claude Code"
        for skill in target_skills:
            destination = _target_path(target, user_home, skill)
            _install_skill(skill, destination, install_dependencies=install_dependencies)
            if first_installed is None:
                first_installed = destination
            print(f"已安装到 {label}：{destination}")

    if first_installed is None:
        print("未执行任何安装。")
        return 0

    client = _load_client_module(first_installed)
    migrate = getattr(client, "migrate_legacy_config", None)
    if callable(migrate):
        migrated_path = migrate()
        if migrated_path is not None:
            print(f"已迁移旧配置，原文件已保留：{migrated_path}")
    print(f"配置文件：{client.config_path().resolve()}")

    if not args.skip_config:
        if _configured(client):
            if os.environ.get("ASYNX_API_KEY"):
                print("正在使用 ASYNX_API_KEY 环境变量中的密钥，其优先级高于配置文件；此步骤不会将其写入配置文件。")
            else:
                print("正在使用已保存的 API Key，无需重复输入。")
        else:
            client.configure()
        if not args.no_verify:
            verification = _verify(client)
            if verification:
                return verification
        else:
            print("已跳过连接检查，尚未验证密钥是否可用。")
    else:
        print("已跳过密钥配置和连接检查；若尚未配置，请回到 Agent 对话中获取配置指引。")

    print("\n安装完成！使用示例：")
    if "codex" in targets:
        print("Codex 中可输入：")
        for skill in target_skills:
            example = {
                "asynx-image": "$asynx-image 生成一张卡通猫的图片",
                "asynx-audio": "$asynx-audio 生成一段雨声音效",
                "asynx-video": "$asynx-video 生成一段猫咪玩耍的视频",
                "asynx-3d": "$asynx-3d 生成一个宝箱 3D 模型",
            }.get(skill, f"${skill}")
            print(f"  {example}")
    if "claude" in targets:
        print("Claude Code 中可输入：")
        for skill in target_skills:
            example = {
                "asynx-image": "/asynx-image 生成一张卡通猫的图片",
                "asynx-audio": "/asynx-audio 生成一段雨声音效",
                "asynx-video": "/asynx-video 生成一段猫咪玩耍的视频",
                "asynx-3d": "/asynx-3d 生成一个宝箱 3D 模型",
            }.get(skill, f"/{skill}")
            print(f"  {example}")
    return 0


def main() -> int:
    try:
        return run()
    except (KeyboardInterrupt, EOFError):
        print("操作已中断。已完成的安装或配置步骤将保留，可重新运行命令继续。", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 - installer failures share one CLI error path
        if isinstance(exc, PermissionError):
            message = "没有权限访问安装目录或配置文件，请检查目录权限后重新运行命令。"
        elif isinstance(exc, OSError):
            message = "无法读写安装文件或启动所需程序，请检查目录权限、磁盘空间，以及 Python 和 pip 是否可用。"
        elif isinstance(exc, RuntimeError) or hasattr(exc, "message"):
            message = getattr(exc, "message", str(exc))
        else:
            message = "安装过程发生异常，请重新运行安装命令；若仍失败，请联系服务支持。"
        print(f"未能完成操作：{message}", file=sys.stderr)
        return int(getattr(exc, "exit_code", 1))


if __name__ == "__main__":
    raise SystemExit(main())
