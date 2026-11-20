#!/usr/bin/env python3
"""Install Asynx Agent Asset Skills and configure API keys."""

from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import sys
from pathlib import Path
from types import ModuleType
from typing import NoReturn

ROOT = Path(__file__).resolve().parent
SKILLS_DIR = ROOT / "skills"

SKILL_NAME_ALIASES: dict[str, str] = {
    "image": "plaiers-asynx-image-pro",
    "img": "plaiers-asynx-image-pro",
    "asynx-image": "plaiers-asynx-image-pro",
    "plaiers-asynx-image-pro": "plaiers-asynx-image-pro",
    "image-pro": "plaiers-asynx-image-pro",
    "audio": "plaiers-asynx-audio",
    "snd": "plaiers-asynx-audio",
    "plaiers-asynx-audio": "plaiers-asynx-audio",
    "video": "plaiers-asynx-video",
    "vid": "plaiers-asynx-video",
    "plaiers-asynx-video": "plaiers-asynx-video",
    "3d": "plaiers-asynx-3d",
    "model3d": "plaiers-asynx-3d",
    "plaiers-asynx-3d": "plaiers-asynx-3d",
    "workbench": "plaiers-asynx-workbench",
    "asynx-workbench": "plaiers-asynx-workbench",
    "asset-manager": "plaiers-asynx-workbench",
    "asset_manager": "plaiers-asynx-workbench",
    "manager": "plaiers-asynx-workbench",
    "assets": "plaiers-asynx-workbench",
    "plaiers-asynx-workbench": "plaiers-asynx-workbench",
    # Names before the plaiers- namespace (PR #10 Q2).
    "asynx-image-pro": "plaiers-asynx-image-pro",
    "asynx-audio": "plaiers-asynx-audio",
    "asynx-video": "plaiers-asynx-video",
    "asynx-3d": "plaiers-asynx-3d",
    "asynx-asset-manager": "plaiers-asynx-workbench",
}

ALL_SKILL_NAMES: list[str] = [
    "plaiers-asynx-image-pro",
    "plaiers-asynx-audio",
    "plaiers-asynx-video",
    "plaiers-asynx-3d",
    "plaiers-asynx-workbench",
]

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
                "plaiers-asynx-image-pro": "image",
                "asynx-image": "image",
                "plaiers-asynx-audio": "audio",
                "plaiers-asynx-video": "video",
                "plaiers-asynx-3d": "3d",
                "plaiers-asynx-workbench": "asset_manager",
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


def _target_path(target: str, home: Path, skill_name: str = "plaiers-asynx-image-pro") -> Path:
    if target == "codex":
        return home / ".agents" / "skills" / skill_name
    if target == "claude":
        return home / ".claude" / "skills" / skill_name
    raise ValueError(f"不支持的安装目标：{target}，请使用 codex、claude 或 both。")


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
    needs_ffmpeg = any(s in {"plaiers-asynx-audio", "plaiers-asynx-video"} for s in skills)
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


def _install_skill(skill_name: str, destination: Path) -> None:
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
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )

    script = destination / "scripts" / "asynx.py"
    if script.exists():
        script.chmod(script.stat().st_mode | 0o111)

    client = _load_client_module(destination)
    update_files = importlib.import_module(f"{client.__name__}.update_files")
    update_files.record_install(destination, files=update_files.inventory(skill_source))


def _uninstall_skill(destination: Path) -> bool:
    if destination.is_symlink():
        destination.unlink()
        return True
    if not destination.exists():
        return False
    if not destination.is_dir() or not destination.name.startswith("plaiers-asynx-") or destination.parent.name != "skills":
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
    return bool(client.config_status().get("configured"))


def _verify(client: ModuleType) -> int:
    try:
        api_client = client.load_client()
        identity, _id_req = api_client.partner_identity()
        models, _request_id = api_client.models()
    except client.AsxError as exc:
        if exc.http_status in {401, 403} or exc.code in {"need_login", "invalid_partner_token", "scope_missing"}:
            message = "SageIn Agent 授权无效或已过期，请通过 sagein-community 重新授权（需勾选 skill_jwt 权限）。"
        elif exc.http_status == 403:
            message = "当前账号无权访问模型目录，请检查权限，或联系服务管理员。"
        elif exc.code == "network_error":
            message = "暂时无法连接 Asynx 或 SageIn 服务，请检查网络后重试。"
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
        "audio.speech",
        "model.generate",
    }
    capable = [
        model
        for model in models
        if isinstance(model, dict) and asset_task_types.intersection(model.get("task_types", []))
    ]
    if not capable:
        print("Skill 已安装、连接正常，但当前没有可用的多模态模型。请稍后重试或联系服务支持。", file=sys.stderr)
        return 1
    api_status = (identity.get("api_key") or {}).get("status", "active") if isinstance(identity, dict) else "active"
    if api_status == "quota_exhausted":
        print(f"连接检查通过（可访问 {len(capable)} 个多模态资产模型），但当前账户额度已用尽 (quota_exhausted)。")
    else:
        print(f"连接检查通过，可访问 {len(capable)} 个多模态资产模型，账户状态：{api_status}。")
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
        help="安装指定技能，默认全部安装 (all)。可选：image, audio, video, 3d, asset-manager 或逗号分隔组合",
    )
    parser.add_argument(
        "--skip-config",
        action="store_true",
        help="安装但不进行账号登录",
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="跳过登录会话和模型目录检查（只读，不提交任务）",
    )
    parser.add_argument(
        "--uninstall",
        action="store_true",
        help="卸载已安装的 Skill，保留登录会话、任务记录和已生成文件",
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
                    "当前终端无法确认卸载。如需卸载，请在命令中添加 --yes；登录会话、任务记录和已生成文件将保留。"
                )
            skills_str = ", ".join(target_skills)
            print(f"将卸载目标：{', '.join(targets)} 下的 [{skills_str}]。登录会话、任务记录和已生成文件将保留。", file=sys.stderr)
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
            _install_skill(skill, destination)
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
            print("检测到 SageIn Agent 授权，运行时将自动换取短期 JWT。")
        else:
            legacy = [n for n in ("ASYNX_PARTNER_TOKEN", "ASYNX_BASE_URL") if os.environ.get(n)]
            if legacy:
                print(f"{' / '.join(legacy)} 已不再支持，请取消这些环境变量，改用 Plaiers 社区凭据桥接（见 docs/agent-jwt-contract.md）。")
            print("未检测到 SageIn Agent 授权。请先通过 sagein-community 完成授权（勾选 skill_jwt 权限）。")
            return 0
        if not args.no_verify:
            verification = _verify(client)
            if verification:
                return verification
        else:
            print("已跳过连接检查，尚未验证登录会话是否可用。")
    else:
        print("已跳过授权和连接检查；使用云端能力前，请先通过 sagein-community 完成授权（勾选 skill_jwt 权限）。")

    print(
        "\n提示：生成、查询、下载无需额外依赖。参考图、Mask 与本地图片处理需要共享运行环境（Pillow），"
        "需要时征得同意后运行：python3 <Skill 目录>/scripts/asynx.py runtime prepare"
    )
    print("\n安装完成！使用示例：")
    if "codex" in targets:
        print("Codex 中可输入：")
        for skill in target_skills:
            example = {
                "plaiers-asynx-image-pro": "$plaiers-asynx-image-pro 生成一张卡通猫的图片",
                "asynx-image": "$plaiers-asynx-image-pro 生成一张卡通猫的图片",
                "plaiers-asynx-audio": "$plaiers-asynx-audio 生成一段雨声音效",
                "plaiers-asynx-video": "$plaiers-asynx-video 生成一段猫咪玩耍的视频",
                "plaiers-asynx-3d": "$plaiers-asynx-3d 生成一个宝箱 3D 模型",
                "plaiers-asynx-workbench": "$plaiers-asynx-workbench 扫描当前项目的创作资产并查看血缘关系",
            }.get(skill, f"${skill}")
            print(f"  {example}")
    if "claude" in targets:
        print("Claude Code 中可输入：")
        for skill in target_skills:
            example = {
                "plaiers-asynx-image-pro": "/plaiers-asynx-image-pro 生成一张卡通猫的图片",
                "asynx-image": "/plaiers-asynx-image-pro 生成一张卡通猫的图片",
                "plaiers-asynx-audio": "/plaiers-asynx-audio 生成一段雨声音效",
                "plaiers-asynx-video": "/plaiers-asynx-video 生成一段猫咪玩耍的视频",
                "plaiers-asynx-3d": "/plaiers-asynx-3d 生成一个宝箱 3D 模型",
                "plaiers-asynx-workbench": "/plaiers-asynx-workbench 扫描当前项目的创作资产并查看血缘关系",
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
