"""Command-line entrypoint for the Arduino mirror tool.

Subcommands:
  manifest  Build the filtered, host-rewritten mirror manifest (manifest.json).
  sync      Reconcile a bucket against an existing manifest (list-diff upload/delete).
  run       manifest + sync in one shot (the GitHub Actions entrypoint).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .core import build_manifest
from .sync import MirrorTarget, sync_bucket

# Defaults for the two URL prefixes, co-located so the CLI is the single
# source of truth for both src (origin) and target (mirror) values.
DEFAULT_ORIGIN_PREFIX = "https://downloads.arduino.cc"
DEFAULT_MIRROR_HOST = "https://arduino-downloads.amperka.ru"


def _env_list(name: str, default: str) -> list[str]:
    val = os.environ.get(name)
    if not val:
        val = default
    return _split_csv(val)


def _split_csv(s: str) -> list[str]:
    return [p.strip() for p in s.split(",") if p.strip()]


def _env_bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes")


def _common_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--mirror-host",
        default=os.environ.get("MIRROR_HOST", DEFAULT_MIRROR_HOST),
        help="Base URL of the published mirror (rewrites downloads.arduino.cc).",
    )
    p.add_argument(
        "--origin-host",
        default=DEFAULT_ORIGIN_PREFIX,
        help="Upstream archive URL prefix to filter/mirror (default: https://downloads.arduino.cc).",
    )
    p.add_argument(
        "--architectures",
        type=_split_csv,
        default=_env_list("ARCHITECTURES", "avr,samd,sam,megaavr,mbed_nano,mbed_rp2040"),
        help="Comma-separated architectures to keep "
        "(default: avr,samd,sam,megaavr,mbed_nano,mbed_rp2040).",
    )
    p.add_argument(
        "--packages",
        type=_split_csv,
        default=_env_list("PACKAGES", "arduino,builtin"),
        help="Comma-separated packager names to keep (default: arduino,builtin). "
        "Packages with no platforms (e.g. builtin) are mirrored as tool-only: "
        "all tool releases, latest version per tool name, no architecture filter.",
    )
    p.add_argument(
        "--latest-only",
        action="store_true",
        default=_env_bool("LATEST_ONLY", True),
        help="Keep only the latest version per (package, arch).",
    )
    p.add_argument(
        "--all-versions",
        dest="latest_only",
        action="store_false",
        help="Keep every matching version (overrides --latest-only).",
    )


def build_manifest_cmd(args: argparse.Namespace) -> int:
    from .core import fetch_json  # local import keeps CLI import light

    index = fetch_json(args.input)
    manifest = build_manifest(
        index,
        packages=args.packages,
        architectures=args.architectures,
        latest_only=args.latest_only,
        mirror_host=args.mirror_host,
        src_prefix=args.origin_host,
    )
    args.manifest.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    if args.dry_run:
        for o in manifest["objects"]:
            sys.stderr.write(f"  [dry-run] object {o['relpath']} ({o['size']} bytes)\n")

    n_pkgs = len(manifest["index"]["packages"])
    n_plats = sum(len(p["platforms"]) for p in manifest["index"]["packages"])
    n_tools = sum(len(p["tools"]) for p in manifest["index"]["packages"])
    sys.stderr.write(
        f"kept: {n_pkgs} package(s), {n_plats} platform(s), {n_tools} tool release(s)\n"
        f"mirror objects: {len(manifest['objects'])} | architectures: {args.architectures} | "
        f"packages: {args.packages} | latest_only: {args.latest_only}\n"
        f"mirror host: {args.mirror_host}\n"
        f"manifest: {args.manifest}\n"
    )
    return 0


def _build_target(args: argparse.Namespace) -> MirrorTarget:
    from .sync import build_target

    return build_target(
        kind=args.target,
        bucket=args.bucket,
        prefix=args.prefix,
        endpoint=args.endpoint,
        access_key=args.access_key,
        secret_key=args.secret_key,
        region=args.region,
        local_root=args.local_root,
    )


def _target_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--target",
        default=os.environ.get("TARGET_KIND", "s3"),
        choices=["s3", "local"],
        help="Storage backend: 's3' (minio/S3-compatible) or 'local' (directory tree).",
    )
    p.add_argument(
        "--bucket",
        default=os.environ.get("TARGET_BUCKET", ""),
        help="S3 target bucket name (required for --target s3).",
    )
    p.add_argument(
        "--prefix",
        default=os.environ.get("TARGET_PREFIX", ""),
        help="Bucket subdir / local root subdir prefix (default: root).",
    )
    p.add_argument(
        "--endpoint",
        default=os.environ.get("TARGET_ENDPOINT", ""),
        help="S3 endpoint, e.g. storage.yandexcloud.net (else AWS_* env).",
    )
    p.add_argument(
        "--access-key",
        default=os.environ.get("AWS_ACCESS_KEY_ID", ""),
        help="S3 access key (else AWS_ACCESS_KEY_ID env).",
    )
    p.add_argument(
        "--secret-key",
        default=os.environ.get("AWS_SECRET_ACCESS_KEY", ""),
        help="S3 secret key (else AWS_SECRET_ACCESS_KEY env).",
    )
    p.add_argument(
        "--region",
        default=os.environ.get("TARGET_REGION", ""),
        help="S3 region (optional).",
    )
    p.add_argument(
        "--local-root",
        default=os.environ.get("TARGET_LOCAL_ROOT", "mirror-out"),
        help="Local target root directory (for --target local).",
    )


def sync_cmd(args: argparse.Namespace) -> int:
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    sync_bucket(manifest, target=_build_target(args))
    return 0


def run_cmd(args: argparse.Namespace) -> int:
    # manifest then sync, reusing the same manifest path.
    rc = build_manifest_cmd(args)
    if rc:
        return rc
    if args.dry_run:
        sys.stderr.write("  [dry-run] skipping sync (no upload/delete)\n")
        return 0
    return sync_cmd(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="arduino-mirror",
        description="Filtered static mirror of Arduino Boards Manager packages.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_m = sub.add_parser("manifest", help="Build the mirror manifest (filter + rewrite host).")
    _common_args(p_m)
    p_m.add_argument(
        "--input",
        default=os.environ.get("INPUT_INDEX"),
        help="package_index.json URL/path. Default: <origin-host>/packages/package_index.json.",
    )
    p_m.add_argument(
        "--manifest",
        type=Path,
        default=Path(os.environ.get("MANIFEST_PATH", "manifest.json")),
        help="Output manifest path (default: manifest.json).",
    )
    p_m.add_argument(
        "--dry-run",
        action="store_true",
        default=_env_bool("DRY_RUN", False),
        help="Print the planned objects without downloading anything.",
    )
    p_m.set_defaults(func=build_manifest_cmd)

    p_s = sub.add_parser("sync", help="Reconcile a target against a manifest.")
    p_s.add_argument(
        "--manifest",
        type=Path,
        default=Path(os.environ.get("MANIFEST_PATH", "manifest.json")),
        help="Manifest to reconcile against (default: manifest.json).",
    )
    _target_args(p_s)
    p_s.set_defaults(func=sync_cmd)

    p_r = sub.add_parser("run", help="manifest + sync in one shot.")
    _common_args(p_r)
    p_r.add_argument("--input", default=os.environ.get("INPUT_INDEX"))
    p_r.add_argument(
        "--manifest",
        type=Path,
        default=Path(os.environ.get("MANIFEST_PATH", "manifest.json")),
    )
    p_r.add_argument(
        "--dry-run",
        action="store_true",
        default=_env_bool("DRY_RUN", False),
        help="Build manifest, print plan, skip download/upload.",
    )
    _target_args(p_r)
    p_r.set_defaults(func=run_cmd)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    # --input is derived from --origin-host unless explicitly overridden
    # (CLI flag or INPUT_INDEX env). Index URL is always <origin>/packages/...
    if not args.input:
        args.input = args.origin_host.rstrip("/") + "/packages/package_index.json"
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
