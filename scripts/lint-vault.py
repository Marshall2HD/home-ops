#!/usr/bin/env python3
"""Read-only, metadata-only 1Password vault linter for Option A+ hygiene.

Never stores, inspects, or prints field values or totp secrets.
"""

from __future__ import annotations

import argparse
import glob
import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

KEBAB_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
DOMAIN_TAGS = {
    "ai",
    "comms",
    "data",
    "games",
    "home",
    "infra",
    "media",
    "monitoring",
    "network",
    "web",
}
LIFECYCLE_TAGS = {"bootstrap", "break-glass", "human", "pii"}
BUILTIN_FIELD_IDS = {
    "notesPlain",
    "validFrom",
    "expires",
    "public_key",
    "private_key",
    "key_type",
    "fingerprint",
    "database_type",
    "type",
}
BUILTIN_STANDARD_LABELS = {
    "username",
    "password",
    "credential",
    "hostname",
    "email",
    "url",
    "URL",
}
SECTIONED_ALLOWLIST = {
    ("kubernetes", "talos"),
    ("falkerie", "langfuse"),  # live aberrant caller keeps langfuse/aberrant/*
}
PUSHSECRET_ITEMS = {("kubernetes", "hades-casa-tls")}


def fetch_vault_metadata(vault: str) -> tuple[list[dict], list[dict]]:
    """Fetch active and archived items for `vault`, stripping secret values immediately."""
    active_ids = [
        x["id"]
        for x in json.loads(
            subprocess.check_output(["op", "item", "list", "--vault", vault, "--format", "json"])
        )
    ]
    all_including_archived = json.loads(
        subprocess.check_output(
            ["op", "item", "list", "--vault", vault, "--include-archive", "--format", "json"]
        )
    )
    archived = [x for x in all_including_archived if x.get("state") == "ARCHIVED"]

    items: list[dict] = []
    for iid in active_ids:
        raw = json.loads(
            subprocess.check_output(["op", "item", "get", iid, "--vault", vault, "--format", "json"])
        )
        fields = []
        for f in raw.get("fields") or []:
            val = f.get("value")
            fields.append(
                {
                    "id": f.get("id"),
                    "label": f.get("label"),
                    "type": f.get("type"),
                    "purpose": f.get("purpose"),
                    "section": f.get("section"),
                    "nonempty": val is not None and str(val) != "",
                }
            )
        files = [
            {"id": fl.get("id"), "name": fl.get("name"), "section": fl.get("section")}
            for fl in (raw.get("files") or [])
        ]
        items.append(
            {
                "id": raw["id"],
                "title": raw["title"],
                "category": raw.get("category"),
                "tags": raw.get("tags") or [],
                "sections": raw.get("sections") or [],
                "fields": fields,
                "files": files,
            }
        )
    return items, archived


def collect_k8s_refs(repo_root: Path) -> tuple[list[tuple[str, str, str]], dict[str, set[str]]]:
    """Collect ExternalSecret remoteRefs and bootstrap/talos op://kubernetes/... refs."""
    refs: list[tuple[str, str, str]] = []
    used_by_item: dict[str, set[str]] = {}

    for path_str in sorted(
        glob.glob(str(repo_root / "kubernetes/**/externalsecret*.yaml"), recursive=True)
    ):
        rel = str(Path(path_str).relative_to(repo_root))
        for doc in yaml.safe_load_all(Path(path_str).read_text()):
            if not doc or doc.get("kind") != "ExternalSecret":
                continue
            for entry in doc.get("spec", {}).get("data", []):
                rr = entry.get("remoteRef", {})
                k, prop = rr.get("key"), rr.get("property")
                if k and prop:
                    refs.append((rel, k, prop))
                    used_by_item.setdefault(k, set()).add(prop)
            for df in doc.get("spec", {}).get("dataFrom", []):
                k = df.get("extract", {}).get("key")
                if k:
                    refs.append((rel, k, "<extract>"))
                    used_by_item.setdefault(k, set()).add("<extract>")

    op_re = re.compile(r"""op://[Kk]ubernetes/([^/\s"\']+)/([^\s"\']+)""")
    for sub in ("bootstrap", "talos"):
        for path in sorted((repo_root / sub).rglob("*")):
            if not path.is_file() or path.suffix in {".png", ".jpg", ".md"}:
                continue
            rel = str(path.relative_to(repo_root))
            text = path.read_text(errors="ignore")
            for m in op_re.finditer(text):
                k, prop = m.group(1), m.group(2)
                refs.append((rel, k, prop))
                used_by_item.setdefault(k, set()).add(prop)

    return refs, used_by_item


def lint_vault(vault: str, repo_root: Path) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    v_low = vault.lower()

    items, archived = fetch_vault_metadata(vault)
    by_title: dict[str, list[dict]] = {}
    for it in items:
        by_title.setdefault(it["title"], []).append(it)

    # 1. Duplicate active titles & archived-title collisions
    for title, group in sorted(by_title.items()):
        if len(group) > 1:
            errors.append(f"[{vault}] duplicate active title {title!r}: {[g['id'] for g in group]}")
    archived_by_title: dict[str, list[str]] = {}
    for a in archived:
        archived_by_title.setdefault(a["title"], []).append(a["id"])
    for title in sorted(set(by_title) & set(archived_by_title)):
        warnings.append(
            f"[{vault}] archived item(s) {archived_by_title[title]} share active title {title!r} "
            "(breaks `op inject`; delete archived duplicate in 1Password UI)"
        )

    # 2. Item schema hygiene
    for it in sorted(items, key=lambda x: x["title"].lower()):
        title = it["title"]
        is_ssh = it["category"] == "SSH_KEY"
        is_push = (v_low, title) in PUSHSECRET_ITEMS
        allow_sec = (v_low, title) in SECTIONED_ALLOWLIST

        if not KEBAB_RE.match(title):
            msg = f"[{vault}] item {title!r} ({it['id']}) title is not lowercase kebab-case"
            (warnings if is_ssh else errors).append(msg)

        for sec in it["sections"]:
            if sec.get("label") is None or sec.get("id") == "add more":
                msg = f"[{vault}] item {title!r} has phantom/null section {sec!r}"
                (warnings if is_ssh else errors).append(msg)

        nonempty_in_section = [
            f for f in it["fields"] if f.get("section") is not None and f["nonempty"]
        ]
        if nonempty_in_section and not allow_sec:
            msg = (
                f"[{vault}] item {title!r} has populated fields inside sections: "
                f"{[f['label'] for f in nonempty_in_section]}"
            )
            (warnings if is_ssh else errors).append(msg)
        elif it["sections"] and not allow_sec and not nonempty_in_section:
            warnings.append(
                f"[{vault}] item {title!r} has empty leftover section(s) "
                f"{[s.get('label') or s.get('id') for s in it['sections']]}"
            )

        seen_labels: dict[tuple[str | None, str], int] = {}
        for f in it["fields"]:
            lbl = f.get("label") or ""
            sec_id = (f.get("section") or {}).get("id") if allow_sec else None
            if lbl:
                key_lbl = (sec_id, lbl)
                seen_labels[key_lbl] = seen_labels.get(key_lbl, 0) + 1
            if not f["nonempty"] or not lbl:
                continue
            fid = f.get("id") or ""
            if fid in BUILTIN_FIELD_IDS or lbl in BUILTIN_STANDARD_LABELS or f.get("type") == "OTP":
                continue
            if is_push or allow_sec:
                continue
            if not KEBAB_RE.match(lbl):
                msg = f"[{vault}] item {title!r} field {lbl!r} is not lowercase kebab-case"
                (warnings if (is_ssh or (v_low == "kubernetes" and lbl == "SMTP server")) else errors).append(
                    f"{msg} (transitional Stage C)" if lbl == "SMTP server" else msg
                )

        dups = [f"{sec}/{lbl}" if sec else lbl for (sec, lbl), cnt in seen_labels.items() if cnt > 1]
        if dups:
            errors.append(f"[{vault}] item {title!r} has duplicate field labels: {dups}")

        if not is_push:
            tags = it["tags"]
            domains = [t for t in tags if t in DOMAIN_TAGS]
            bad_tags = [
                t
                for t in tags
                if t not in DOMAIN_TAGS
                and t not in LIFECYCLE_TAGS
                and not t.startswith(("host:", "owner:"))
            ]
            if len(domains) != 1 or bad_tags:
                msg = f"[{vault}] item {title!r} invalid tags {tags!r} (domains={domains}, invalid={bad_tags})"
                (warnings if is_ssh else errors).append(msg)

    # 3. Kubernetes cross-references
    if v_low == "kubernetes":
        refs, used_by_item = collect_k8s_refs(repo_root)
        for src, key, prop in refs:
            matches = by_title.get(key)
            if not matches:
                errors.append(f"[{vault}] {src}: missing item {key!r} (property {prop!r})")
                continue
            if len(matches) > 1:
                continue
            it = matches[0]
            if prop == "<extract>":
                continue
            if prop.startswith("file/"):
                fname = prop.split("/", 1)[1]
                if not any(fl["name"] == fname for fl in it["files"]):
                    errors.append(f"[{vault}] {src}: missing file {fname!r} on item {key!r}")
                continue
            if "/" in prop:
                sec_name, fld_name = prop.split("/", 1)
                sec_ids = {s["id"] for s in it["sections"] if (s.get("label") or "").lower() == sec_name.lower()}
                found = any(
                    f["nonempty"]
                    and f.get("label") == fld_name
                    and (f.get("section") or {}).get("id") in sec_ids
                    for f in it["fields"]
                )
                if not found:
                    errors.append(f"[{vault}] {src}: missing {sec_name}/{fld_name} on {key!r}")
            else:
                found = any(
                    (f["nonempty"] and f.get("label") == prop)
                    for f in it["fields"]
                ) or any(fl["name"] == prop for fl in it["files"])
                if not found:
                    errors.append(f"[{vault}] {src}: missing property {prop!r} on item {key!r}")

        for title, group in sorted(by_title.items()):
            if len(group) != 1:
                continue
            it = group[0]
            used = used_by_item.get(title, set())
            if not used:
                warnings.append(f"[{vault}] unreferenced active item {title!r} (legacy / Stage C candidate)")
                continue
            if "<extract>" in used or title == "talos":
                continue
            for f in it["fields"]:
                if not f["nonempty"]:
                    continue
                lbl = f.get("label") or ""
                if f.get("id") in BUILTIN_FIELD_IDS or lbl in {"type", "valid from", "expires"}:
                    continue
                if lbl not in used:
                    warnings.append(f"[{vault}] unused field {title}/{lbl} (verify if Stage C cleanup)")

    return errors, warnings


def main() -> int:
    parser = argparse.ArgumentParser(description="Metadata-only 1Password Option A+ vault linter")
    parser.add_argument("--vault", default="Kubernetes", help="Vault name (Kubernetes or Falkerie)")
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parent.parent))
    args = parser.parse_args()

    errors, warnings = lint_vault(args.vault, Path(args.repo_root))
    for w in warnings:
        print(f"WARN  {w}")
    for e in errors:
        print(f"ERROR {e}")
    print(f"Summary ({args.vault}): {len(errors)} error(s), {len(warnings)} warning(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
