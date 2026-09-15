#!/usr/bin/env sh
set -eu

# Vesta was called OPai. Adopt old OPAI_* installer settings whose VESTA_*
# counterpart is unset (see vesta/legacy.py for the full list of legacy names).
for name in REPO_URL BRANCH INSTALL_ROOT PROJECT_ROOT PYTHON WITH_TOOLS NO_SUPERPOWERS; do
  eval "legacy_value=\${OPAI_$name:-}"
  eval "current_set=\${VESTA_$name+x}"
  if [ -n "$legacy_value" ] && [ -z "$current_set" ]; then
    eval "VESTA_$name=\$legacy_value"
  fi
done
# An install from before the rename lives in ~/.opai/source and the desktop app
# may be running from it: update it in place rather than cloning a second copy.
if [ -z "${VESTA_INSTALL_ROOT:-}" ] && [ ! -e "$HOME/.vesta/source" ] && [ -d "$HOME/.opai/source/.git" ]; then
  VESTA_INSTALL_ROOT="$HOME/.opai/source"
fi

VESTA_REPO_URL="${VESTA_REPO_URL:-https://github.com/MarcoLadeira/OPai.git}"
VESTA_BRANCH="${VESTA_BRANCH:-main}"
VESTA_INSTALL_ROOT="${VESTA_INSTALL_ROOT:-$HOME/.vesta/source}"
VESTA_PROJECT_ROOT="${VESTA_PROJECT_ROOT:-$(pwd)}"
VESTA_PYTHON="${VESTA_PYTHON:-python}"
WITH_TOOLS="${VESTA_WITH_TOOLS:-0}"
NO_SUPERPOWERS="${VESTA_NO_SUPERPOWERS:-0}"
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
  if [ "$arg" = "--no-superpowers" ]; then
    NO_SUPERPOWERS=1
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
    printf "Git is required for the one-command Vesta installer. Install Git, then re-run this command.\n" >&2
    exit 127
  fi

  if [ -d "$VESTA_INSTALL_ROOT/.git" ]; then
    git -C "$VESTA_INSTALL_ROOT" fetch origin "$VESTA_BRANCH"
    git -C "$VESTA_INSTALL_ROOT" checkout "$VESTA_BRANCH"
    git -C "$VESTA_INSTALL_ROOT" pull --ff-only origin "$VESTA_BRANCH"
  elif [ -e "$VESTA_INSTALL_ROOT" ]; then
    if [ ! -f "$VESTA_INSTALL_ROOT/pyproject.toml" ]; then
      printf "Install target exists but is not a Vesta checkout: %s\n" "$VESTA_INSTALL_ROOT" >&2
      exit 1
    fi
  else
    mkdir -p "$(dirname "$VESTA_INSTALL_ROOT")"
    git clone --depth 1 --branch "$VESTA_BRANCH" "$VESTA_REPO_URL" "$VESTA_INSTALL_ROOT"
  fi
  ROOT="$VESTA_INSTALL_ROOT"
fi

export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"

"$VESTA_PYTHON" -m pip install -e "$ROOT"

INSTALL_ARGS="--no-tools"
if [ "$WITH_TOOLS" = "1" ]; then
  INSTALL_ARGS="--with-tools"
fi
if [ "$NO_SUPERPOWERS" = "1" ]; then
  INSTALL_ARGS="$INSTALL_ARGS --no-superpowers"
fi
if [ "$SHELL_ALIASES" = "1" ]; then
  INSTALL_ARGS="$INSTALL_ARGS --shell-aliases"
fi

"$VESTA_PYTHON" -m vesta install --project "$VESTA_PROJECT_ROOT" $INSTALL_ARGS

INSTALLED_VERSION="$("$VESTA_PYTHON" -m vesta version 2>/dev/null || echo "Vesta installed")"
printf "\n%s installed permanently.\n" "$INSTALLED_VERSION"
printf "Source: %s\n" "$ROOT"
printf "Activated project: %s\n" "$VESTA_PROJECT_ROOT"
printf "Restart terminals and AI clients once so aliases and skills reload.\n"
printf "Use in any repo: op status\n"
printf "Launch with Vesta: op launch codex\n"
