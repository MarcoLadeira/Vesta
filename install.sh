#!/usr/bin/env sh
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"

python -m pip install -e "$ROOT" --no-deps

WITH_TOOLS=0
SHELL_ALIASES=0
for arg in "$@"; do
  if [ "$arg" = "--with-tools" ]; then
    WITH_TOOLS=1
  fi
  if [ "$arg" = "--shell-aliases" ]; then
    SHELL_ALIASES=1
  fi
done

INSTALL_ARGS="--no-tools"
if [ "$WITH_TOOLS" = "1" ]; then
  INSTALL_ARGS="--with-tools"
fi
if [ "$SHELL_ALIASES" = "1" ]; then
  INSTALL_ARGS="$INSTALL_ARGS --shell-aliases"
fi

python -m opai --project "$ROOT" install $INSTALL_ARGS

printf "\nOPai 0.1.0 pre-alpha installed.\nNext: op activate --repair\n"
