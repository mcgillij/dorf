#!/usr/bin/env bash
set -euo pipefail

# Uninstalls the Kokoro stack that was installed via:
#   scripts/install_kokoro_py313_rocm.sh
# and then re-syncs the environment to poetry.lock.
#
# Usage:
#   ./scripts/uninstall_kokoro_py313_rocm.sh
#   DRY_RUN=1 ./scripts/uninstall_kokoro_py313_rocm.sh

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

if ! command -v poetry >/dev/null 2>&1; then
  echo "poetry not found in PATH" >&2
  exit 1
fi

DRY_RUN="${DRY_RUN:-0}"

run() {
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "+ $*"
  else
    "$@"
  fi
}

echo "[kokoro-uninstall] Using Poetry env: $(poetry env info -p)"

# Packages that the installer script pins/installs.
# Note: this intentionally includes transitive deps (e.g. tokenizers/safetensors)
# so that after uninstall, `poetry install` rehydrates the locked set.
PKGS=(
  kokoro
  misaki
  addict
  transformers
  tokenizers
  safetensors
  spacy
  spacy-legacy
  spacy-loggers
  thinc
  srsly
  cymem
  preshed
  murmurhash
  weasel
  blis
  catalogue
  confection
  cloudpathlib
  smart-open
  wrapt
  typer-slim
  phonemizer-fork
  espeakng-loader
  num2words
  docopt
  dlinfo
  joblib
  segments
  csvw
  rdflib
  rfc3986
  uritemplate
  isodate
  language-tags
  en-core-web-sm
)

# Only attempt to uninstall things that are actually installed.
echo "[kokoro-uninstall] Detecting installed packages to remove..."
installed=()
for p in "${PKGS[@]}"; do
  if poetry run python - <<PY >/dev/null 2>&1
import importlib.util as u
# Map pip-style names to import module names for common cases
name = "${p}"
imports = {
  "en-core-web-sm": "en_core_web_sm",
  "phonemizer-fork": "phonemizer",
  "espeakng-loader": "espeakng_loader",
  "spacy-legacy": "spacy_legacy",
  "spacy-loggers": "spacy_loggers",
  "typer-slim": "typer",
  "cloudpathlib": "cloudpathlib",
  "smart-open": "smart_open",
}
mod = imports.get(name, name.replace('-', '_'))
raise SystemExit(0 if u.find_spec(mod) else 1)
PY
  then
    installed+=("$p")
  fi
done

echo "[kokoro-uninstall] Will uninstall: ${installed[*]:-(nothing found)}"

if [[ "${#installed[@]}" -gt 0 ]]; then
  echo "[kokoro-uninstall] Uninstalling..."
  run poetry run pip uninstall -y "${installed[@]}"
fi

echo "[kokoro-uninstall] Re-syncing venv to poetry.lock (restores locked deps if needed)..."
run poetry install

echo "[kokoro-uninstall] Done"
