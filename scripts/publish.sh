#!/usr/bin/env sh
# Build and publish summation-cli to PyPI.
#
# This is the release path for this repository. There is no CI publish workflow —
# run this script deliberately when ready. It builds from the current working
# tree (including uncommitted changes) and does not enforce merge or tag gates.
#
# Usage (from repo root):
#   ./scripts/publish.sh                # TestPyPI (default)
#   ./scripts/publish.sh --test         # TestPyPI (explicit)
#   ./scripts/publish.sh --production   # real PyPI
#
# Credentials (token values must include the pypi- prefix):
#   TestPyPI     UV_PUBLISH_PASSWORD_TEST
#   Production   UV_PUBLISH_PASSWORD
# Optional:
#   UV_PUBLISH_USERNAME (defaults to __token__)
#
# Uploads are permanent: a version cannot be replaced or re-uploaded once published.
set -eu

PACKAGE="summation-cli"
TARGET="test"

err() { printf 'publish: %s\n' "$*" >&2; exit 1; }
say() { printf 'publish: %s\n' "$*"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --test) TARGET="test" ;;
    --production|--prod) TARGET="production" ;;
    -h|--help) sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) err "unknown option: $1 (use --test or --production)" ;;
  esac
  shift
done

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PACKAGE_DIR="$(CDPATH= cd -- "${SCRIPT_DIR}/.." && pwd)"

if [ "${TARGET}" = "production" ]; then
  PUBLISH_URL="https://upload.pypi.org/legacy/"
  INDEX_NAME="PyPI (production)"
  TOKEN="${UV_PUBLISH_PASSWORD:-}"
  TOKEN_VAR="UV_PUBLISH_PASSWORD"
else
  PUBLISH_URL="https://test.pypi.org/legacy/"
  INDEX_NAME="TestPyPI"
  TOKEN="${UV_PUBLISH_PASSWORD_TEST:-}"
  TOKEN_VAR="UV_PUBLISH_PASSWORD_TEST"
fi

[ -n "${TOKEN}" ] || err "${TOKEN_VAR} is unset. Export it before running."
command -v uv >/dev/null 2>&1 || err "uv is required (https://docs.astral.sh/uv/)"

cd "${PACKAGE_DIR}"
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' sum_cli/__init__.py)"
[ -n "${VERSION}" ] || err "could not read __version__ from sum_cli/__init__.py"

# Production uploads are irreversible, so make the operator confirm the exact version.
if [ "${TARGET}" = "production" ]; then
  say "About to publish ${PACKAGE} ${VERSION} to ${INDEX_NAME}."
  say "This is permanent: ${VERSION} can never be replaced or re-uploaded."
  say "Builds from the current working tree — commit and push first if that matters."
  printf 'publish: type the version to continue: '
  read -r reply
  [ "${reply}" = "${VERSION}" ] || err "got '${reply}', expected '${VERSION}'. Aborted."
fi

say "building ${PACKAGE} ${VERSION} in ${PACKAGE_DIR}"
rm -rf dist
uv build

say "uploading dist/* to ${INDEX_NAME}"
UV_PUBLISH_USERNAME="${UV_PUBLISH_USERNAME:-__token__}"
export UV_PUBLISH_USERNAME
UV_PUBLISH_PASSWORD="${TOKEN}"
export UV_PUBLISH_PASSWORD
uv publish --publish-url "${PUBLISH_URL}" dist/*

say "done: ${PACKAGE} ${VERSION} -> ${INDEX_NAME}"

printf '\nVerify:\n'
if [ "${TARGET}" = "production" ]; then
  printf '  cd /tmp && UV_NO_CONFIG=1 uv tool install --force %s\n' "${PACKAGE}"
else
  printf '  cd /tmp\n'
  printf '  UV_NO_CONFIG=1 uv tool install --default-index https://test.pypi.org/simple/ --index https://pypi.org/simple/ --index-strategy unsafe-best-match --force %s\n' "${PACKAGE}"
fi
printf '  sumcli --version\n'
