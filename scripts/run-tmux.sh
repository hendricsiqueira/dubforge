#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
SESSION_NAME="${DUBFORGE_TMUX_SESSION:-dubforge}"
PYTHON_BIN="${DUBFORGE_PYTHON:-python}"

if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
  echo "DubForge já está rodando na sessão tmux: $SESSION_NAME"
  exit 0
fi

tmux new-session -d -s "$SESSION_NAME" \
  "cd '$PROJECT_DIR' && exec '$PYTHON_BIN' app.py >> dubforge.log 2>&1"

echo "DubForge iniciado na sessão tmux: $SESSION_NAME"
echo "Acompanhe com: tmux attach -t $SESSION_NAME"
