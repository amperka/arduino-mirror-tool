"""Shared helpers and the index-filtering logic for the Arduino mirror.

This module is import-safe (no network, no rclone) and is the home of the
pure functions exercised by the test suite.

Mirroring model -- two URL prefixes, period:
  * src_prefix: where upstream archives live. `objects[].url` keeps it, and
    sync downloads from it.
  * target_prefix (--mirror-host): what the published Boards Manager index
    advertises so clients fetch from us.

An archive URL always STARTS WITH src_prefix. Mirroring = keep object URLs
on the origin, rewrite the published index src -> target. No host parsing,
no regex, no env re-reads, no module-level prefix default: the two prefixes
come in once via CLI and are threaded through build_manifest as required
arguments. There is no fallback to a hardcoded prefix -- a missing src_prefix
is a caller bug, surfaced immediately, not silently defaulted.
"""

from __future__ import annotations

import json
import re
from pathlib import Path


def verkey(v: str):
    """Version-ish string -> comparable list (handles dots, dashes, plus)."""
    return [int(x) if x.isdigit() else x for x in re.split(r"[.\-+]", str(v))]


def parse_sha256(checksum: str | None) -> str | None:
    if not checksum:
        return None
    m = re.search(r"([0-9a-fA-F]{64})", checksum)
    return m.group(1).lower() if m else None


def is_mirrorable(url: str | None, src_prefix: str) -> bool:
    """True only for archives whose URL starts with the origin (src) prefix."""
    return bool(url) and str(url).startswith(src_prefix)


def rewrite_index_url(url: str, target_prefix: str, src_prefix: str) -> str:
    """Rewrite a single origin URL to the published mirror (target) prefix.

    Only touches URLs that actually start with src_prefix (archive URLs). Other
    URLs (help, website) pass through unchanged. Download *source* URLs in the
    manifest objects must NOT be rewritten -- sync fetches them from src.
    """
    if not url or not url.startswith(src_prefix):
        return url
    return target_prefix + url[len(src_prefix) :]


def relpath_of(url: str, src_prefix: str) -> str:
    """Path portion of an origin URL (S3 object key), with no leading slash."""
    if not url.startswith(src_prefix):
        raise ValueError(f"URL not on origin prefix, cannot mirror: {url}")
    return url[len(src_prefix) :].lstrip("/")


def fetch_json(source: str) -> dict:
    """Load JSON from a URL or a local path.

    Uses `requests`, which verifies TLS against certifi's CA bundle by default
    (no dependency on the system trust store). Override the bundle with the
    standard REQUESTS_CA_BUNDLE / CURL_CA_BUNDLE env vars.
    """
    if source.startswith(("http://", "https://")):
        import requests

        resp = requests.get(source, timeout=60)
        resp.raise_for_status()
        return resp.json()
    return json.loads(Path(source).read_text(encoding="utf-8"))


def build_manifest(
    index: dict,
    *,
    packages: list[str],
    architectures: list[str],
    latest_only: bool,
    mirror_host: str,
    src_prefix: str,
) -> dict:
    """Filter + rewrite an upstream package_index into a mirror manifest.

    Returns a dict with keys: mirror_host, objects (desired mirror files),
    index (the rewritten Boards Manager index).
    """
    kept_pkgs: dict[str, dict] = {}
    kept_tools: set[tuple[str, str, str]] = set()
    objects: list[dict] = []

    def pkg_skeleton(pkg: dict) -> dict:
        return {
            "name": pkg.get("name"),
            "maintainer": pkg.get("maintainer"),
            "websiteURL": pkg.get("websiteURL"),
            "url": pkg.get("url"),
            "email": pkg.get("email"),
            "help": pkg.get("help", {}),
            "platforms": [],
            "tools": [],
        }

    # Pass 1: pick platforms + collect tool dependencies.
    for pkg in index.get("packages", []):
        if pkg.get("name") not in packages:
            continue
        plats = [p for p in pkg.get("platforms", []) if p.get("architecture") in architectures]
        if not plats:
            continue
        if latest_only:
            plats = [max(plats, key=lambda p: verkey(p.get("version", "0")))]
        meta = kept_pkgs.setdefault(pkg["name"], pkg_skeleton(pkg))
        for p in plats:
            meta["platforms"].append(p)
            if is_mirrorable(p.get("url"), src_prefix):
                objects.append(
                    {
                        "relpath": relpath_of(p["url"], src_prefix),
                        # Download SOURCE url is kept verbatim (origin prefix).
                        # sync fetches from here; the published index is what
                        # gets prefix-rewritten for clients.
                        "url": p["url"],
                        "sha256": parse_sha256(p.get("checksum")),
                        "size": int(p.get("size", 0) or 0),
                    }
                )
            for td in p.get("toolsDependencies", []):
                kept_tools.add((td["packager"], td["name"], str(td["version"])))

    # Ensure tool-owning packages are present even without a kept platform.
    for pkg in index.get("packages", []):
        if any(
            (pkg["name"], t["name"], str(t["version"])) in kept_tools for t in pkg.get("tools", [])
        ):
            kept_pkgs.setdefault(pkg["name"], pkg_skeleton(pkg))

    # Pass 2: attach ONLY the required tool releases to their owning package.
    for pkg in index.get("packages", []):
        out = kept_pkgs.get(pkg["name"])
        if out is None:
            continue
        for tool in pkg.get("tools", []):
            if (pkg["name"], tool["name"], str(tool["version"])) in kept_tools:
                out["tools"].append(tool)
                for sys_flavor in tool.get("systems", []):
                    if is_mirrorable(sys_flavor.get("url"), src_prefix):
                        objects.append(
                            {
                                "relpath": relpath_of(sys_flavor["url"], src_prefix),
                                "url": sys_flavor["url"],  # origin prefix, see note above
                                "sha256": parse_sha256(sys_flavor.get("checksum")),
                                "size": int(sys_flavor.get("size", 0) or 0),
                            }
                        )

    # Rewrite the published index: every origin (src) URL -> mirror (target).
    # Object download URLs live in `objects` above and are intentionally left
    # on the origin so sync pulls from upstream.
    out_index = {"packages": list(kept_pkgs.values())}
    raw = json.dumps(out_index, indent=2, ensure_ascii=False)
    raw = raw.replace(src_prefix, mirror_host)
    out_index = json.loads(raw)
    out_index["packages"] = [
        p for p in out_index["packages"] if p.get("platforms") or p.get("tools")
    ]

    # De-duplicate objects (same relpath can appear from platform + tool paths).
    seen: dict[str, dict] = {}
    for o in objects:
        seen[o["relpath"]] = o
    objects = list(seen.values())

    return {
        "mirror_host": mirror_host,
        "objects": objects,
        "index": out_index,
    }


def top_level_dirs(objects: list[dict]) -> set[str]:
    """Top-level directory names present in manifest relpaths.

    e.g. {"cores/staging/avr.tar.bz2", "tools/avr-gcc.tar.bz2"} -> {"cores", "tools"}
    """
    dirs: set[str] = set()
    for o in objects:
        rel = o.get("relpath", "")
        if "/" in rel:
            dirs.add(rel.split("/", 1)[0])
    return dirs


def managed_keys(present: dict, desired: dict, managed_dirs: set[str]):
    """Partition present keys into (managed_desired, managed_stale, protected).

    - managed_desired: under a managed top-level dir AND in the desired set.
    - managed_stale:   under a managed top-level dir but NOT in desired -> delete.
    - protected:       loose root files / dirs outside managed_dirs -> untouched.
    """
    desired_keys = set(desired)
    managed_desired, managed_stale, protected = [], [], []
    for key in present:
        top = key.split("/", 1)[0] if "/" in key else ""
        if top in managed_dirs:
            if key in desired_keys:
                managed_desired.append(key)
            else:
                managed_stale.append(key)
        else:
            protected.append(key)
    return managed_desired, managed_stale, protected
