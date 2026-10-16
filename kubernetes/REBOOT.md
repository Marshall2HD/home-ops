# Rebooting Nyx

Nyx is the only node, so every reboot is a full outage of the cluster, Ceph and
everything that uses them (Deadlock's Postgres, Langfuse, whisper/csm, the arrs).
Follow this for any planned reboot: a Talos upgrade, a schematic or kernel argument
change, or a plain reboot.

## Precondition: a remote reset that has been tested

Two graceful reboots have hung. Do not start one unless someone can reset Nyx if it
wedges.

- JetKVM: `http://jetkvm.internal` (10.4.0.40, HTTP only). It gives video and
  keyboard. Power and reset only work with the ATX extension fitted and cabled to
  Nyx's front-panel header. As of 2026-10-14, nobody had confirmed the extension,
  and agents did not have the local password (it is not in 1Password).
- No smart plug feeds Nyx. The Zigbee plugs are for Apollo, Athena, Momus and the
  AC. Home Assistant and Zigbee2MQTT run on Nyx, so they can't help once it wedges.
- The SP5100 TCO watchdog (`/dev/watchdog0`, 5 minute timeout) is armed by Talos
  but has `nowayout=0`. It did not fire during the September hang. Do not count on it.

Before rebooting, open the JetKVM page and confirm that the console shows Nyx and
that ATX power state is reported. If there is no tested reset, the reboot needs a
person at Nyx (or one who can reach it within minutes) for its whole duration.

## Known failure modes

| when                                                                        | what happened                                                                                                                                            | mitigation                                                                                                                                                                                                 |
| --------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Aug 2026, manual `talosctl upgrade`                                         | Talos drained the node and waited forever on single-node PDBs (Ceph OSD/MDS, cert-manager and external-secrets webhooks).                                | Rook `managePodBudgets: false`; webhook PDBs removed. Tuppr already passes `--drain=false` on one node. `talosctl upgrade` defaults to a 5 minute drain timeout, after which it exits _without rebooting_. |
| Aug 2026, `talosctl reboot -m powercycle`                                   | Shutdown stalled stopping containers and removing cgroups; the reboot lock then timed out.                                                               | Don't stack reboot requests. Wait, look at the console, then reset.                                                                                                                                        |
| Sep 2026, `talosctl reboot --mode=powercycle` after a `--no-reboot` upgrade | RBD volumes unmounted cleanly, then Talos went silent after `stopAllServices`. Ping answered; ports 50000 and 6443 refused. A physical reset was needed. | Unknown root cause. Capture the JetKVM screen before resetting next time.                                                                                                                                  |

Kexec is disabled (`sysctl.kernel.kexec_load_disabled=1`), so both reboot modes go
through firmware. Use `powercycle` anyway: it is what Tuppr is configured for, and
it skips any kexec attempt.

## Procedure

All commands are run from the home-ops checkout with its `kubeconfig` and
`talosconfig`. `ceph` means
`kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph`.

### 1. Announce and pause automation

- Tell the Deadlock thread not to deploy, and tell the owner of any live GPU pod
  (for example `default/lark-l4`) that it will be killed and must be restarted by
  them afterwards.
- Pause nyx-autopilot: `sudo launchctl bootout system/com.falke.nyx-autopilot`.
  Check `state/run/*.lock` for a job thread that is still running and wait for it
  or stop it.

### 2. Health gate

All of these must hold. Otherwise fix them first.

```sh
kubectl get nodes                       # nyx Ready, not cordoned
ceph status                             # HEALTH_OK, 2 osds up/in, all PGs active+clean
kubectl get hr -A | awk '$4!="True"'    # no output except known, unrelated failures
flux get ks -A --status-selector ready=false
```

### 3. Backups

Every Kopiur policy must have a successful snapshot from the last two hours.
Trigger one per policy, wait for all to show `Succeeded`, and don't continue if
any fails. ClickHouse (Langfuse) has no Kopiur policy.

```sh
for p in $(kubectl get snapshotpolicies -A -o jsonpath='{range .items[*]}{.metadata.namespace}/{.metadata.name}{" "}{end}'); do
  kubectl create -f - <<EOF
apiVersion: kopiur.home-operations.com/v1alpha1
kind: Snapshot
metadata: {generateName: ${p#*/}-reboot-, namespace: ${p%/*}}
spec: {policyRef: {name: ${p#*/}}}
EOF
done
kubectl get snapshots -A --sort-by=.metadata.creationTimestamp | tail -30
```

Take an etcd snapshot and encrypt it to the recovery recipient. Never leave a
plaintext copy: it contains every cluster secret. Then record PVC identities for
the post-reboot check.

```sh
R=/Volumes/Mimi/Developer/artifacts/home-ops-review/recovery
talosctl -n 10.4.0.10 etcd snapshot /tmp/etcd.db
openssl cms -encrypt -binary -aes256 -outform DER -in /tmp/etcd.db \
  -out "$R/etcd-before-$(date -u +%F).cms" "$R/recipient.crt" && trash /tmp/etcd.db
kubectl get pvc -A -o json | jq '[.items[] | {ns:.metadata.namespace,name:.metadata.name,uid:.metadata.uid,pv:.spec.volumeName,phase:.status.phase}]' > "$R/pvcs-$(date -u +%F).json"
```

### 4. Ceph flags

```sh
ceph osd set noout
ceph osd set norebalance
```

`mon_osd_down_out_subtree_limit: host` also keeps Ceph from marking OSDs out
while the whole host is down. The flags cover the window where the mon is back but
the OSDs are not yet.

### 5. Hold Flux

Stop Flux from applying changes or remediating releases while storage comes back:

```sh
flux suspend ks flux-system -n flux-system
flux suspend ks --all -n system-upgrade   # tuppr, tuppr-upgrades: no surprise upgrade
```

Do not merge to main during the window.

### 6. Reboot

For a Talos version or schematic change, stage the installer first and reboot
separately, so the reboot happens when you choose:

```sh
talosctl -n 10.4.0.10 upgrade -i "$(just talos machine-image nyx)" --no-reboot --timeout=15m
```

Then reboot (or use `just talos reboot-node nyx`). In a second terminal, run
`talosctl -n 10.4.0.10 dmesg -f` and keep the JetKVM console open:

```sh
talosctl -n 10.4.0.10 reboot --mode=powercycle --wait --timeout=20m
```

The `stopAllServices ... completed` line, followed by the API dropping, is
normal: the API goes away at that point. Issue one reboot only. Never send a
second one while the first is in progress.

When the version is bumped in Git afterwards (`talosupgrade.yaml`, `cluster.yaml.j2`),
Tuppr finds the node already at the target and does nothing.

### 7. If it doesn't come back

Start a timer when the API drops.

1. Up to 10 minutes: wait. Talos's own bounded waits (drain, sync, service stops)
   fit within this.
2. Ping answers but 50000 and 6443 still refuse after 10 minutes: Nyx is wedged in
   late shutdown. Screenshot the JetKVM console (kernel messages tell sync, device
   shutdown and firmware hangs apart), then reset: JetKVM ATX `reset`, or
   `power-long` and then `power-short`, or the physical button.
3. No ping and no boot after 10 minutes: use the JetKVM console. If firmware is
   stuck in setup or the boot menu, pick the Talos EFI entry. Report BIOS errors
   rather than resetting repeatedly.
4. The node is up but `SchedulingDisabled`: `kubectl uncordon nyx`.
5. An OSD pod sits in `Init` with PGs `down`: check whether its `activate` container
   is stuck in `lvs` (`ps` shows it in state `D`, with `/dev/rbd*` open). In raw mode,
   Rook's activate script doesn't filter RBD devices from LVM, so `lvs` blocks on an
   RBD image whose PGs need this very OSD. This happened on 2026-10-14. To break it:
   scale `rook-ceph-operator` to 0 (suspend the `rook-ceph` HelmRelease first). Then,
   in the OSD deployment's activate script, insert
   `echo 'devices { filter = ["r|/dev/rbd.*|", "r|/dev/nbd.*|", "r|/dev/drbd.*|"] }' >> /etc/lvm/lvm.conf`
   before `ceph-volume raw activate`, force-delete the stuck pod, wait for
   `active+clean`, and restore the operator (resume, then scale to 1). Never mark the
   OSD lost.
6. The API server doesn't return but Talos does: `talosctl -n 10.4.0.10 service kubelet restart`
   (this recovered a stalled static pod in Sep 2026). Don't reboot again for this.

### 8. Verify

```sh
kubectl get nodes                      # Ready, schedulable
ceph status                            # wait for both OSDs up, all PGs active+clean
ceph osd unset norebalance
ceph osd unset noout
ceph status                            # HEALTH_OK
flux resume ks --all -n system-upgrade
flux resume ks flux-system -n flux-system
flux reconcile ks flux-system -n flux-system --with-source
kubectl get hr -A | awk '$4!="True"'   # wait until empty, except known failures
kubectl get pvc -A --no-headers | awk '$3!="Bound"'   # compare with the saved list
```

Compare PVC names, UIDs and PV bindings with the saved JSON, check that one
scheduled Kopiur snapshot per policy succeeds, and check NFS mounts (Plex, arrs)
and the NVIDIA device plugin.

### 9. Close the window

- Resume nyx-autopilot:
  `sudo launchctl bootstrap system /Library/LaunchDaemons/com.falke.nyx-autopilot.plist`.
- Tell the Deadlock thread the window is over, and tell GPU pod owners to restart
  their pods.

## After the first reboot following a cephx CSI rotation

Existing CSI mounts keep their old `aes` keys until they remount, so on Nyx the
CSI migration to `aes256k` is only finished by a reboot. After one, check
`ceph auth dump-keys --format=json-pretty` (no `aes` keys left), then set
`keepPriorKeyCountMax: 0` and `allowedCiphers: [aes256k]` and drop the
`AUTH_INSECURE_*` health mutes in `rook-ceph/rook-ceph/cluster/helmrelease.yaml`,
following onedr0p's `8571459f12`, `079ea76ac5` and `5c1386680a`.
