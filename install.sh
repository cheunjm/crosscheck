#!/usr/bin/env bash
set -euo pipefail

HOOK_DIR="$HOME/.claude/hooks"
CONFIG_DIR="$HOME/.claude"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "crosscheck installer"
echo "===================="
echo ""

# Create directories
mkdir -p "$HOOK_DIR"

# Copy hook script
cp "$SCRIPT_DIR/crosscheck.py" "$HOOK_DIR/crosscheck.py"
chmod +x "$HOOK_DIR/crosscheck.py"
echo "Installed hook: $HOOK_DIR/crosscheck.py"

# Copy config if not already present
if [ ! -f "$CONFIG_DIR/crosscheck.json" ]; then
    cp "$SCRIPT_DIR/crosscheck.json" "$CONFIG_DIR/crosscheck.json"
    echo "Installed config: $CONFIG_DIR/crosscheck.json"
else
    echo "Config already exists: $CONFIG_DIR/crosscheck.json (skipped)"
fi

echo ""
echo "Add the following to your ~/.claude/settings.json under \"hooks\":"
echo ""
cat <<'SETTINGS'
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Edit|Write|NotebookEdit",
        "hooks": [
          {
            "type": "command",
            "command": "python3 ~/.claude/hooks/crosscheck.py"
          }
        ]
      }
    ]
  }
}
SETTINGS
echo ""
echo "Done. Verify your setup:"
echo "  python3 ~/.claude/hooks/crosscheck.py --test"
echo ""
echo "Default model: Ollama with qwen2.5:14b at localhost:11434"
echo "Edit ~/.claude/crosscheck.json to change model, endpoint, or thresholds."
