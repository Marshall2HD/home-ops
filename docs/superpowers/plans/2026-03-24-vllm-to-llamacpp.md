# Replace vLLM with llama.cpp — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the vLLM-stack deployment with a llama.cpp server using bjw-s app-template, serving Qwen3.5-27B-UD-Q4_K_XL via an OpenAI-compatible API.

**Architecture:** Single StatefulSet with an init container for model download and a CUDA-enabled llama-server main container. Uses the same bjw-s app-template pattern as all other custom apps in the cluster. LibreChat and Open-WebUI updated to point at the new service.

**Tech Stack:** llama.cpp (server-cuda), bjw-s app-template 4.6.2, Flux CD, NVIDIA GPU, Ceph block storage

**Spec:** `docs/superpowers/specs/2026-03-24-vllm-to-llamacpp-design.md`

---

### Task 1: Create llama app scaffolding

**Files:**
- Create: `kubernetes/apps/default/llama/ks.yaml`
- Create: `kubernetes/apps/default/llama/app/kustomization.yaml`
- Create: `kubernetes/apps/default/llama/app/ocirepository.yaml`

- [ ] **Step 1: Create `ks.yaml`**

Reference: `kubernetes/apps/default/speaches/ks.yaml` for structure, but note: no volsync component needed, and add `nvidia-device-plugin` dependency (like the existing vLLM `ks.yaml`).

```yaml
---
# yaml-language-server: $schema=https://kubernetes-schemas.pages.dev/kustomize.toolkit.fluxcd.io/kustomization_v1.json
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: llama
spec:
  dependsOn:
    - name: rook-ceph-cluster
      namespace: rook-ceph
    - name: nvidia-device-plugin
      namespace: kube-system
  interval: 1h
  path: ./kubernetes/apps/default/llama/app
  prune: true
  sourceRef:
    kind: GitRepository
    name: flux-system
    namespace: flux-system
  targetNamespace: default
  wait: false
```

- [ ] **Step 2: Create `app/kustomization.yaml`**

```yaml
---
# yaml-language-server: $schema=https://json.schemastore.org/kustomization
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ./ocirepository.yaml
  - ./helmrelease.yaml
```

- [ ] **Step 3: Create `app/ocirepository.yaml`**

Reference: `kubernetes/apps/default/qbittorrent/app/ocirepository.yaml`

```yaml
---
# yaml-language-server: $schema=https://kubernetes-schemas.pages.dev/source.toolkit.fluxcd.io/ocirepository_v1.json
apiVersion: source.toolkit.fluxcd.io/v1
kind: OCIRepository
metadata:
  name: llama
spec:
  interval: 15m
  layerSelector:
    mediaType: application/vnd.cncf.helm.chart.content.v1.tar+gzip
    operation: copy
  ref:
    tag: 4.6.2
  url: oci://ghcr.io/bjw-s-labs/helm/app-template
```

- [ ] **Step 4: Commit**

```bash
git add kubernetes/apps/default/llama/
git commit -m "feat(llama): add app scaffolding for llama.cpp deployment"
```

---

### Task 2: Create helmrelease.yaml

**Files:**
- Create: `kubernetes/apps/default/llama/app/helmrelease.yaml`

Reference patterns:
- GPU + probes + runtimeClassName: `kubernetes/apps/default/speaches/app/helmrelease.yaml`
- StatefulSet + volumeClaimTemplates: `kubernetes/apps/default/mosquitto/app/helmrelease.yaml`
- Route with sectionName: `kubernetes/apps/default/open-webui/app/helmrelease.yaml:62-65`

- [ ] **Step 1: Create the HelmRelease**

```yaml
---
# yaml-language-server: $schema=https://raw.githubusercontent.com/bjw-s-labs/helm-charts/main/charts/other/app-template/schemas/helmrelease-helm-v2.schema.json
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: &app llama
spec:
  chartRef:
    kind: OCIRepository
    name: llama
  interval: 1h
  values:
    controllers:
      llama:
        type: statefulset
        strategy: Recreate
        initContainers:
          download-model:
            image:
              repository: alpine
              tag: 3.21
            command: ["/bin/sh", "-c"]
            args:
              - |
                MODEL_PATH="/models/Qwen3.5-27B-UD-Q4_K_XL.gguf"
                MODEL_URL="https://huggingface.co/unsloth/Qwen3.5-27B-UD-GGUF/resolve/main/Qwen3.5-27B-UD-Q4_K_XL.gguf"
                echo "Checking/downloading model..."
                wget -c -O "$MODEL_PATH" "$MODEL_URL"
        containers:
          app:
            image:
              repository: ghcr.io/ggml-org/llama.cpp
              tag: server-cuda-b8496
            args:
              - "-m"
              - "/models/Qwen3.5-27B-UD-Q4_K_XL.gguf"
              - "--host"
              - "0.0.0.0"
              - "--port"
              - "8080"
              - "--ctx-size"
              - "100000"
              - "--n-gpu-layers"
              - "-1"
              - "--alias"
              - "Qwen3.5-27B"
              - "--temp"
              - "1.0"
              - "--top-p"
              - "0.95"
              - "--top-k"
              - "20"
              - "--min-p"
              - "0.0"
              - "--presence-penalty"
              - "1.5"
              - "--repeat-penalty"
              - "1.0"
            env:
              TZ: America/New_York
              NVIDIA_DRIVER_CAPABILITIES: all
              NVIDIA_VISIBLE_DEVICES: all
            probes:
              liveness:
                enabled: true
                custom: true
                spec:
                  httpGet:
                    path: /health
                    port: &port 8080
                  periodSeconds: 30
                  timeoutSeconds: 5
                  failureThreshold: 3
              startup:
                enabled: true
                custom: true
                spec:
                  httpGet:
                    path: /health
                    port: *port
                  initialDelaySeconds: 10
                  periodSeconds: 10
                  timeoutSeconds: 5
                  failureThreshold: 120
            resources:
              requests:
                cpu: 4
                memory: 2Gi
                nvidia.com/gpu: 1
              limits:
                memory: 24Gi
                nvidia.com/gpu: 1
        statefulset:
          volumeClaimTemplates:
            - name: models
              storageClass: ceph-block
              accessMode: ReadWriteOnce
              size: 30Gi
              globalMounts:
                - path: /models
    defaultPodOptions:
      runtimeClassName: nvidia
      nodeSelector:
        nvidia.feature.node.kubernetes.io/gpu: "true"
      tolerations:
        - key: nvidia.com/gpu
          operator: Exists
          effect: NoSchedule
    service:
      app:
        controller: *app
        ports:
          http:
            port: *port
    route:
      app:
        hostnames:
          - "{{ .Release.Name }}.hades.casa"
        parentRefs:
          - name: envoy-internal
            namespace: network
            sectionName: https
```

- [ ] **Step 2: Commit**

```bash
git add kubernetes/apps/default/llama/app/helmrelease.yaml
git commit -m "feat(llama): add helmrelease with llama.cpp server-cuda"
```

---

### Task 3: Update LibreChat to use llama

**Files:**
- Modify: `kubernetes/apps/default/librechat/app/helmrelease.yaml:57-65`

- [ ] **Step 1: Replace the VLLM custom endpoint block**

Change:
```yaml
            - name: "VLLM"
              apiKey: "none"
              baseURL: "http://vllm-vllm-stack-router.default.svc.cluster.local/v1"
              models:
                default: ["Qwen/Qwen3.5-9B"]
                fetch: true
              titleConvo: true
              titleModel: "Qwen/Qwen3.5-9B"
              modelDisplayLabel: "VLLM"
```

To:
```yaml
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

- [ ] **Step 2: Commit**

```bash
git add kubernetes/apps/default/librechat/app/helmrelease.yaml
git commit -m "feat(librechat): point custom endpoint to llama.cpp"
```

---

### Task 4: Update Open-WebUI to use llama

**Files:**
- Modify: `kubernetes/apps/default/open-webui/app/helmrelease.yaml:26`

- [ ] **Step 1: Update the OpenAI base URL**

Change:
```yaml
              OPENAI_API_BASE_URLS: "http://vllm-vllm-stack-router.default.svc.cluster.local/v1"
```

To:
```yaml
              OPENAI_API_BASE_URLS: "http://llama.default.svc.cluster.local:8080/v1"
```

- [ ] **Step 2: Commit**

```bash
git add kubernetes/apps/default/open-webui/app/helmrelease.yaml
git commit -m "feat(open-webui): point OpenAI API to llama.cpp"
```

---

### Task 5: Swap vLLM for llama in kustomization and remove vLLM

**Files:**
- Modify: `kubernetes/apps/default/kustomization.yaml:44`
- Delete: `kubernetes/apps/default/vllm/` (entire directory)

- [ ] **Step 1: Replace vllm with llama in kustomization.yaml**

Change line 44:
```yaml
  - ./vllm/ks.yaml
```

To:
```yaml
  - ./llama/ks.yaml
```

- [ ] **Step 2: Delete the vLLM directory**

```bash
rm -rf kubernetes/apps/default/vllm
```

- [ ] **Step 3: Commit**

```bash
git add kubernetes/apps/default/kustomization.yaml
git rm -r kubernetes/apps/default/vllm
git commit -m "refactor: replace vllm with llama.cpp deployment"
```
