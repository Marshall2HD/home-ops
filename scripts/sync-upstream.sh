#!/usr/bin/env bash
# sync-upstream.sh — Pull onedr0p/home-ops changes, transform env-specific values,
# and present diffs for review before committing.
#
# Usage: ./scripts/sync-upstream.sh [--dry-run]
#
# What it does:
#   1. Clones/updates onedr0p/home-ops to a temp dir
#   2. For shared apps, copies his files over yours
#   3. Applies string replacements (domain, NAS, IPs, paths)
#   4. Skips hardware-specific files (talos config)
#   5. Skips apps that only exist in YOUR repo
#   6. Stages everything so you can `git diff --cached` to review
#
# After running, review with: git diff --cached
# Then: git commit -m "sync: upstream onedr0p/home-ops"
# Or:   git checkout . && git reset HEAD  (to abort)

set -euo pipefail

DRY_RUN=false
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

# ──────────────────────────────────────────────
# Config: value mappings (onedr0p → ours)
# Format: "old_value|new_value" per line
# ──────────────────────────────────────────────
REPLACEMENTS=(
    # Domain & NAS
    "turbo.ac|hades.casa"
    "turbo-ac|hades-casa"
    "expanse.internal|hypnos.internal"
    "onedr0p/home-ops|Marshall2HD/home-ops"
    "github.com/onedr0p|github.com/Marshall2HD"
    "avatars.githubusercontent.com/u/213795|avatars.githubusercontent.com/u/15093189"
    # NFS paths
    "/mnt/ceres/Media|/mnt/Elysium/Media"
    "/mnt/eros/Kopia|/mnt/Asphodel/Kopia"
    # Subnets (per-VLAN) — ORDER MATTERS: longer prefixes first to avoid partial matches
    "192.168.42.0/24|10.4.0.0/24"
    "192.168.10.|10.5.0."
    "192.168.69.|10.8.0."
    "192.168.70.|10.7.0."
    "192.168.90.|10.9.0."
    "192.168.1.|10.1.0."
    # VLANs
    "vlanID: 70|vlanID: 7"
    "vlanID: 90|vlanID: 9"
    "bond0.70|bond0.7"
    "bond0.90|bond0.9"
)

# Files/dirs to NEVER overwrite (hardware-specific, or ours-only)
SKIP_PATTERNS=(
    "talos/machineconfig.yaml.j2"
    "talos/schematic.yaml.j2"
    "talos/nodes/"
    "README.md"
    ".github/"
    "scripts/"
    "kubernetes/apps/kube-system/intel-gpu-resource-driver"
)

# Apps that only exist in OUR repo — don't delete them
OUR_ONLY_APPS=(
    "default/chaptarr"
    "default/cobalt"
    "default/decluttarr"
    "default/flaresolverr"
    "default/librechat"
    "default/minecraft"
    "default/open-webui"
    "default/sillytavern"
    "default/speaches"
    "default/vllm"
    "kube-system/node-feature-discovery"
    "kube-system/nvidia-device-plugin"
)

# ──────────────────────────────────────────────
# Setup
# ──────────────────────────────────────────────
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UPSTREAM_URL="https://github.com/onedr0p/home-ops.git"
UPSTREAM_DIR=$(mktemp -d)

trap 'rm -rf "$UPSTREAM_DIR"' EXIT

echo "==> Cloning onedr0p/home-ops into temp dir..."
git clone --depth 1 --quiet "$UPSTREAM_URL" "$UPSTREAM_DIR"

# ──────────────────────────────────────────────
# Helper functions
# ──────────────────────────────────────────────
should_skip() {
    local file="$1"
    for skip in "${SKIP_PATTERNS[@]}"; do
        if [[ "$file" == "$skip"* ]]; then
            return 0
        fi
    done
    return 1
}

is_our_only_app() {
    local file="$1"
    for app in "${OUR_ONLY_APPS[@]}"; do
        if [[ "$file" == "kubernetes/apps/$app"* ]]; then
            return 0
        fi
    done
    return 1
}

# ──────────────────────────────────────────────
# Copy upstream files, skipping protected paths
# ──────────────────────────────────────────────
echo "==> Syncing files..."

copied=0
skipped=0
new_files=0

while IFS= read -r -d '' file; do
    # Strip upstream dir prefix to get relative path
    rel="${file#${UPSTREAM_DIR}/}"

    if should_skip "$rel"; then
        skipped=$((skipped + 1))
        continue
    fi

    target="$REPO_DIR/$rel"

    if $DRY_RUN; then
        if [[ ! -f "$target" ]]; then
            echo "  [NEW] $rel"
            new_files=$((new_files + 1))
        fi
        copied=$((copied + 1))
        continue
    fi

    mkdir -p "$(dirname "$target")"

    if [[ ! -f "$target" ]]; then
        new_files=$((new_files + 1))
    fi

    cp "$file" "$target"
    copied=$((copied + 1))

done < <(find "$UPSTREAM_DIR" -type f ! -path '*/.git/*' ! -name '.DS_Store' -print0)

echo "  Processed: $copied | Skipped: $skipped | New: $new_files"

if $DRY_RUN; then
    echo "==> Dry run complete. No files were modified."
    exit 0
fi

# ──────────────────────────────────────────────
# Apply string replacements
# ──────────────────────────────────────────────
echo "==> Applying replacements..."

cd "$REPO_DIR"

for mapping in "${REPLACEMENTS[@]}"; do
    old_val="${mapping%%|*}"
    new_val="${mapping##*|}"
    echo "  $old_val → $new_val"
    # Use | as sed delimiter since paths contain /
    find . -type f \( -name "*.yaml" -o -name "*.yml" -o -name "*.j2" -o -name "*.toml" -o -name "*.json" -o -name "*.sh" \) \
        ! -path './.git/*' \
        ! -path './scripts/*' \
        ! -path './talos/schematic*' \
        ! -path './talos/machineconfig*' \
        -exec sed -i '' "s|${old_val}|${new_val}|g" {} +
done

echo "==> Replacements applied."

# ──────────────────────────────────────────────
# Stage changes for review
# ──────────────────────────────────────────────
echo "==> Staging changes..."
git add -A

changed=$(git diff --cached --stat | tail -1)
echo ""
echo "════════════════════════════════════════════"
echo "  Sync complete."
echo "  $changed"
echo "════════════════════════════════════════════"
echo ""
echo "  Review:   git diff --cached --stat"
echo "            git diff --cached"
echo "            git diff --cached -- kubernetes/apps/default/sonarr/"
echo ""
echo "  Commit:   git commit -m 'sync: upstream onedr0p/home-ops'"
echo "  Abort:    git checkout . && git reset HEAD"
echo ""
