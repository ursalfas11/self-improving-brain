#!/bin/sh
# self-improving-brain installer: one line, no root, no dependencies beyond Python 3.9+ and git.
#   curl -fsSL https://raw.githubusercontent.com/ursalfas11/self-improving-brain/main/install.sh | sh
# Installs into ~/.local/share/self-improving-brain, links `brain` into ~/.local/bin, then runs `brain setup`.
set -eu

REPO="${BRAIN_REPO:-https://github.com/ursalfas11/self-improving-brain.git}"
DEST="${BRAIN_HOME:-$HOME/.local/share/self-improving-brain}"
BIN="$HOME/.local/bin"

say() { printf '\033[1m%s\033[0m\n' "$*"; }
warn() { printf '\033[33m!\033[0m %s\n' "$*"; }
die() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

command -v git >/dev/null 2>&1 || die "git is required"
PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
    PY="$c"; break
  fi
done
[ -n "$PY" ] || die "Python 3.9 or newer is required (python.org, brew install python, apt install python3)"

if [ -d "$DEST/.git" ]; then
  say "Updating $DEST"
  git -C "$DEST" pull --ff-only -q || die "could not update $DEST (local changes?) — see: git -C \"$DEST\" status"
else
  say "Installing into $DEST"
  mkdir -p "$(dirname "$DEST")"
  git clone -q --depth 1 "$REPO" "$DEST" || die "could not download $REPO"
fi
chmod +x "$DEST/brain"

# link `brain`, but never replace a file that belongs to something else
mkdir -p "$BIN"
if [ ! -e "$BIN/brain" ] && [ ! -L "$BIN/brain" ]; then
  ln -s "$DEST/brain" "$BIN/brain"
fi
if [ -L "$BIN/brain" ] && [ "$(readlink "$BIN/brain")" = "$DEST/brain" ]; then
  case ":$PATH:" in
    *":$BIN:"*) ;;
    *)
      case "$(basename "${SHELL:-sh}")" in
        fish) hint="fish_add_path ~/.local/bin" ;;
        zsh)  hint="echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.zshrc" ;;
        bash) if [ "$(uname)" = Darwin ]; then rc=.bash_profile; else rc=.bashrc; fi
              hint="echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/$rc" ;;
        *)    hint="echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.profile" ;;
      esac
      say "Add ~/.local/bin to your PATH:  $hint" ;;
  esac
else
  warn "$BIN/brain already exists and is not this brain — left untouched. Use $DEST/brain instead."
fi

# setup asks nothing, so it runs without a terminal too (an agent installing it for you); stdin is the pipe of
# `curl … | sh`, so it gets none
"$PY" "$DEST/brain" setup </dev/null
