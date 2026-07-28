#!/usr/bin/env sh
# Install summation-cli (sumcli) via uv tool install.
# Usage:
#   curl -fsSL https://install.summation.com/sumcli | sh
# Optional:
#   SUMCLI_VERSION=X.Y.Z curl -fsSL https://install.summation.com/sumcli | sh
set -eu

PACKAGE="summation-cli"
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
  if [ -n "${SUMCLI_VERSION:-}" ]; then
    say "Installing ${PACKAGE}==${SUMCLI_VERSION}"
    uv tool install "${PACKAGE}==${SUMCLI_VERSION}"
  else
    say "Installing ${PACKAGE} (latest)"
    uv tool install "${PACKAGE}"
  fi
}

main() {
  case "$(uname -s)" in
    Darwin|Linux) ;;
    *) err "unsupported OS: $(uname -s) (macOS/Linux only; for Windows use https://install.summation.com/sumcli.ps1)" ;;
  esac

  ensure_uv
  install_sumcli

  if command -v "${BIN_NAME}" >/dev/null 2>&1; then
    say "Installed. Try: ${BIN_NAME} --version"
    "${BIN_NAME}" --version || true
  else
    say "Installed ${PACKAGE}."
    say "If '${BIN_NAME}' is not found, ensure uv's tool bin dir is on PATH (often ~/.local/bin)."
    say "Upgrade later with: uv tool upgrade ${PACKAGE}"
  fi
}

main "$@"
