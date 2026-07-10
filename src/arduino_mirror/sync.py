"""Bucket reconciliation (list-diff sync) against the mirror manifest.

Pure list-diff helpers live in core.py; this module adds the rclone/network
half: listing the bucket, downloading + verifying archives, and pushing/deleting
objects. Import-safe except for the functions that shell out to rclone.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from .core import managed_keys, top_level_dirs


def _dry_run() -> bool:
    return os.environ.get("DRY_RUN", "false").lower() in ("1", "true", "yes")


CACHE_DIR = Path(os.environ.get("CACHE_DIR", "cache"))


def die(msg: str, code: int = 1) -> None:
    sys.stderr.write(f"ERROR: {msg}\n")
    sys.exit(code)


def run(cmd: list[str], **kw) -> tuple[int, str]:
    """Run an rclone/subprocess command, return (rc, out)."""
    if _dry_run() and cmd[0] == "rclone" and cmd[1] in ("copyto", "deletefile"):
        sys.stderr.write(f"  [dry-run] would run: {' '.join(cmd)}\n")
        return 0, ""
    res = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if res.returncode != 0:
        sys.stderr.write(f"rclone failed ({res.returncode}): {' '.join(cmd)}\n{res.stderr}\n")
    return res.returncode, res.stdout


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def lsjson_bucket(remote: str, bucket: str, prefix: str) -> dict:
    """Return {relkey: {"size": int}} for objects under prefix (relpath form)."""
    base = f"{remote}:{bucket}"
    if prefix:
        base += f"/{prefix}"
    rc, out = run(["rclone", "lsjson", base, "--files-only", "-R"])
    if rc != 0:
        die("cannot list bucket; check rclone config/credentials")
    present: dict[str, dict] = {}
    try:
        entries: list[dict] = json.loads(out) if out.strip() else []
    except json.JSONDecodeError:
        die("rclone lsjson returned non-JSON output")
    for e in entries:
        present[e.get("Path", "")] = {"size": int(e.get("Size", 0))}
    return present


def bucket_target(relkey: str, remote: str, bucket: str, prefix: str) -> str:
    base = f"{remote}:{bucket}"
    if prefix:
        return f"{base}/{prefix}/{relkey}"
    return f"{base}/{relkey}"


def upload_object(obj: dict, remote: str, bucket: str, prefix: str):
    relkey = obj["relpath"]
    url = obj["url"]
    expected = obj.get("sha256")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dest = CACHE_DIR / Path(relkey).name

    if _dry_run():
        sys.stderr.write(f"  [dry-run] would upload {relkey} <- {url}\n")
        return

    need_fetch = True
    if dest.exists() and expected:
        if sha256_of(dest).lower() == expected.lower():
            need_fetch = False
    if need_fetch:
        sys.stderr.write(f"  download {relkey} <- {url}\n")
        import requests

        verify = os.environ.get("ARDUINO_MIRROR_INSECURE", "").lower() not in (
            "1",
            "true",
            "yes",
        )
        with requests.get(url, timeout=600, stream=True, verify=verify) as r:
            r.raise_for_status()
            with dest.open("wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    f.write(chunk)
        if expected and sha256_of(dest).lower() != expected.lower():
            dest.unlink()
            die(f"checksum mismatch after download: {relkey}")
    else:
        sys.stderr.write(f"  cache hit {relkey}\n")
    run(["rclone", "copyto", str(dest), bucket_target(relkey, remote, bucket, prefix)])


def delete_object(relkey: str, remote: str, bucket: str, prefix: str):
    run(["rclone", "deletefile", bucket_target(relkey, remote, bucket, prefix)])


def sync_bucket(
    manifest: dict,
    *,
    remote: str,
    bucket: str,
    prefix: str = "",
):
    """Reconcile the bucket against the manifest (upload missing, delete stale)."""
    objects = manifest.get("objects", [])
    if not objects:
        die("manifest has zero objects -> refusing to run (abort to protect bucket)")

    managed_dirs = top_level_dirs(objects)
    if not managed_dirs:
        die("manifest has no managed top-level dirs -> refusing to run")

    present = lsjson_bucket(remote, bucket, prefix)
    desired = {o["relpath"]: o for o in objects}

    managed_desired, managed_stale, protected = managed_keys(present, desired, managed_dirs)

    to_upload = [k for k in desired if k not in present]
    for k, o in desired.items():
        if k in present and present[k]["size"] != int(o.get("size", 0) or 0):
            to_upload.append(k)

    sys.stderr.write(
        f"bucket objects: {len(present)} | desired: {len(desired)} | "
        f"managed_dirs: {sorted(managed_dirs)}\n"
        f"upload: {len(to_upload)} | delete (stale, managed only): {len(managed_stale)} | "
        f"protected (untouched): {len(protected)}\n"
    )
    if protected:
        sys.stderr.write("  protected root files (kept): " + ", ".join(sorted(protected)) + "\n")

    for relkey in to_upload:
        upload_object(desired[relkey], remote, bucket, prefix)
    for relkey in managed_stale:
        delete_object(relkey, remote, bucket, prefix)

    if _dry_run():
        sys.stderr.write("  [dry-run] would publish package_index.json\n")
        sys.stderr.write("sync complete (dry-run, nothing written)\n")
        return

    idx_json = json.dumps(manifest["index"], indent=2, ensure_ascii=False)
    fd, tmp = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(idx_json)
    run(
        [
            "rclone",
            "copyto",
            tmp,
            bucket_target("package_index.json", remote, bucket, prefix),
        ]
    )
    os.unlink(tmp)

    sys.stderr.write("sync complete\n")
