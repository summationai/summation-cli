#!/usr/bin/env sh
# Install summation-cli (sumcli) via uv tool install.
# Usage:
#   curl -fsSL https://install.summation.com/sumcli | sh
# Optional:
#   SUMCLI_VERSION=X.Y.Z curl -fsSL https://install.summation.com/sumcli | sh
#   SUMCLI_PACKAGE=/path/to/checkout   # install from a local path (tests / dev)
set -eu

PACKAGE="${SUMCLI_PACKAGE:-summation-cli}"
BIN_NAME="sumcli"

say() { printf '%s\n' "$*"; }
err() { printf 'sumcli-install: %s\n' "$*" >&2; exit 1; }

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || err "missing required command: $1"
}

ensure_uv() {
  if command -v uv >/dev/null 2>&1; then
    return 0
  fi
  say "uv not found; installing via https://astral.sh/uv/install.sh"
  need_cmd curl
  # shellcheck disable=SC2312
  curl -LsSf https://astral.sh/uv/install.sh | sh

  # Common install locations when PATH hasn't been refreshed yet.
  if ! command -v uv >/dev/null 2>&1; then
    for candidate in \
      "${HOME}/.local/bin/uv" \
      "${HOME}/.cargo/bin/uv" \
      "${XDG_BIN_HOME:-}/uv"
    do
      if [ -n "${candidate}" ] && [ -x "${candidate}" ]; then
        PATH="$(dirname "${candidate}"):${PATH}"
        export PATH
        break
      fi
    done
  fi

  command -v uv >/dev/null 2>&1 || err "uv installed but not on PATH; open a new shell or add ~/.local/bin to PATH"
}

install_sumcli() {
  # --force reinstalls so a corrupt or stale tool venv is repaired instead of
  # silently no-op'ing with "already installed".
  if [ -n "${SUMCLI_VERSION:-}" ]; then
    say "Installing ${PACKAGE}==${SUMCLI_VERSION}"
    uv tool install --force "${PACKAGE}==${SUMCLI_VERSION}"
  else
    say "Installing ${PACKAGE} (latest)"
    uv tool install --force "${PACKAGE}"
  fi
}

same_bin() {
  # True if both paths are the same executable (string match or same device+inode).
  # test -ef follows symlinks, so a PATH entry that links to the uv shim counts as a match.
  [ "$1" = "$2" ] && return 0
  [ "$1" -ef "$2" ]
}

verify_sumcli() {
  bin_dir="$(uv tool dir --bin 2>/dev/null || printf '%s' "${HOME}/.local/bin")"
  installed="${bin_dir}/${BIN_NAME}"

  [ -x "${installed}" ] || err "install finished but ${installed} is missing or not executable"

  # Verify the binary we just installed, not whatever PATH resolves to.
  if ! "${installed}" --version; then
    err "${installed} is installed but fails to run"
  fi

  say "Installed. Try: ${BIN_NAME} --version"

  # A different sumcli earlier on PATH makes the good install unreachable.
  resolved="$(command -v "${BIN_NAME}" 2>/dev/null || true)"
  if [ -z "${resolved}" ]; then
    err "'${BIN_NAME}' is not on your PATH. Add ${bin_dir} to PATH, then open a new shell."
  fi
  if ! same_bin "${resolved}" "${installed}"; then
    err "another '${BIN_NAME}' comes first on your PATH:
  ${resolved}   <- your shell uses this one
  ${installed}   <- the one just installed
Remove the first program, or put ${bin_dir} earlier on PATH."
  fi
}

main() {
  case "$(uname -s)" in
    Darwin|Linux) ;;
    *) err "unsupported OS: $(uname -s) (macOS/Linux only; for Windows use https://install.summation.com/sumcli.ps1 or sumcli.cmd)" ;;
  esac

  ensure_uv
  install_sumcli
  verify_sumcli
}

main "$@"
