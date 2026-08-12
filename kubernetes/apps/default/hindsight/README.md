# Hindsight home-ops draft

Draft only. This directory is **not wired into** `kubernetes/apps/default/kustomization.yaml`, so pushing the branch will not deploy Hindsight until we explicitly add `./hindsight/ks.yaml` there. Cute little safety rail, because deploying memory infrastructure by accident would be performance art.

## Shape

- Namespace: `default`, matching the app stack.
- API/UI chart: `ghcr.io/vectorize-io/charts/hindsight:0.6.2`.
- API image: `ghcr.io/vectorize-io/hindsight-api:0.6.2`.
- Control plane image: `ghcr.io/vectorize-io/hindsight-control-plane:0.6.2`.
- Postgres: standalone `tensorchord/vchord-suite:pg18-20260501`, not the chart's toy bundled Postgres, so we keep `vchord`, BM25, and the `llmlingua2` tokenizer behavior from Thanatos.
- Storage: `hindsight-postgres` PVC created by the repo's VolSync component, backed up hourly to Kopia like the rest of the stateful app data.
- Routes: internal Envoy only.
  - UI: `https://ui.hindsight.hades.casa`
  - API: `https://hindsight.hades.casa`
- API/control-plane deployments are post-rendered to `maxSurge: 0` / `maxUnavailable: 1` so this single-node cluster doesn't double-schedule memory requests during rollouts, because Kubernetes loves being dramatic.
- Blackbox proxy: stores every LLM request/response body locally for prompt debugging and training data mining. Flow: `hindsight-api -> hindsight-blackbox:8788 -> metaxu.nix.casa:443`
- API initContainer patches `migrations.py` to fix a `USER-DEFINED` type detection bug in Hindsight 0.6.2.

## Required 1Password item

Create/update the 1Password item `hindsight` in the Kubernetes vault. Required fields:

- `POSTGRES_PASSWORD`
- `VOYAGE_API_KEY`
- `HINDSIGHT_CP_ACCESS_KEY`

The generated Kubernetes Secret is `hindsight-secret`. It includes both `POSTGRES_PASSWORD` for the standalone StatefulSet and `postgres-password` for the upstream chart's hardcoded secretKeyRef. `POSTGRES_PASSWORD_URI` is derived in the ExternalSecret template with `urlquery`, so the raw password can still be a normal 1Password-generated value without breaking the PostgreSQL URI.

## Deliberate differences from Thanatos local env

- No `127.0.0.1` endpoints. Pods cannot use Thanatos-local loopback, obviously.
- LLM base URL is `http://hindsight-blackbox.default.svc.cluster.local:8788`, not direct metaxu. Every request/response is captured in the blackbox PVC.
- OTEL/Langfuse is disabled for now because the current Langfuse endpoint is Thanatos-local. Re-enable only after Langfuse has a routable in-cluster or internal URL.
- No `HINDSIGHT_API_LLM_EXTRA_BODY`, so no direct reasoning injection. Metaxu thinking stays controlled by Metaxu, not Hindsight.
- `HINDSIGHT_API_WORKER_ENABLED=false` for the local Flux trial so it does not chew tokens just by existing. Enable intentionally during cutover.

## Blackbox proxy

Hindsight LLM traffic flows through a local blackbox proxy so every request/response body is captured for debugging and training data extraction.

### Flow
```
Hindsight API  ->  hindsight-blackbox:8788  ->  metaxu.nix.casa (via HTTPS)
       |                      |
       +-- captures full request/response bodies as gzip files
       +-- indexes metadata in SQLite + JSONL
```

### Access
- **API logs**: `kubectl exec deployment/hindsight-blackbox -- tail /data/calls.jsonl`
- **SQLite index**: `kubectl exec deployment/hindsight-blackbox -- sqlite3 /data/index.sqlite3 "SELECT * FROM calls ORDER BY started_at DESC LIMIT 5;"`
- **Body files**: `/data/bodies/YYYY/MM/DD/` in the blackbox pod

### Why this exists
Langfuse traces are sampled and structured. The blackbox is 100% raw capture of every LLM request/response, including failed ones. This is essential for prompt debugging and training data mining.

## Cutover checklist

1. Restore the existing Thanatos Hindsight DB via logical dump/restore into `hindsight-postgres`. Do not raw-copy the Docker volume.
2. Verify extensions inside the pod: `vector`, `vchord`, `pg_tokenizer`, `vchord_bm25`, and tokenizer `llmlingua2`.
3. Add `- ./hindsight/ks.yaml` to `kubernetes/apps/default/kustomization.yaml`.
4. Force Flux reconcile.
5. Verify `curl -fsS https://hindsight.hades.casa/health` and `curl -I https://ui.hindsight.hades.casa/`.
6. Point Hermes Hindsight config at `https://hindsight.hades.casa`.
7. Keep Thanatos local Hindsight stopped, not deleted, until recall/retain/consolidation all pass.
