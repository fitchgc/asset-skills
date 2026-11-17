#!/usr/bin/env sh
# One-line bootstrap: installs all Asynx skills without prompting for API keys.
#   curl -fsSL https://raw.githubusercontent.com/fitchgc/asset-skills/main/install.sh | sh
# Extra args are passed to install.py, e.g.  ... | sh -s -- --target claude
set -eu
REPO="${ASSET_SKILLS_REPO:-https://github.com/fitchgc/asset-skills.git}"
REF="${ASSET_SKILLS_REF:-main}"
PY="$(command -v python3 || command -v python || true)"
[ -n "$PY" ] || { echo "需要 Python 3.10+" >&2; exit 1; }
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
git clone --depth 1 --branch "$REF" "$REPO" "$TMP/asset-skills"
"$PY" "$TMP/asset-skills/install.py" --skill all --skip-config "$@"
echo "安装完成。请在终端自行运行 install.py 配置 API Key（不要发给 Agent）。"
