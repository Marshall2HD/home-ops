#!/bin/bash
set -euo pipefail
if ! command -v gcc >/dev/null; then
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends gcc g++
fi
export PYTHONPATH=/data/source
export HF_HOME=/data/huggingface
export TORCHINDUCTOR_CACHE_DIR=/data/torchinductor
export TRITON_CACHE_DIR=/data/triton
if [[ ! -f /data/venv/.ready-v2 ]]; then
    python -m venv --system-site-packages /data/venv
    /data/venv/bin/python -m pip install 'torchtune==0.4.0' 'torchao==0.9.0' 'moshi==0.2.12' 'transformers==4.49.0' 'tokenizers==0.21.0' 'huggingface_hub==0.28.1' 'fastapi==0.115.12' 'uvicorn==0.34.3' 'soundfile==0.13.1' 'numpy<2' --extra-index-url https://download.pytorch.org/whl/cu128 --constraint /app/constraints.txt
    touch /data/venv/.ready-v2
fi
mkdir -p /data/source
if [[ ! -f /data/source/models.py ]]; then
    python -c 'import urllib.request; urllib.request.urlretrieve("https://raw.githubusercontent.com/SesameAILabs/csm/daed31e6d42cf71873999075de204fa37d2acec3/models.py", "/data/source/models.py")'
fi
exec /data/venv/bin/python -m uvicorn server:app --app-dir /app --host 0.0.0.0 --port 8000
