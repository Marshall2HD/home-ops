# Bootstrap

Everything needed to take freshly installed Talos nodes to a cluster that Flux
manages on its own. The entire process is driven by a single command:

```sh
just bootstrap cluster
```

Once it completes, Flux reconciles the rest of the repository and this
directory is not used again until the next rebuild.

## Prerequisites

- Tools pinned in `.mise/config.toml` installed via `mise install` (talosctl,
  just, minijinja-cli, op, yq, jq), plus kubectl, helmfile, kustomize and gum
  on the PATH (not pinned here).
- A signed-in 1Password CLI (`op`) with access to the personal account.
  Machine secrets never live in this repo; every `op://` reference in the
  Talos configs and bootstrap manifests is resolved at apply time with
  `op inject`.
- A valid `talosconfig` at the repo root (mise points `TALOSCONFIG` there).
  The justfile derives the controller endpoint and node list from
  `talosctl config info`, so nothing is hardcoded here.
- The UDM configuration below. `k8s.internal` points at the Cilium
  LoadBalancer VIP, which exists only once Cilium is installed, so bootstrap
  talks to the controller's node IP directly until the `apps` stage brings
  Cilium up.

## UDM configuration

The Kubernetes API is fronted by a Cilium LoadBalancer Service (`kube-api`,
`10.8.0.120`, `externalTrafficPolicy: Local` so only the node with a healthy
apiserver attracts traffic). Cilium announces it to the UDM over BGP along
with every other LoadBalancer IP. See
[networking.yaml](../kubernetes/apps/kube-system/cilium/app/networking.yaml).

```mermaid
graph LR
    client[LAN client] --> udm["UDM (ASN 64513)"]
    udm --> nyx["nyx (10.4.0.10)"]
    nyx -. "BGP (ASN 64514): VIPs from 10.8.0.0/24" .-> udm
```

The VIPs the UDM learns this way:

| VIP          | Hostname              | Backs                          |
| ------------ | --------------------- | ------------------------------ |
| `10.8.0.120` | `k8s.internal`        | `kube-api` Service (apiserver) |
| `10.8.0.121` | `internal.hades.casa` | `envoy-internal` Gateway       |
| `10.8.0.126` | `external.hades.casa` | `envoy-external` Gateway       |

A static A record in UniFi (under the policy settings; the UI location
varies by Network release) points the API hostname at the VIP:

```text
k8s.internal → 10.8.0.120
```

Cilium (ASN 64514) peers from Nyx on the SERVERS subnet (`10.4.0.10`) and
announces LoadBalancer Service IPs from the `10.8.0.0/24` pool. UniFi accepts
a single FRR config upload per device
(Settings → Routing → BGP):

<details>
<summary>FRR config</summary>

```text
router bgp 64513
  bgp router-id 10.1.0.1
  no bgp ebgp-requires-policy

  neighbor k8s peer-group
  neighbor k8s remote-as 64514

  neighbor 10.4.0.10 peer-group k8s

  address-family ipv4 unicast
    neighbor k8s next-hop-self
    neighbor k8s soft-reconfiguration inbound
  exit-address-family
exit
```

</details>

> [!WARNING]
> Re-uploading the FRR config briefly bounces established BGP sessions.

To verify, run `vtysh -c "show bgp summary"` on the UDM and confirm that
`10.4.0.10` is established. `vtysh -c "show ip route 10.8.0.120"` should show
Nyx as the next hop, and `curl -k https://k8s.internal:6443/livez` should
succeed.

> [!NOTE]
> `k8s.internal` rides the Cilium `kube-api` LoadBalancer, so the named API
> endpoint depends on Cilium being healthy. If the CNI is ever down, reach
> the API directly at `https://10.4.0.10:6443` and the Talos API at
> `10.4.0.10`; neither depends on the CNI.

## UDM boot scripts

The UDM root filesystem is an overlay: writes to `/etc` survive reboots but
are wiped by firmware upgrades, and `/run` is tmpfs. `/data` is a real
partition that survives both, so anything custom lives there as a boot
script, run by the `udm-boot` service from
[unifi-utilities/unifi-common](https://github.com/unifi-utilities/unifi-common)
(UniFi OS 4.x+):

```sh
curl -fsL "https://raw.githubusercontent.com/unifi-utilities/unifi-common/HEAD/remote_install.sh" | /bin/bash
```

> [!WARNING]
> The service unit itself sits on the overlay, so a firmware upgrade can
> remove it :hurtrealbad: while the scripts in `/data/on_boot.d` remain.
> After an
> upgrade, check `systemctl is-enabled udm-boot` and rerun the installer if
> needed.

## HTTP/3 discovery

Envoy Gateway serves HTTP/3 (`http3: {}` in the `ClientTrafficPolicy`, UDP
443 on both LoadBalancer Services), but browsers only discover it after a
first TCP visit via `Alt-Svc` unless DNS advertises it. dnsmasq on the UDM
can publish HTTPS (type 65) records for the gateway hostnames; the hex
payload decodes to priority 1, target `.`, `alpn="h3,h2"`. Lookups follow
CNAMEs, so app hostnames the UDM resolves to the gateways itself need no
records of their own.

> [!IMPORTANT]
> Externally published apps (`plex`, anything else behind the Cloudflare
> tunnel) are CNAMEs to `external.hades.casa` in public DNS, and the UDM has
> no HTTPS record for those names. The browser's HTTPS query is forwarded
> upstream, where Cloudflare answers with its own HTTPS record, and
> browsers then use that record and connect through Cloudflare, even
> though the A/AAAA answer is the internal gateway IP. LAN traffic to
> those apps rides the tunnel instead of the local path. :suspect:

The main dnsmasq instance loads `--conf-dir=/run/dnsmasq.dhcp.conf.d/`,
which is tmpfs and regenerated by `ubios-udapi-server`, hence another boot
script.

<details>
<summary><code>/data/on_boot.d/40-dnsmasq-https-rr.sh</code></summary>

```sh
#!/bin/sh
CONF_DIR=/run/dnsmasq.dhcp.conf.d
for i in $(seq 1 30); do [ -d "$CONF_DIR" ] && break; sleep 2; done
[ -d "$CONF_DIR" ] || exit 0
cat > "$CONF_DIR/custom.conf" <<RR
dns-rr=external.hades.casa,65,00010000010006026833026832
dns-rr=internal.hades.casa,65,00010000010006026833026832
RR
[ -f /run/dnsmasq-main.pid ] && kill "$(cat /run/dnsmasq-main.pid)" 2>/dev/null
exit 0
```

</details>

Killing the main dnsmasq is safe :goberserk:; `ubios-udapi-server` respawns
it with the new config.

> [!NOTE]
> A provisioning event in the Network app can regenerate the conf dir and
> drop `custom.conf` until the next reboot; rerunning the script puts it
> back.

To verify:

```sh
dig +short @10.1.0.1 internal.hades.casa HTTPS   # expect: 1 . alpn="h3,h2"
curl --http3-only -sk -o /dev/null -w '%{http_version}\n' https://internal.hades.casa/
```

## Stages

`just bootstrap cluster` runs these stages in order (see [mod.just](mod.just)):

```mermaid
graph LR
    nodes --> k8s --> kubeconfig --> base --> apps
```

1. **nodes** - Renders each node's Talos config (`talos/*.j2` templates plus
   1Password injection) and applies it with `talosctl apply-config --insecure`.
   Nodes that are already configured are skipped, so the stage is idempotent.
2. **k8s** - Runs `talosctl bootstrap` against the controller, retrying until
   etcd reports the cluster already exists.
3. **kubeconfig** - Fetches the kubeconfig with `talosctl kubeconfig`, then
   rewrites the server address to the controller's node IP: the generated
   `https://k8s.internal:6443` points at the Cilium VIP, which does not
   exist yet. The final stage re-fetches the kubeconfig so the endpoint
   returns to `k8s.internal` once Cilium is serving it.
4. **base** - Waits for every control plane apiserver to answer `/readyz`
   and for nodes to register (they stay `Ready=False` until the CNI is
   installed), then applies:
    - `kustomize/` - bootstrap Secrets rendered through `op inject`, plus
      their namespaces: 1Password Connect credentials and token plus the
      Cloudflare tunnel ID from the personal account (`personal/`). These
      exist before their controllers so nothing deadlocks on a missing
      Secret.
    - `helmfile/crds.yaml` - CRDs extracted from upstream charts
      (envoy-gateway, grafana-operator, kube-prometheus-stack) and applied
      directly. Installing CRDs out-of-band means Flux Kustomizations that
      consume CRD-backed resources don't need `dependsOn` chains.
5. **apps** - `helmfile sync` of `helmfile/apps.yaml`, the minimal release
   chain Flux needs before it can take over:

    cilium → coredns → cert-manager → external-secrets → onepassword-connect →
    flux-operator → flux-instance

    Once `flux-instance` is healthy, Flux reconciles `kubernetes/` and manages
    these same releases from then on.

> [!TIP]
> Every stage is safe to re-run. If bootstrap fails partway, fix the issue
> and run `just bootstrap cluster` again.

## Data restore (Kopiur)

Bootstrap itself restores no application data; that happens declaratively
once Flux takes over, via [Kopiur](https://github.com/home-operations/kopiur)
(deployed from [kubernetes/apps/kopiur-system/](../kubernetes/apps/kopiur-system/),
backed by the `hypnos`
ClusterRepository: kopia in S3 on `hypnos.internal`).

Apps that opt into the `kopiur/backup` component get a PVC whose
`spec.dataSourceRef` points at a Kopiur `Restore` with `target.populator: {}`
(see [kubernetes/components/kopiur/backup/](../kubernetes/components/kopiur/backup/)).
That makes the `Restore` a
passive volume-populator source: when Flux applies the app on a fresh
cluster, the PVC is provisioned by restoring the latest snapshot for the
app's SnapshotPolicy from the repository. The PVC stays unbound while the
restore mover Job runs, so the app's pod simply stays `Pending` until the
data is back; no ordering logic needed anywhere.

Because the `Restore`s use `onMissingSnapshot: Continue`, an app with no
snapshot yet (a brand-new app, or a deliberately fresh start) comes up with
an empty volume instead of failing; the same manifests handle first deploy
and disaster recovery ("deploy-or-restore").

Each `Restore` pins the snapshot it resolved on first reconciliation and
never silently retargets, even if a schedule fires mid-restore. Expect pods
to sit `Pending` for as long as their volume takes to restore.

## Single source of truth

The helmfiles define no chart versions or values of their own. Each release's
chart and version are read from the app's `ocirepository.yaml` and its values
from the app's `helmrelease.yaml` under `kubernetes/apps/` (see
[helmfile/templates/](helmfile/templates/)). Bootstrap therefore installs
exactly what Flux will
later reconcile, and Renovate updates only one place.
