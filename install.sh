#!/usr/bin/env sh
set -eu

OPAI_REPO_URL="${OPAI_REPO_URL:-https://github.com/MarcoLadeira/OPai.git}"
OPAI_BRANCH="${OPAI_BRANCH:-main}"
OPAI_INSTALL_ROOT="${OPAI_INSTALL_ROOT:-$HOME/.opai/source}"
WITH_TOOLS="${OPAI_WITH_TOOLS:-0}"
SHELL_ALIASES=1

for arg in "$@"; do
  if [ "$arg" = "--with-tools" ]; then
    WITH_TOOLS=1
  fi
  if [ "$arg" = "--no-tools" ]; then
    WITH_TOOLS=0
  fi
  if [ "$arg" = "--shell-aliases" ]; then
    SHELL_ALIASES=1
  fi
  if [ "$arg" = "--no-shell-aliases" ]; then
    SHELL_ALIASES=0
  fi
done

script_root=""
case "$0" in
  */*) script_root="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)" ;;
esac

if [ -n "$script_root" ] && [ -f "$script_root/pyproject.toml" ]; then
  ROOT="$script_root"
else
  if ! command -v git >/dev/null 2>&1; then
    printf "Git is required for the one-command OPai installer. Install Git, then re-run this command.\n" >&2
    exit 127
  fi

  if [ -d "$OPAI_INSTALL_ROOT/.git" ]; then
    git -C "$OPAI_INSTALL_ROOT" fetch origin "$OPAI_BRANCH"
    git -C "$OPAI_INSTALL_ROOT" checkout "$OPAI_BRANCH"
    git -C "$OPAI_INSTALL_ROOT" pull --ff-only origin "$OPAI_BRANCH"
  elif [ -e "$OPAI_INSTALL_ROOT" ]; then
    if [ ! -f "$OPAI_INSTALL_ROOT/pyproject.toml" ]; then
      printf "Install target exists but is not an OPai checkout: %s\n" "$OPAI_INSTALL_ROOT" >&2
      exit 1
    fi
  else
    mkdir -p "$(dirname "$OPAI_INSTALL_ROOT")"
    git clone --depth 1 --branch "$OPAI_BRANCH" "$OPAI_REPO_URL" "$OPAI_INSTALL_ROOT"
  fi
  ROOT="$OPAI_INSTALL_ROOT"
fi

export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"

python -m pip install -e "$ROOT" --no-deps

INSTALL_ARGS="--no-tools"
if [ "$WITH_TOOLS" = "1" ]; then
  INSTALL_ARGS="--with-tools"
fi
if [ "$SHELL_ALIASES" = "1" ]; then
  INSTALL_ARGS="$INSTALL_ARGS --shell-aliases"
fi

python -m opai --project "$ROOT" install $INSTALL_ARGS

printf "\nOPai 0.1.0 pre-alpha installed permanently.\n"
printf "Source: %s\n" "$ROOT"
printf "Restart terminals and AI clients once so aliases and skills reload.\n"
printf "Use in any repo: op status\n"
printf "Launch with OPai: op launch codex\n"
