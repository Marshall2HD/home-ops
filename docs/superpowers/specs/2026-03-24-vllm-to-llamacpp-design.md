# Replace vLLM with llama.cpp

## Summary

Replace the vLLM-stack Helm chart deployment with a llama.cpp server deployment using the bjw-s app-template pattern. Switch from the Qwen 3.5 9B (safetensors/FP8) model to Qwen3.5-27B-UD-Q4_K_XL (GGUF/Q4) served via llama.cpp's OpenAI-compatible API.

## Motivation

llama.cpp provides a lighter-weight inference server for GGUF-quantized models. The switch enables running the larger Qwen3.5-27B model with Q4 quantization, replacing the vLLM production stack (which includes a router, serving engine, and heavier infrastructure) with a single-container server.

## Architecture

### New deployment: `llama`

**Location:** `kubernetes/apps/default/llama/`

```
kubernetes/apps/default/llama/
├── ks.yaml                    # Flux Kustomization
└── app/
    ├── kustomization.yaml     # Component kustomization
    ├── ocirepository.yaml     # bjw-s app-template 4.6.2
    └── helmrelease.yaml       # HelmRelease
```

**Deployment pattern:** bjw-s app-template (same as qbittorrent, open-webui, etc.)

### Containers

**Init container** (`alpine`):
- Checks if `/models/Qwen3.5-27B-UD-Q4_K_XL.gguf` exists on PVC
- If missing, downloads from `https://huggingface.co/unsloth/Qwen3.5-27B-UD-GGUF/resolve/main/Qwen3.5-27B-UD-Q4_K_XL.gguf`
- Uses `wget -c` for resume support on interrupted downloads
- Exits non-zero on failure so the pod restarts and retries
- Skips download if file already present (fast restart)

**Main container** (`ghcr.io/ggml-org/llama.cpp:server-cuda`):
- Runs `llama-server` with:
  - `-m /models/Qwen3.5-27B-UD-Q4_K_XL.gguf`
  - `--host 0.0.0.0 --port 8080`
  - `--ctx-size 100000`
  - `--n-gpu-layers -1` (all layers on GPU)
  - `--alias Qwen3.5-27B`
  - Sampling defaults: `--temp 1.0 --top-p 0.95 --top-k 20 --min-p 0.0 --presence-penalty 1.5 --repeat-penalty 1.0`
- Resources:
  - CPU: 4 (request)
  - Memory: 2Gi (request), 24Gi (limit)
  - GPU: `nvidia.com/gpu: 1` (request and limit)
- Health probes:
  - Startup probe on `/health` with generous threshold (model loading takes time)
  - Liveness probe on `/health`

### Storage

- **30Gi PVC** (ReadWriteOnce, `ceph-block` storage class) via volumeClaimTemplate
- Model file is ~18GB; 30Gi provides headroom
- No volsync backup needed — model can be re-downloaded by init container

### GPU scheduling

- `runtimeClassName: nvidia`
- Node selector: `nvidia.feature.node.kubernetes.io/gpu: "true"`
- Toleration: `nvidia.com/gpu` NoSchedule
- Strategy: Recreate (single GPU workload)

### Networking

- Service port: 8080
- HTTPRoute: `llama.hades.casa` via `envoy-internal` gateway (`sectionName: https`)
- OpenAI-compatible API at `/v1/chat/completions`, `/v1/completions`, `/v1/models`

### Secrets

No ExternalSecret needed. The unsloth GGUF repository is public.

## Client updates

### LibreChat (`kubernetes/apps/default/librechat/app/helmrelease.yaml`)

Replace the "VLLM" custom endpoint:

```yaml
# Before
- name: "VLLM"
  apiKey: "none"
  baseURL: "http://vllm-vllm-stack-router.default.svc.cluster.local/v1"
  models:
    default: ["Qwen/Qwen3.5-9B"]
    fetch: true
  titleModel: "Qwen/Qwen3.5-9B"
  modelDisplayLabel: "VLLM"

# After
- name: "Llama"
  apiKey: "none"
  baseURL: "http://llama.default.svc.cluster.local:8080/v1"
  models:
    default: ["Qwen3.5-27B"]
    fetch: true
  titleConvo: true
  titleModel: "Qwen3.5-27B"
  modelDisplayLabel: "Llama"
```

### Open-WebUI (`kubernetes/apps/default/open-webui/app/helmrelease.yaml`)

```yaml
# Before
OPENAI_API_BASE_URLS: "http://vllm-vllm-stack-router.default.svc.cluster.local/v1"

# After
OPENAI_API_BASE_URLS: "http://llama.default.svc.cluster.local:8080/v1"
```

## Cleanup

- Delete `kubernetes/apps/default/vllm/` directory entirely
- Update `kubernetes/apps/default/kustomization.yaml`: replace `./vllm/ks.yaml` with `./llama/ks.yaml`

## Dependencies

The Flux Kustomization depends on:
- `rook-ceph-cluster` (rook-ceph namespace) — persistent storage
- `nvidia-device-plugin` (kube-system namespace) — GPU access
