#!/usr/bin/env bash
set -euo pipefail

# Installs Kokoro into this repo's Poetry venv on Python 3.13 using system ROCm torch.
#
# Why this exists:
# - We want to use pacman/AUR-provided ROCm PyTorch (stable for your GPU arch).
# - `kokoro==0.9.4` depends on `misaki==0.9.4`, which declares Requires-Python <3.13.
#   In practice it can work on 3.13, but you must bypass that metadata.
# - We also want to avoid pip/Poetry pulling a PyPI CUDA torch wheel.
#
# This script intentionally uses `pip` inside the Poetry env, and pins versions.

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

if ! command -v poetry >/dev/null 2>&1; then
  echo "poetry not found in PATH" >&2
  exit 1
fi

if [[ ! -f pyproject.toml ]]; then
  echo "Run this from the repo root (missing pyproject.toml)" >&2
  exit 1
fi

echo "[kokoro-install] Using Poetry env: $(poetry env info -p)"

echo "[kokoro-install] Checking Python + torch visibility..."
poetry run python - <<'PY'
import sys
import importlib.util as u
print('python', sys.version)
assert u.find_spec('torch'), 'torch not importable (expected system ROCm torch via system-site-packages)'
import torch
print('torch', torch.__version__)
print('torch_file', torch.__file__)
print('hip', getattr(torch.version, 'hip', None))
print('cuda_available', torch.cuda.is_available())
print('device_count', torch.cuda.device_count())
PY

echo "[kokoro-install] Checking ROCm headers (rocrand)..."
if [[ ! -f /opt/rocm/include/rocrand/rocrand_xorwow.h ]]; then
  cat >&2 <<'ERR'
Missing /opt/rocm/include/rocrand/rocrand_xorwow.h

This usually means the ROCm RNG headers package is missing.
On Arch, install the relevant package (names vary): rocrand / rocm-rocrand.
ERR
  exit 1
fi

# Pinned versions we validated.
KOKORO_VER="0.9.4"
MISAKI_VER="0.9.4"
ADDICT_VER="2.4.0"
SPACY_VER="3.8.11"
TRANSFORMERS_VER="4.57.3"
HF_HUB_VER="0.36.0"
PHONEMIZER_FORK_VER="3.3.2"
ESPEAKNG_LOADER_VER="0.2.4"
NUM2WORDS_VER="0.5.14"

# Note: `transformers` does NOT hard-require torch installation via pip.
# We rely on system torch already being importable.

echo "[kokoro-install] Installing Kokoro runtime deps (pinned)..."
poetry run pip install --upgrade \
  "addict==${ADDICT_VER}" \
  "huggingface-hub==${HF_HUB_VER}" \
  "transformers==${TRANSFORMERS_VER}" \
  "spacy==${SPACY_VER}" \
  "phonemizer-fork==${PHONEMIZER_FORK_VER}" \
  "espeakng-loader==${ESPEAKNG_LOADER_VER}" \
  "num2words==${NUM2WORDS_VER}"

echo "[kokoro-install] Force-installing misaki/kokoro (bypass Requires-Python)..."
poetry run pip install --upgrade --no-deps --ignore-requires-python "misaki==${MISAKI_VER}"
poetry run pip install --upgrade --no-deps --ignore-requires-python "kokoro==${KOKORO_VER}"

echo "[kokoro-install] Ensuring spaCy English model is present..."
if poetry run python - <<'PY' >/dev/null 2>&1
import importlib.util as u
raise SystemExit(0 if u.find_spec('en_core_web_sm') else 1)
PY
then
  echo "[kokoro-install] spaCy model en_core_web_sm already installed"
else
  # This may print a lot and will install a compatible model for the installed spaCy.
  poetry run python -m spacy download en_core_web_sm
fi

echo "[kokoro-install] Sanity import..."
poetry run python - <<'PY'
from kokoro import KPipeline
print('kokoro import ok')
PY

echo "[kokoro-install] Optional: run GPU smoke test..."
OUT_WAV="/tmp/kokoro_gpu_py313.wav"
poetry run python scripts/test_kokoro_tts.py --device gpu --out "$OUT_WAV" || {
  echo "[kokoro-install] GPU smoke test failed (see logs above)." >&2
  exit 2
}

echo "[kokoro-install] OK: wrote $OUT_WAV"
