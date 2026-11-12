#!/bin/bash
# Removes what "Install mocapapp.command" created. Session folders (your data) are never touched.
# The pose models in ~/.cache/rtmlib are kept unless MOCAPAPP_REMOVE_MODELS=1 (other installs may share them).
PREFIX="${MOCAPAPP_PREFIX:-$HOME/Library/Application Support/mocapapp}"
rm -rf "$HOME/Applications/mocapapp.app"
for name in venv python uv src cache install.json install.log; do rm -rf "${PREFIX:?}/$name"; done
rmdir "$PREFIX" 2>/dev/null || true
[ -n "${MOCAPAPP_REMOVE_MODELS:-}" ] && rm -rf "$HOME/.cache/rtmlib"
echo "mocapapp was removed. Your session folders were not touched."
read -r -p "Press Enter to close this window " _ || true
