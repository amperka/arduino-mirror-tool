"""Bucket reconciliation (list-diff sync) against the mirror manifest.

Pure list-diff helpers live in core.py; this module adds the network half:
listing the target, downloading + verifying archives, and pushing/deleting
objects.

A `MirrorTarget` abstracts the storage backend so callers don't care whether
objects land in Yandex S3 or a plain local directory (handy for dry runs,
offline CI, and local previews). The only concrete backends shipped are
`S3Target` (minio / S3-compatible) and `LocalTarget` (filesystem tree).
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path

from .core import managed_keys, top_level_dirs


def _dry_run() -> bool:
    return os.environ.get("DRY_RUN", "false").lower() in ("1", "true", "yes")


CACHE_DIR = Path(os.environ.get("CACHE_DIR", "cache"))


def die(msg: str, code: int = 1) -> None:
    sys.stderr.write(f"ERROR: {msg}\n")
    sys.exit(code)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------- #
# Storage abstraction                                                         #
# --------------------------------------------------------------------------- #


class MirrorTarget(ABC):
    """Minimal object-store surface the mirror needs.

    Implementations map object keys to storage. A key is always the S3-style
    relpath (e.g. ``cores/staging/avr-1.8.8.tar.bz2`` / ``package_index.json``),
    never OS-specific. ``LocalTarget`` keeps that same layout under a root dir;
    ``S3Target`` maps it straight onto the bucket.
    """

    @abstractmethod
    def list_keys(self) -> dict[str, dict]:
        """Return ``{key: {"size": int}}`` for every managed object present."""

    @abstractmethod
    def upload_file(self, local_path: Path, key: str) -> None:
        """Put/upload a local file at ``key``."""

    @abstractmethod
    def delete_key(self, key: str) -> None:
        """Delete the object at ``key`` (no-op if absent)."""

    @abstractmethod
    def prepare_public_read(self) -> None:
        """Make managed objects anonymously readable (best-effort)."""


class S3Target(MirrorTarget):
    def __init__(
        self,
        *,
        bucket: str,
        endpoint: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        region: str | None = None,
        prefix: str = "",
        public_read: bool = True,
    ) -> None:
        from minio import Minio

        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.public_read = public_read
        self._policy_applied = False
        self._client = Minio(
            endpoint or "storage.yandexcloud.net",
            access_key=access_key or os.environ.get("AWS_ACCESS_KEY_ID", ""),
            secret_key=secret_key or os.environ.get("AWS_SECRET_ACCESS_KEY", ""),
            region=region,
            secure=True,
        )

    def _full_key(self, key: str) -> str:
        return f"{self.prefix}/{key}" if self.prefix else key

    def _resource_arn(self, key: str) -> str:
        arn = f"arn:aws:s3:::{self.bucket}"
        if key:
            arn += f"/{key}"
        return arn

    def prepare_public_read(self) -> None:
        if not self.public_read or self._policy_applied:
            return
        # Anonymous s3:GetObject on the managed prefix (or whole bucket).
        # Applied once per sync; idempotent. Non-fatal if the credential lacks
        # SetBucketPolicy rights — the bucket should already be public-read in
        # Yandex per the deployment docs.
        resource = self._resource_arn(f"{self.prefix}/*" if self.prefix else "*")
        policy = json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {"AWS": ["*"]},
                        "Action": ["s3:GetObject"],
                        "Resource": [resource],
                    }
                ],
            }
        )
        try:
            self._client.set_bucket_policy(self.bucket, policy)
            self._policy_applied = True
            sys.stderr.write("  applied public-read bucket policy\n")
        except Exception as exc:  # noqa: BLE001 - best-effort, non-fatal
            sys.stderr.write(f"  WARNING: could not set public-read policy: {exc}\n")

    def list_keys(self) -> dict[str, dict]:
        present: dict[str, dict] = {}
        for obj in self._client.list_objects(self.bucket, prefix=self.prefix, recursive=True):
            if obj.object_name is None or obj.is_dir:
                continue
            rel = obj.object_name
            if self.prefix and rel.startswith(self.prefix + "/"):
                rel = rel[len(self.prefix) + 1 :]
            elif self.prefix and rel == self.prefix:
                continue
            present[rel] = {"size": int(obj.size or 0)}
        return present

    def upload_file(self, local_path: Path, key: str) -> None:
        self._client.fput_object(self.bucket, self._full_key(key), str(local_path))

    def delete_key(self, key: str) -> None:
        self._client.remove_object(self.bucket, self._full_key(key))


class LocalTarget(MirrorTarget):
    """A directory tree mirroring the bucket — same keys, same layout."""

    def __init__(self, *, root: str | Path, prefix: str = "") -> None:
        self.root = Path(root)
        self.prefix = prefix.strip("/")

    def _path(self, key: str) -> Path:
        rel = f"{self.prefix}/{key}" if self.prefix else key
        return self.root / rel

    def list_keys(self) -> dict[str, dict]:
        base = self._path("")
        if not base.exists():
            return {}
        present: dict[str, dict] = {}
        for p in base.rglob("*"):
            if p.is_file():
                rel = str(p.relative_to(base)).replace(os.sep, "/")
                present[rel] = {"size": int(p.stat().st_size)}
        return present

    def upload_file(self, local_path: Path, key: str) -> None:
        dest = self._path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(local_path.read_bytes())

    def delete_key(self, key: str) -> None:
        p = self._path(key)
        if p.exists():
            p.unlink()

    def prepare_public_read(self) -> None:
        # Local trees are already readable on disk; nothing to do.
        return


def build_target(
    *,
    kind: str,
    bucket: str = "",
    prefix: str = "",
    endpoint: str | None = None,
    access_key: str | None = None,
    secret_key: str | None = None,
    region: str | None = None,
    local_root: str | Path = "",
) -> MirrorTarget:
    """Construct a storage target from CLI flags / env.

    kind == "s3"   -> S3Target (endpoint/keys fall back to AWS_* env).
    kind == "local"-> LocalTarget (root from --local-root / TARGET_LOCAL_ROOT).
    """
    if kind == "local":
        root = local_root or os.environ.get("TARGET_LOCAL_ROOT", "mirror-out")
        return LocalTarget(root=root, prefix=prefix)
    if kind == "s3":
        if not bucket:
            die("S3 target requires a bucket (--bucket / TARGET_BUCKET)")
        return S3Target(
            bucket=bucket,
            endpoint=endpoint or os.environ.get("TARGET_ENDPOINT"),
            access_key=access_key or os.environ.get("AWS_ACCESS_KEY_ID"),
            secret_key=secret_key or os.environ.get("AWS_SECRET_ACCESS_KEY"),
            region=region or os.environ.get("TARGET_REGION"),
            prefix=prefix,
        )
    die(f"unknown target kind: {kind!r} (expected 's3' or 'local')")


# --------------------------------------------------------------------------- #
# Sync logic                                                                  #
# --------------------------------------------------------------------------- #


def upload_object(obj: dict, target: MirrorTarget):
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
    target.upload_file(dest, relkey)


def delete_object(relkey: str, target: MirrorTarget):
    target.delete_key(relkey)


def write_index(manifest: dict, target: MirrorTarget) -> None:
    idx_json = json.dumps(manifest["index"], indent=2, ensure_ascii=False)
    if _dry_run():
        sys.stderr.write("  [dry-run] would publish package_index.json\n")
        return
    fd, tmp = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(idx_json)
    try:
        target.upload_file(Path(tmp), "package_index.json")
    finally:
        os.unlink(tmp)


def sync_bucket(
    manifest: dict,
    *,
    target: MirrorTarget,
) -> None:
    """Reconcile the target against the manifest (upload missing, delete stale)."""
    objects = manifest.get("objects", [])
    if not objects:
        die("manifest has zero objects -> refusing to run (abort to protect target)")

    managed_dirs = top_level_dirs(objects)
    if not managed_dirs:
        die("manifest has no managed top-level dirs -> refusing to run")

    target.prepare_public_read()
    present = target.list_keys()
    desired = {o["relpath"]: o for o in objects}

    _managed_desired, managed_stale, protected = managed_keys(present, desired, managed_dirs)

    to_upload = [k for k in desired if k not in present]
    for k, o in desired.items():
        if k in present and present[k]["size"] != int(o.get("size", 0) or 0):
            to_upload.append(k)

    sys.stderr.write(
        f"target objects: {len(present)} | desired: {len(desired)} | "
        f"managed_dirs: {sorted(managed_dirs)}\n"
        f"upload: {len(to_upload)} | delete (stale, managed only): {len(managed_stale)} | "
        f"protected (untouched): {len(protected)}\n"
    )
    if protected:
        sys.stderr.write("  protected root files (kept): " + ", ".join(sorted(protected)) + "\n")

    for relkey in to_upload:
        upload_object(desired[relkey], target)
    for relkey in managed_stale:
        delete_object(relkey, target)

    write_index(manifest, target)

    if _dry_run():
        sys.stderr.write("sync complete (dry-run, nothing written)\n")
    else:
        sys.stderr.write("sync complete\n")
