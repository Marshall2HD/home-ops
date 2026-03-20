#!/usr/bin/env python3
"""
Upstream Sync Transform Script

Pulls onedr0p/home-ops changes and applies site-specific replacements,
then flags files with structural differences for manual review.

Usage:
    1. Clone/update onedr0p's repo to /tmp/onedr0p-home-ops (or pass --upstream)
    2. Run: python3 scripts/upstream-sync.py
    3. Review flagged files, then commit.
"""

import argparse
import difflib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# ─── Replacement Map ───────────────────────────────────────────────
# Order matters: more specific patterns first to avoid partial matches.
REPLACEMENTS = [
    # ── TLS / Cert resource names ──────────────────────────────────
    # Must come BEFORE the domain replacement to avoid partial matches.
    # e.g. "turbo-ac-tls" → "hades.casa-tls" (not "hades.casa-hades.casa-tls")
    ("turbo-ac-tls", "hades.casa-tls"),   # TLS secret name (pushsecret, externalsecret, envoy refs)
    ("turbo-ac", "hades.casa"),            # cert-manager Certificate resource name

    # ── Domains ────────────────────────────────────────────────────
    ("turbo.ac", "hades.casa"),            # onedr0p's public domain → ours (55 occurrences)
    ("expanse.internal", "hypnos.internal"),  # his NAS/NFS server → ours (18 occurrences, volsync etc.)

    # ── GitHub ─────────────────────────────────────────────────────
    ("onedr0p/home-ops", "Marshall2HD/home-ops"),  # flux source, alerts provider, gatus, actions-runner

    # ── NFS mount paths ────────────────────────────────────────────
    # His TrueNAS pools vs ours. Used in media app volumes and volsync/kopia.
    ("/mnt/ceres/Media", "/mnt/Elysium/Media"),    # media library (bazarr, radarr, sonarr, plex, etc.)
    ("/mnt/eros/Kopia", "/mnt/Asphodel/Kopia"),    # kopia backup repo (volsync components)

    # ── Subnets ────────────────────────────────────────────────────
    # His 192.168.X.Y maps to our 10.X.0.Y for most subnets.
    # Node IPs (192.168.42.10-12) are the same on both — no replacement needed.

    ("192.168.69.", "10.8.0."),     # Cilium LB IP pool (envoy, plex, mosquitto, slskd, etc.)
    ("192.168.10.0/24", "10.5.0.0/24"),  # Plex no-auth trusted network
    ("192.168.70.", "10.7.0."),     # IoT / Home Assistant VLAN (multus iot network, HASS trusted proxy)
    ("192.168.90.", "10.9.0."),     # VPN network (multus vpn network, blackbox-exporter)

    # ── Node IPs ───────────────────────────────────────────────────
    # His nodes are 192.168.42.10-12, ours are 10.4.0.10-12.
    ("192.168.42.10", "10.4.0.10"),  # k8s-0 node
    ("192.168.42.11", "10.4.0.11"),  # k8s-1 node
    ("192.168.42.12", "10.4.0.12"),  # k8s-2 node

    # ── Individual LAN devices ─────────────────────────────────────
    # His UniFi gateway + devices on the main LAN.
    ("192.168.1.1", "10.1.0.1"),    # UniFi gateway (unpoller, go2rtc RTSP cameras, cilium BGP peer)
    ("192.168.1.90", "10.1.0.90"),  # Zigbee coordinator (zigbee2mqtt serial-over-TCP)
    ("192.168.1.80", "10.1.0.80"),  # SNMP target (currently commented out in ours)
    ("192.168.1.82", "10.1.0.82"),  # SNMP target (currently commented out in ours)

    # ── Ports ──────────────────────────────────────────────────────
    # These are forwarded ports that differ between our setups.
    # His torrent/soulseek ports vs ours.
    ("&torrentPort 31288", "&torrentPort 50470"),     # qbittorrent forwarded port
    ("&soulseekPort 50429", "&soulseekPort 50439"),   # slskd soulseek listen port

    # ── GPU dependency ─────────────────────────────────────────────
    # He uses Intel iGPU (DRA), we use NVIDIA (device plugin).
    ("name: intel-gpu-resource-driver", "name: nvidia-device-plugin"),  # plex ks.yaml dependsOn

    # ── Misc ───────────────────────────────────────────────────────
    ("avatars.githubusercontent.com/u/213795",      # onedr0p's GitHub avatar (gatus status page)
     "avatars.githubusercontent.com/u/15093189"),    # ours
]

# ─── Files to SKIP (your version always wins) ─────────────────────
# These have structural differences that can't be handled by string replacement.
SKIP_FILES = [
    # These files have STRUCTURAL differences — not just string swaps.
    # The script will leave your version untouched and print them as needing review.

    # Ceph: we run single-node (size:2, failureDomain:osd, specific disk serial filters,
    # 10.4.0.0/24 ceph public network, osdsPerDevice:1). He runs 3-node (size:3, host failure domain).
    "apps/rook-ceph/rook-ceph/cluster/helmrelease.yaml",

    # Plex: we use NVIDIA GPU (runtimeClassName:nvidia, NVIDIA_VISIBLE_DEVICES env vars).
    # He uses Intel iGPU via DRA (resourceClaims, deviceClassName:gpu.intel.com).
    "apps/default/plex/app/helmrelease.yaml",

    # Plex GPU claim: his is active (Intel DRA ResourceClaimTemplate), ours is fully commented out.
    "apps/default/plex/app/resourceclaimtemplate.yaml",

    # SNMP: his apcups + dell modules are active, ours are commented out (no UPS/iDRAC SNMP yet).
    "apps/observability/snmp-exporter/app/helmrelease.yaml",

    # Scrape targets: he has 3 JetKVMs (jetkvm-0/1/2.internal), we have 1 (jetkvm.internal).
    "apps/observability/kube-prometheus-stack/app/scrapeconfig.yaml",

    # Cilium README: has BGP config with our actual node IPs and gateway.
    # The string replacements handle this now, but keeping it in skip list
    # since it's documentation — manual changes there shouldn't be blindly overwritten.
    "apps/kube-system/cilium/README.md",

    # Kustomization: our custom app list (chaptarr, cobalt, librechat, minecraft, etc.)
    # gets blown away if overwritten. The script can't merge resource lists.
    "apps/default/kustomization.yaml",

    # Resource limits: we run leaner than onedr0p on these.
    # qbittorrent: we use 1Gi request / 8Gi limit, he uses no request / 32Gi limit.
    "apps/default/qbittorrent/app/helmrelease.yaml",
    # sabnzbd: we have 2Gi memory request, he doesn't set one.
    "apps/default/sabnzbd/app/helmrelease.yaml",
]

# ─── Apps only in YOUR repo (never overwrite with "not found") ────
# These dirs exist in yours but not upstream. The script ignores them entirely
# (won't flag them as "upstream removed" in the report).
YOUR_APPS = [
    # Custom apps we run that onedr0p doesn't
    "apps/default/chaptarr",       # manga chapter tracking
    "apps/default/cobalt",         # media downloader
    "apps/default/decluttarr",     # arr cleanup automation
    "apps/default/flaresolverr",   # cloudflare challenge solver for prowlarr
    "apps/default/librechat",      # LLM chat frontend
    "apps/default/minecraft",      # paper server + mc-router
    "apps/default/open-webui",     # LLM web UI
    "apps/default/sillytavern",    # RP/chat frontend
    "apps/default/speaches",       # ASR/STT service
    "apps/default/vllm",           # LLM inference server

    # GPU stack: we use NVIDIA, he uses Intel iGPU (via intel-gpu-resource-driver)
    "apps/kube-system/node-feature-discovery",  # NFD for NVIDIA GPU detection
    "apps/kube-system/nvidia-device-plugin",    # NVIDIA device plugin + RuntimeClass

    # Volsync extra
    "apps/volsync-system/volsync/maintenance/mutatingadmissionpolicy.yaml",
]

# ─── Apps only in UPSTREAM (he added, you don't have yet) ─────────
# These get copied in with replacements applied. Review after.
# (Currently empty — you're a superset. Will be populated at runtime.)


def apply_replacements(text: str) -> str:
    """Apply all string replacements to file content."""
    for old, new in REPLACEMENTS:
        text = text.replace(old, new)
    return text


def is_skipped(rel_path: str) -> bool:
    """Check if a file is in the skip list."""
    return any(rel_path.endswith(s) for s in SKIP_FILES)


def is_your_app(rel_path: str) -> bool:
    """Check if file belongs to a your-only app directory."""
    return any(rel_path.startswith(a) for a in YOUR_APPS)


def sync(upstream_dir: Path, local_dir: Path, dry_run: bool = False):
    upstream_k8s = upstream_dir / "kubernetes"
    local_k8s = local_dir / "kubernetes"

    if not upstream_k8s.exists():
        print(f"ERROR: {upstream_k8s} not found. Clone onedr0p/home-ops first.")
        sys.exit(1)

    skipped = []       # files where we cherry-pick (review structural diffs)
    transformed = []   # files auto-replaced
    new_files = []     # new from upstream
    unchanged = []
    cherrypick = {}    # rel_path -> unified diff string (for review files)

    for root, dirs, files in os.walk(upstream_k8s):
        for fname in files:
            if not fname.endswith((".yaml", ".yml", ".md")):
                continue

            upstream_file = Path(root) / fname
            rel_path = str(upstream_file.relative_to(upstream_k8s))
            local_file = local_k8s / rel_path

            # Read upstream content and apply replacements
            content = upstream_file.read_text()
            transformed_content = apply_replacements(content)

            if is_skipped(rel_path):
                # Cherry-pick mode: show what upstream changed vs our version
                # after applying replacements, so only structural diffs remain.
                if local_file.exists():
                    local_content = local_file.read_text()
                    if local_content != transformed_content:
                        # Generate a unified diff for review
                        diff_lines = list(difflib.unified_diff(
                            local_content.splitlines(keepends=True),
                            transformed_content.splitlines(keepends=True),
                            fromfile=f"ours: {rel_path}",
                            tofile=f"upstream (transformed): {rel_path}",
                            n=3,
                        ))
                        if diff_lines:
                            cherrypick[rel_path] = "".join(diff_lines)
                    else:
                        unchanged.append(rel_path)
                skipped.append(rel_path)
                continue

            if local_file.exists():
                local_content = local_file.read_text()
                if local_content == transformed_content:
                    unchanged.append(rel_path)
                    continue

                transformed.append(rel_path)
                if not dry_run:
                    local_file.write_text(transformed_content)
            else:
                # New file from upstream
                new_files.append(rel_path)
                if not dry_run:
                    local_file.parent.mkdir(parents=True, exist_ok=True)
                    local_file.write_text(transformed_content)

    # Check for files upstream removed (exist locally but not upstream,
    # excluding your custom apps)
    removed = []
    for root, dirs, files in os.walk(local_k8s):
        for fname in files:
            if not fname.endswith((".yaml", ".yml", ".md")):
                continue
            local_file = Path(root) / fname
            rel_path = str(local_file.relative_to(local_k8s))

            if is_your_app(rel_path):
                continue

            upstream_file = upstream_k8s / rel_path
            if not upstream_file.exists():
                removed.append(rel_path)

    # ─── Report ───────────────────────────────────────────────
    print(f"\n{'DRY RUN — ' if dry_run else ''}Upstream Sync Report")
    print("=" * 60)

    if transformed:
        print(f"\n  TRANSFORMED ({len(transformed)} files):")
        for f in sorted(transformed):
            print(f"    ✓ {f}")

    if new_files:
        print(f"\n  NEW FROM UPSTREAM ({len(new_files)} files):")
        for f in sorted(new_files):
            print(f"    + {f}")

    if cherrypick:
        print(f"\n  ⚠ CHERRY-PICK — structural diffs after replacement ({len(cherrypick)} files):")
        for f in sorted(cherrypick):
            print(f"\n    ── {f} ──")
            for line in cherrypick[f].splitlines():
                print(f"    {line}")

    skipped_clean = [f for f in skipped if f not in cherrypick]
    if skipped_clean:
        print(f"\n  ✓ REVIEW FILES (no upstream changes): {len(skipped_clean)}")
        for f in sorted(skipped_clean):
            print(f"    = {f}")

    if removed:
        print(f"\n  ⚠ UPSTREAM REMOVED — still in your repo ({len(removed)} files):")
        for f in sorted(removed):
            print(f"    - {f}")

    print(f"\n  Unchanged: {len(unchanged)} files")
    print(f"  Total processed: {len(transformed) + len(new_files) + len(unchanged) + len(skipped)}")

    if dry_run:
        print("\n  (No files were modified. Run without --dry-run to apply.)")

    # Show git diff summary if not dry run
    if not dry_run and (transformed or new_files):
        print("\n  Run 'git diff --stat' in home-ops to see what changed.")


def clone_upstream(target: Path):
    """Clone or update onedr0p/home-ops."""
    if target.exists():
        print("Updating upstream repo...")
        subprocess.run(["git", "-C", str(target), "pull", "--ff-only"],
                       check=True, capture_output=True)
    else:
        print("Cloning onedr0p/home-ops...")
        subprocess.run(["git", "clone", "--depth", "1",
                        "https://github.com/onedr0p/home-ops.git", str(target)],
                       check=True, capture_output=True)


def main():
    parser = argparse.ArgumentParser(description="Sync upstream onedr0p/home-ops with local transforms")
    parser.add_argument("--upstream", type=Path, default=Path("/tmp/onedr0p-home-ops"),
                        help="Path to onedr0p/home-ops clone (default: /tmp/onedr0p-home-ops)")
    parser.add_argument("--local", type=Path,
                        default=Path(__file__).resolve().parent.parent,
                        help="Path to your home-ops repo (default: parent of scripts/)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would change without modifying files")
    parser.add_argument("--fetch", action="store_true",
                        help="Clone or pull upstream before syncing")
    args = parser.parse_args()

    if args.fetch:
        clone_upstream(args.upstream)

    sync(args.upstream, args.local, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
