# Langfuse home-ops draft

This is an inert draft for moving Langfuse into home-ops. It is **not active** until `./langfuse/ks.yaml` is added to `kubernetes/apps/default/kustomization.yaml`, so no cluster rollout happens just because these files exist. Drama avoided, allegedly.

## Shape

- Chart: `langfuse/langfuse` from `https://langfuse.github.io/langfuse-k8s`, pinned at chart `1.5.32` / app `3.175.0`.
- Route: `https://langfuse.hades.casa`, internal Envoy only.
- Web service: `langfuse-web:3000`.
- Data stores deployed by the chart:
  - PostgreSQL, metadata/control-plane state, existing PVC `langfuse-postgresql`, restored/created by the repo VolSync component at 1Gi.
  - ClickHouse, traces/events/log-like observability data, chart-managed StatefulSet claim at 10Gi.
  - MinIO/S3, blob/event payload storage, chart-managed PVC `langfuse-s3` at 5Gi.
  - Valkey/Redis, queue/cache, chart-managed StatefulSet claim at 1Gi.

## Required 1Password item

Create item `langfuse` in the Kubernetes vault with fields:

- `SALT`, generate with `openssl rand -base64 32`
- `ENCRYPTION_KEY`, generate with `openssl rand -hex 32`
- `NEXTAUTH_SECRET`, generate with `openssl rand -base64 32`
- `POSTGRESQL_PASSWORD`
- `CLICKHOUSE_PASSWORD`

Redis/Valkey and S3 credentials are no longer stored here. The `langfuse` `ExternalSecret` re-extracts the owner items — `valkey` for `VALKEY_PASSWORD` and `minio` for `MINIO_LANGFUSE_SECRET_KEY` — mirroring how `recyclarr`/`deduparr` re-extract the `radarr`/`sonarr` items. The S3 access key (`langfuse`) is a literal.

The `ExternalSecret` renders those into `langfuse-secret` using the key names expected by the chart and its Bitnami subcharts.

## Storage and backup policy

Thanatos Docker Langfuse, after the old Hermes plugin traffic, is using about 5Gi total durable data:

- PostgreSQL volume: ~46Mi on disk, DB is ~13Mi.
- ClickHouse data: ~3.8Gi, plus ~650Mi ClickHouse logs. Most of this is ClickHouse `system.*` logs, not Langfuse trace tables, because of course the observability database is observing itself harder than the app. The K8s draft sets ClickHouse `logLevel: warning` to cut that chatter down.
- MinIO/S3: ~539Mi, mostly event uploads.
- Redis: ~6Mi memory / <1Mi persisted.

The requested K8s sizes are intentionally tight and based on observed Thanatos usage instead of copying random app sizes from LibreChat/SillyTavern: 1Gi Postgres, 10Gi ClickHouse, 5Gi S3, 1Gi Redis, total 17Gi requested on `ceph-block`.

Only PostgreSQL is VolSync-backed initially. It contains the durable control-plane state: projects, users/orgs, API keys, prompts, datasets/eval metadata. ClickHouse and S3 are trace/event history, and Redis is queue/cache state, so they stay chart-managed unless Langfuse trace history becomes dataset-grade evidence worth backing up. This mirrors the `o11y` pattern: Prometheus and VictoriaLogs keep high-churn observability data in chart/operator-managed PVCs with retention limits, not VolSync backups.

## First-run / cutover notes

- `signUpDisabled` is intentionally `false` for first boot, otherwise we get a gorgeous locked door with no admin account, very chic, totally useless. Disable signup after the initial user/org exists.
- Do not point Hindsight/Hermes OTEL at this until Langfuse is live and projects/API keys are recreated or migrated.
- Expected OTEL endpoint after cutover: `https://langfuse.hades.casa/api/public/otel`.
- Existing Thanatos/OrbStack Langfuse remains untouched by this draft.

## Activation checklist

1. Create the 1Password `langfuse` item with the fields above.
2. Add `./langfuse/ks.yaml` to `kubernetes/apps/default/kustomization.yaml`.
3. Reconcile/apply locally first, then verify:
   - `ExternalSecret/langfuse` Ready
   - `PersistentVolumeClaim/langfuse-postgresql` exists from VolSync restore/create
   - chart-managed claims exist for ClickHouse, S3, and Redis
   - `HelmRelease/langfuse` Ready
   - pods for web, worker, PostgreSQL, ClickHouse, Redis, and MinIO Running
   - `HTTPRoute/langfuse` Accepted/ResolvedRefs
   - `curl -I https://langfuse.hades.casa/api/public/health`
4. Create/login to the admin org.
5. Disable signup and rotate any temporary bootstrap credentials if used.
6. Update Hindsight OTEL headers/endpoint only after Langfuse is confirmed healthy.
