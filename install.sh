#!/usr/bin/env bash
# codey installer — install or update to the latest main.
#   curl -fsSL https://raw.githubusercontent.com/yuhangzhao0126/codey/main/install.sh | bash
# Re-run to self-update. Never overwrites your config.
set -euo pipefail

REPO="git+https://github.com/yuhangzhao0126/codey@main"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/codey"
CONFIG_FILE="$CONFIG_DIR/config.toml"

echo "codey installer"

# 1. ensure uv (bootstraps its own Python 3.11+)
if ! command -v uv >/dev/null 2>&1; then
  echo "→ installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

# 2. install or update codey
echo "→ installing codey from main..."
uv tool install --force "$REPO"

# 3. seed a placeholder config only if none exists
if [ ! -f "$CONFIG_FILE" ]; then
  echo "→ writing default config to $CONFIG_FILE"
  mkdir -p "$CONFIG_DIR"
  cat > "$CONFIG_FILE" <<'TOML'
default_profile = "deepseek"

[profiles.deepseek]
base_url = "https://api.deepseek.com/v1"
api_key  = "sk-..."
model    = "deepseek-chat"
TOML
  chmod 600 "$CONFIG_FILE"
fi

# 4. PATH hint
if ! command -v codey >/dev/null 2>&1; then
  echo "⚠ ~/.local/bin is not on your PATH. Add to your shell rc:"
  echo '    export PATH="$HOME/.local/bin:$PATH"'
fi

echo "✓ done. Edit $CONFIG_FILE to add your API key, then run: codey"
