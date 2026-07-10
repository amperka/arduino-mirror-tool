"""Shared helpers and the index-filtering logic for the Arduino mirror.

This module is import-safe (no network, no rclone) and is the home of the
pure functions exercised by the test suite.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

ORIGIN_HOST_RE = re.compile(r"https?://downloads\.arduino\.cc")

OFFICIAL_INDEX_URL = "https://downloads.arduino.cc/packages/package_index.json"


def verkey(v: str):
    """Version-ish string -> comparable list (handles dots, dashes, plus)."""
    return [int(x) if x.isdigit() else x for x in re.split(r"[.\-+]", str(v))]


def parse_sha256(checksum: str | None) -> str | None:
    if not checksum:
        return None
    m = re.search(r"([0-9a-fA-F]{64})", checksum)
    return m.group(1).lower() if m else None


def is_mirrorable(url: str | None) -> bool:
    """True only for archives we mirror (host == downloads.arduino.cc)."""
    if not url:
        return False
    return ORIGIN_HOST_RE.search(url) is not None


def rewrite_url(url: str, mirror_host: str) -> str:
    """Point a downloads.arduino.cc URL at the mirror. Others left as-is."""
    if not url:
        return url
    return ORIGIN_HOST_RE.sub(mirror_host, url, count=1)


def relpath_of(url: str) -> str:
    """Path portion of a downloads.arduino.cc URL (S3 object key, no slash)."""
    m = ORIGIN_HOST_RE.search(url)
    if not m:
        raise ValueError(f"URL not on downloads.arduino.cc, cannot mirror: {url}")
    return url[m.end() :].lstrip("/")


def fetch_json(source: str) -> dict:
    """Load JSON from a URL or a local path.

    Uses `requests`, which verifies TLS against certifi's CA bundle by default
    (no dependency on the system trust store, so it works on python.org macOS
    builds and minimal Linux containers alike). Override verification with the
    standard REQUESTS_CA_BUNDLE / CURL_CA_BUNDLE env vars, or disable it with
    ARDUINO_MIRROR_INSECURE=1 (self-signed internal mirrors).
    """
    if source.startswith(("http://", "https://")):
        import requests

        verify = os.environ.get("ARDUINO_MIRROR_INSECURE", "").lower() not in (
            "1",
            "true",
            "yes",
        )
        resp = requests.get(source, timeout=60, verify=verify)
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
) -> dict:
    """Filter + rewrite an upstream package_index into a mirror manifest.

    Returns a dict with keys: mirror_host, generated_from (unused here),
    objects (desired mirror files), index (the rewritten Boards Manager index).
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
            if is_mirrorable(p.get("url")):
                objects.append(
                    {
                        "relpath": relpath_of(p["url"]),
                        "url": rewrite_url(p["url"], mirror_host),
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
                    if is_mirrorable(sys_flavor.get("url")):
                        objects.append(
                            {
                                "relpath": relpath_of(sys_flavor["url"]),
                                "url": rewrite_url(sys_flavor["url"], mirror_host),
                                "sha256": parse_sha256(sys_flavor.get("checksum")),
                                "size": int(sys_flavor.get("size", 0) or 0),
                            }
                        )

    # Rewrite all URLs in the filtered index (downloads.arduino.cc -> mirror).
    out_index = {"packages": list(kept_pkgs.values())}
    raw = json.dumps(out_index, indent=2, ensure_ascii=False)
    raw = ORIGIN_HOST_RE.sub(mirror_host, raw)
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
