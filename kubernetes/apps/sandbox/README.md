# Kata workload isolation on Nyx

This namespace is for untrusted code, not host infrastructure. Nothing has been
deployed during preparation. No existing workload has been moved into it.

## Boundary

- Pods must explicitly select `runtimeClassName: kata` and
  `automountServiceAccountToken: false`. Admission denies other runtimes, privileged
  service accounts, explicit service-account-token projections, Kata override
  annotations, and Multus additional-network annotations.
- Pod Security Admission enforces the restricted profile: no privileged containers,
  host namespaces, hostPath volumes, or unrestricted capabilities.
- All ingress and egress are denied by default, including DNS. Add a narrowly scoped
  policy only when a workload needs connectivity. Do not attach trusted LAN networks.
- No PVCs, NodePort or LoadBalancer Services. Namespace quota caps pod count at four,
  CPU at four cores, memory at 8Gi and requested/limited ephemeral storage at 8Gi.
  Limits include Kubernetes-accounted runtime overhead; these are scheduling and
  resource controls, not a guarantee against all denial-of-service attacks.
- RuntimeClass scheduling requires `runtime.hades.casa/kata-ready=true`. The label is
  intentionally absent until the node is prepared. Missing runtime support fails
  closed rather than falling back to runc. Do not grant untrusted actors permission
  to modify namespace policies, RuntimeClasses, node labels or RBAC.

The existing `home-ops-runner` is **not an untrusted sandbox**: its service account
has Kubernetes `cluster-admin`, and its mounted Talos certificate has `os:admin`.
Kata cannot contain API permissions or protect credentials given to a workload.
Its image-pull workflow currently needs Talos access. Keep untrusted jobs out of that
runner; separating its trusted image-pull duties from untrusted execution is a separate
runner migration, not something a runtimeClass switch solves.

## Maintenance rollout

1. Confirm fresh backups, Ceph HEALTH_OK and out-of-band console access. Nyx is the
   only node, so reboot and pod recreation cause an outage. Coordinate with Tuppr;
   do not allow manual and automated upgrades to race.
2. Reconcile the veth Cilium configuration and verify the Cilium DaemonSet rollout.
   Do not admit sandbox workloads yet. Prepare the Talos image from the updated
   schematic, preserving all existing AMD/NVIDIA extensions, kernel arguments and
   exact disk selection. A version-only upgrade using the old schematic omits Kata.
3. Upgrade Nyx using that new Image Factory image. Recreate old netkit pods as required
   by Cilium's datapath migration procedure; a reboot alone must not be assumed to
   migrate every sandbox. Verify veth endpoints, DNS, service routing, BGP, Ceph CSI,
   NFS and NVIDIA workloads before proceeding. Never delete all infrastructure pods
   indiscriminately on a single-node control plane.
4. Verify `talosctl get extensions`, `/dev/kvm`, and the extension's containerd
   fragments `/etc/cri/conf.d/10-kata-containers.part` and `11-kata-qemu.part`.
   The extension registers the runtimes; do not add a duplicate containerd patch.
5. Confirm `sandbox-kata` admission policy has no type-check warnings and its binding
   is installed, alongside restricted namespace labels, quota and default-deny policy.
   Then label Nyx `runtime.hades.casa/kata-ready=true`. Persist that label in
   `talos/nodes/controlplane/nyx.yaml.j2` only after the runtime smoke test succeeds.
6. Run the example below in `sandbox`. Compare its `uname -r` with the Talos host's
   kernel and confirm a separate VMM process on the host; RuntimeClass metadata alone
   is not proof of VM isolation. Verify external/LAN/cluster connections are denied,
   the Kubernetes API token is absent, and no host devices or host files are visible.
7. Negative controls must be rejected: omit/change runtimeClassName, enable token
   automount, project a token explicitly, add a Multus annotation, request privileged
   mode, hostNetwork or a hostPath. Also verify a second ordinary pod cannot reach
   the sandbox. Do not call the namespace validated until these live checks pass.

For rollback, stop sandbox jobs and remove the readiness label first. Restore the
previous schematic only after no Kata pods remain. Reversing veth back to netkit is
another coordinated datapath migration, not a harmless Helm rollback.

## First test pod

This is a manual example, intentionally not reconciled by Flux. The image is pulled
by the host; it does not require allowing network access from inside the sandbox.

```yaml
apiVersion: v1
kind: Pod
metadata:
    name: kata-smoke
    namespace: sandbox
spec:
    runtimeClassName: kata
    automountServiceAccountToken: false
    restartPolicy: Never
    securityContext:
        runAsNonRoot: true
        runAsUser: 65532
        seccompProfile:
            type: RuntimeDefault
    containers:
        - name: check
          image: mirror.gcr.io/library/busybox:1.37.0
          command: [sh, -c]
          args:
              - uname -r; test ! -e /var/run/secrets/kubernetes.io/serviceaccount/token && sleep 600
          securityContext:
              allowPrivilegeEscalation: false
              capabilities:
                  drop: [ALL]
```

Authoritative configuration: [Siderolabs Kata extension](https://github.com/siderolabs/extensions/tree/main/container-runtime/kata-containers).
The `kata` handler uses Cloud Hypervisor with published overhead of 130Mi and 250m;
the guest configuration has a 2Gi default memory size. QEMU is not exposed through a
RuntimeClass here because nested virtualization is not required.
Networking limitation: [Kata netkit issue](https://github.com/kata-containers/kata-containers/issues/12159).
