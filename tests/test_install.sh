#!/usr/bin/env sh
# Integration tests for scripts/install.sh.
# Reproduces PATH-shadowing and silent no-repair failures.
set -eu

REPO_ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
INSTALLER="${REPO_ROOT}/scripts/install.sh"
UV_BIN="$(command -v uv)" || {
  printf 'test_install: uv is required on PATH\n' >&2
  exit 1
}
UV_DIR="$(dirname "${UV_BIN}")"

pass=0
fail=0
TEST_HOME=""

assert_ok() {
  name="$1"
  shift
  # Run the body in a subshell with errexit so the first failing assertion
  # aborts only that test. Keep the subshell *outside* `if` — macOS sh
  # suppresses set -e inside if-conditions, including nested subshells.
  set +e
  ( set -e; "$@" )
  status=$?
  set -e
  if [ "${status}" -eq 0 ]; then
    printf 'PASS  %s\n' "${name}"
    pass=$((pass + 1))
  else
    printf 'FAIL  %s\n' "${name}" >&2
    fail=$((fail + 1))
  fi
}

setup_isolated_env() {
  TEST_HOME="$(mktemp -d)"
  export HOME="${TEST_HOME}"
  export XDG_DATA_HOME="${TEST_HOME}/.local/share"
  export XDG_CONFIG_HOME="${TEST_HOME}/.config"
  export XDG_CACHE_HOME="${TEST_HOME}/.cache"
  export XDG_BIN_HOME="${TEST_HOME}/.local/bin"
  mkdir -p "${XDG_BIN_HOME}"
  # Isolated bin dir FIRST so a host ~/.local/bin/sumcli cannot shadow the
  # install under test. Keep the real uv available after that.
  export PATH="${XDG_BIN_HOME}:${UV_DIR}:/usr/bin:/bin:/usr/sbin:/sbin"
  # Cleanup must run in this subshell (TEST_HOME is not visible to the parent).
  trap teardown_isolated_env EXIT
}

teardown_isolated_env() {
  if [ -n "${TEST_HOME}" ] && [ -d "${TEST_HOME}" ]; then
    rm -rf "${TEST_HOME}"
  fi
  TEST_HOME=""
}

run_clean_install() {
  setup_isolated_env
  SUMCLI_PACKAGE="${REPO_ROOT}" sh "${INSTALLER}"
  resolved="$(command -v sumcli)"
  installed="$(uv tool dir --bin)/sumcli"
  [ "${resolved}" = "${installed}" ]
  sumcli --version >/dev/null
}

run_shadow_detected() {
  setup_isolated_env
  shadow="${TEST_HOME}/shadow"
  mkdir -p "${shadow}"
  printf '#!/bin/sh\nprintf "broken-shadow\\n"\nexit 1\n' >"${shadow}/sumcli"
  chmod +x "${shadow}/sumcli"
  # Broken binary ahead of the isolated uv tool bin dir.
  export PATH="${shadow}:${XDG_BIN_HOME}:${UV_DIR}:/usr/bin:/bin:/usr/sbin:/sbin"

  out="$(mktemp)"
  set +e
  SUMCLI_PACKAGE="${REPO_ROOT}" sh "${INSTALLER}" >"${out}" 2>&1
  status=$?
  set -e

  ok=1
  [ "${status}" -ne 0 ] || ok=0
  grep -q "${shadow}/sumcli" "${out}" || ok=0
  grep -q "comes first on your PATH" "${out}" || ok=0

  # Good binary must still have been installed despite the hard fail.
  installed="$(uv tool dir --bin)/sumcli"
  [ -x "${installed}" ] || ok=0
  "${installed}" --version >/dev/null || ok=0

  if [ "${ok}" -ne 1 ]; then
    sed 's/^/  | /' "${out}" >&2 || true
  fi
  rm -f "${out}"
  [ "${ok}" -eq 1 ]
}

run_repair_corrupt_venv() {
  setup_isolated_env

  SUMCLI_PACKAGE="${REPO_ROOT}" sh "${INSTALLER}"
  tool_dir="$(uv tool dir)/summation-cli"
  # Corrupt the tool environment the way a partial/stale install might.
  rm -rf "${tool_dir}/lib"
  # Confirm it is actually broken before repair.
  set +e
  sumcli --version >/dev/null 2>&1
  broken=$?
  set -e
  [ "${broken}" -ne 0 ]

  # Without --force this would no-op; with --force it must repair.
  SUMCLI_PACKAGE="${REPO_ROOT}" sh "${INSTALLER}"
  sumcli --version >/dev/null
}

assert_ok "clean install" run_clean_install
assert_ok "PATH shadow detected" run_shadow_detected
assert_ok "repairs corrupt tool venv" run_repair_corrupt_venv

printf '\n%d passed, %d failed\n' "${pass}" "${fail}"
[ "${fail}" -eq 0 ]
