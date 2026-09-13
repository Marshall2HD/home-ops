# Nyx upstream adoption

Prepared against [onedr0p's reviewed revision](https://github.com/onedr0p/home-ops/commit/912b5f8709db724eefb8c7431c5d9f710549a534).
This is a portable-change adoption, not a cluster mirror. Nothing in this branch has
been deployed as part of preparation.

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

The Kata follow-up separately passed flate checks for the sandbox Kustomization and
Cilium HelmRelease, server-side dry-run schema validation for the RuntimeClass and
isolation resources, and 13 positive/negative controls against the admission CEL
expressions. No live admission policy, VM, or network-isolation test has been run.
