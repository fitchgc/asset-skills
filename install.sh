#!/usr/bin/env sh
# One-command, non-interactive install of all Asynx skills (no prompts, no login).
#   curl -fsSL https://raw.githubusercontent.com/fitchgc/asset-skills/main/install.sh | sh
# Options (after `sh -s --`):
#   --login            also open the browser login page at the end (blocks until done)
#   --target X         codex | claude | both (default: auto-detect, else codex)
# Login is otherwise triggered automatically on first cloud use.
set -eu
REPO="${ASSET_SKILLS_REPO:-https://github.com/fitchgc/asset-skills.git}"
REF="${ASSET_SKILLS_REF:-main}"
LOGIN=0; TARGET=""
while [ $# -gt 0 ]; do
  case "$1" in
    --login) LOGIN=1 ;;
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
if [ "$LOGIN" = 1 ]; then
  for d in "$HOME/.agents/skills" "$HOME/.claude/skills"; do
    if [ -f "$d/asynx-asset-manager/scripts/asynx.py" ]; then
      "$PY" "$d/asynx-asset-manager/scripts/asynx.py" login; break
    fi
  done
else
  echo "安装完成。首次使用云端生成时会自动打开登录页面；也可随时运行 asynx.py login。"
fi
