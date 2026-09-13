# Ceph CSI Helm migration

This branch implements **stage one**, not immediate removal of the generated resources.
Read-only inspection found the CSI resources in both the `rook-ceph` Flux Kustomization
inventory and Helm release `ceph-csi-drivers` revision 4. Helm ownership alone does not
protect them from Flux pruning.

The Ceph engine image is explicitly held at the live `quay.io/ceph/ceph:v20.2.1`.
Rook chart 1.20.7 otherwise advances it to 20.2.4. Upgrade the engine separately after
CSI adoption, storage validation and fresh backups; do not combine those transitions.

## Required staged rollout

1. Confirm the live Helm release is deployed and the two Drivers still match
   `app/helmrelease.yaml` (names, `grpcTimeout: 30`, `snapshotPolicy: volumeSnapshot`, and
   controller replicas `1`). Confirm the monitor remains `10.43.136.137:3300` and both
   ClientProfiles retain their existing names and secret references.
2. Reconcile stage one: the old app kustomization now annotates all 23 generated CSI
   objects with `kustomize.toolkit.fluxcd.io/prune: disabled` without removing them.
   The new CSI Kustomization depends on `rook-ceph`, so it adopts the existing named Helm
   release only after that protection has reconciled. Both generated files remain.
3. Verify protection on every old-inventory CSI object, HelmRelease readiness, unchanged
   Driver UIDs/specs, CSI pods, and PVC mount/provision/snapshot operations. Do not infer
   this from Helm ownership or a successful YAML render. Do not squash stages one and two
   into a single first deployment.
4. In a subsequent rollout commit, remove `ceph-csi-drivers.yaml` and its temporary
   annotation patch from the old app kustomization. Keep `ceph-csi-profiles.yaml`: it owns
   Nyx's monitor/profile identities and is not rendered with these chart values. Change
   the cluster Kustomization's `rook-ceph` dependency to `ceph-csi-drivers`. Reconcile the
   new CSI Kustomization first, then the old rook-ceph Kustomization. The live annotations
   prevent deletion as the old inventory forgets these objects. Verify unchanged UIDs
   and working storage before removing the temporary annotations from live resources.

Do not add the upstream CephX key rotation or health-warning suppression as part of this
migration. Nyx already runs Ceph v20.2.1, but neither key-rotation state nor applicability
of those warning suppressions was established by read-only inspection.
