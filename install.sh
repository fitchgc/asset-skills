#!/usr/bin/env sh
# One-command, non-interactive install of all Asynx skills (no prompts).
#   curl -fsSL https://raw.githubusercontent.com/fitchgc/asset-skills/main/install.sh | sh
# Options (after `sh -s --`):
#   --target X         codex | claude | both (default: auto-detect, else codex)
# Cloud use requires a SageIn Agent grant with the skill_jwt scope (sagein-community).
set -eu
REPO="${ASSET_SKILLS_REPO:-https://github.com/fitchgc/asset-skills.git}"
REF="${ASSET_SKILLS_REF:-main}"
TARGET=""
while [ $# -gt 0 ]; do
  case "$1" in
    --target) TARGET="$2"; shift ;;
    *) echo "未知参数：$1" >&2; exit 2 ;;
  esac
  shift
done
PY="$(command -v python3 || command -v python || true)"
[ -n "$PY" ] || { echo "需要 Python 3.10+" >&2; exit 1; }
if [ -z "$TARGET" ]; then
  if [ -d "$HOME/.codex" ] || [ -d "$HOME/.agents" ] || [ -d "$HOME/.claude" ]; then TARGET=auto; else TARGET=codex; fi
fi
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
git clone --quiet --depth 1 --branch "$REF" "$REPO" "$TMP/asset-skills"
"$PY" "$TMP/asset-skills/install.py" --skill all --target "$TARGET" --skip-config
echo "安装完成。云端能力需先通过 sagein-community 授权（勾选 skill_jwt 权限），随后自动换取短期 JWT。"
