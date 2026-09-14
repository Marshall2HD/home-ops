# Nyx upstream adoption

Prepared against [onedr0p's reviewed revision](https://github.com/onedr0p/home-ops/commit/912b5f8709db724eefb8c7431c5d9f710549a534).
This is a portable-change adoption, not a cluster mirror.

## Live checkpoint: 2026-09-14

PR #809 is merged. CSI adoption completed with all 23 object UIDs and all 70 original
PVC bindings preserved. Block and CephFS snapshot/restore tests passed. Both DNS
controllers use GA annotations; all 99 UniFi records were unchanged by that switch.
All Helm releases were Ready before node maintenance.

Talos 1.14.0 with Kata installed successfully using `--no-reboot`. The subsequent
graceful reboot stalled after volume unmount phases and entry into service shutdown.
The user force-restarted Nyx. Both APIs recovered, the node is Ready on Talos 1.14.0,
and the Kata, AMD, NVIDIA and DRBD extensions are loaded. Ceph returned to HEALTH_OK
and all 70 original PVC identities and bindings were checked again after restart.
Minecraft is running again. MinIO's old Docker Hub image became inaccessible; Quay
serves the exact same pinned digest, so only its registry address was changed.

Kubernetes is now 1.37.0 and the full Talos configuration is applied. The Kubernetes
upgrade required one kubelet service restart to recover a stalled API-server static
pod, not another host reboot. Cilium migrated to veth only after graceful application
shutdown, volume unmounts, ordered Ceph shutdown and zero remaining pod endpoints.
DNS, BGP, Ceph, NFS, NVIDIA Plex and all original controller replica counts recovered.
All 70 original PVC identities and bindings were verified again. Backup/CronJob
suspensions and autoscaler behavior were restored.

Kata passed live admission, separate guest-kernel/VMM, token/device-absence and network
isolation tests. The readiness label is persisted in the Nyx Talos template. Cilium
and tuppr-upgrades holds are removed; Tuppr targets match the installed versions.
No existing application or credentialed CI runner was moved to Kata.

ClickHouse's retained 1,190-column `system.metric_log` caused repeated memory-heavy
merge failures. Bounded merge blocks/concurrency and an 8Gi memory limit compacted
79 parts into six without changing the 560,520 rows. No active merges or new errors
remained at the final check, and Langfuse ingestion resumed. Its image/schema/PVC
were not changed. Ceph 20.2.2 fixed raw-device discovery; foreign-disk wiping remains
disabled. The storage release explicitly opts out of global Helm rollback defaults
so a failed future engine upgrade retries rather than automatically downgrading.

Two stale restore attachment/PV records were removed only after confirming their
backing images were absent. Three retained database rollback images remain intact.
There are also 31 historical finalizing VolumeAttachments whose PVs no longer exist;
these were left alone and distinguished from real mounts during the cutover.

Recovery evidence and three verified encrypted etcd snapshots are outside Git at
`/Volumes/Mimi/Artifacts/home-ops-review/recovery/`. Consult `host-maintenance.json`
there before further maintenance. Keep future runtime/datapath changes separate from
unrelated engine upgrades.

ExternalDNS v0.22.0 does not enforce `--dry-run` for its webhook provider. Do not use
that flag to preview UniFi writes. The initial probe submitted a plan; normal DNS
reconciliation restored the records, which were checked directly before switching.

## Preserved local configuration

- Marshall2HD/home-ops, hades.casa, ocharted.hades.casa, local VLANs, BGP peer,
  load-balancer addresses, and Hypnos backup repository.
- Nyx's AMD/NVIDIA schematic, install disk serial, exact two Ceph device IDs,
  single-node monitor/controller counts, OSD-level replication, maxPods and hugepages.
- NVIDIA Plex initialization, existing applications and persistent data, and local
  dependency guards for certificates, registry storage, snapshots and backups.
- No go2rtc, Z-Wave, Spegel, or Intel GPU stack.
- Langfuse remains on chart 1.5.41. Its separate 2.0 PR requires a ClickHouse data
  migration and is not part of upstream parity.

## Kata changes the Cilium decision

Kata was subsequently requested for untrusted workloads. Nyx exposes AMD-V (`svm`),
`/dev/kvm`, `/dev/vhost-net` and `/dev/vhost-vsock`. The schematic now includes the
official Kata extension while retaining all AMD/NVIDIA extensions and kernel arguments.
The `kata` RuntimeClass uses Cloud Hypervisor, not QEMU's nested-virtualization runtime.

Cilium now targets veth because released Kata does not support netkit. Preserve BPF
host routing, BBR, native routing, BIG TCP and the single operator replica. Existing
netkit pods must be recreated in a coordinated maintenance window, not silently left
on a mixed datapath. Follow the [Kata rollout and isolation checks](apps/sandbox/README.md)
before admitting workloads. This is not a default runtime change for infrastructure,
GPU applications, or the credentialed CI runner.

## Roll out in stages

1. Before merging into the Flux-watched branch, confirm recent successful Kopiur
   backups, Ceph HEALTH_OK, and console access for Nyx. The new Tuppr targets are
   Talos 1.14.0 and Kubernetes 1.37.0; a merge can trigger upgrades, including a
   single-node outage. Do not treat publishing a review branch as rollout approval.
2. Apply stage one, retaining both generated CSI resources and their prune protection.
   Follow the [CSI migration procedure](apps/rook-ceph/ceph-csi-drivers/MIGRATION.md).
   Confirm Helm adoption, unchanged Driver identities and working PVC mounts,
   provisioning and snapshots before preparing its separate stage-two commit.
3. DNS stage one deliberately retains the alpha annotation prefix in both
   ExternalDNS releases and writes both alpha and GA annotations. Verify both key
   sets on the live Cilium API Service, Envoy Gateways and generated Services, SMTP
   Service, and redirect route. Then remove the two `--annotation-prefix` overrides
   in a separate rollout. Verify unchanged Cloudflare and UniFi records before
   removing alpha keys later. Both controllers use sync policy, so ordering matters.
4. Talos template changes are not applied by Flux. Separately render and dry-run the
   Nyx configuration, then apply it in a maintenance window. Preserve the existing
   AMD/NVIDIA extensions and disk selection while adding Kata. A version-only Tuppr
   upgrade does not install the new schematic. Follow the Kata procedure for the
   Image Factory upgrade and veth migration. Verify networking, NVIDIA workloads and
   NFS mounts after reboot. Do not run a manual upgrade concurrently with Tuppr.
5. Verify Cleanrr health and its Radarr/Sonarr connections. Download-client removal
   remains disabled. Check Kopiur maintenance and cache usage, Arr authentication,
   Home Assistant MCP authentication, monitoring, DNS and tunnel readiness.

## Konflate activation

Konflate targets this repository and uses the existing GitHub bot App
and read-only mounted ocharted credentials. Required 1Password
field/attachment names were checked without persisting secret values. The webhook
secret is derived by External Secrets from the existing Flux token with a separate
`konflate:` domain prefix; SHA-256 is the available Sprig template function.

The chart polls every 30 minutes, so a GitHub webhook is not required for initial
operation. After deployment, verify ExternalSecret synchronization, App repository
access, private-chart rendering, checks/comments, and endpoint readiness. Configure
an optional GitHub webhook only after confirming the deployed Konflate endpoint and
event requirements, using the derived secret without logging it. No webhook or App
permission changes were made during preparation.

## Verification limits

Offline flate tests passed for all 237 Flux/chart sources at Kubernetes 1.37.0, and
the full build rendered 1,333 objects. The only duplicate identities are the 23
intentional stage-one CSI objects, whose behavior was compared. The Cloudflare tunnel
ID secret is unavailable to offline rendering and produces the expected substitution
warning. Rendering is not proof of live upgrade, storage, DNS, or webhook behavior;
those checks remain mandatory during rollout.

The Kata follow-up passed live admission controls, including rejecting omitted runtime
and token settings, explicit token projection, Kata/Multus overrides, privileged mode,
host networking, hostPath and extra capabilities. The guest kernel was `6.18.35`, versus
the host's `6.18.48-talos`, with a separate Cloud Hypervisor process. Internet, LAN,
cluster/node API, DNS and peer ingress were denied. A temporary peer-only HTTP rule
allowed both directions; deleting it restored denial, proving that a broken guest
network was not being mistaken for isolation. No API token or tested host devices
were exposed. These checks verify the configured boundary, not absence of all escape
vulnerabilities.
