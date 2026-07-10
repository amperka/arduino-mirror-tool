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

from .core import OFFICIAL_INDEX_URL, build_manifest
from .sync import sync_bucket

DEFAULT_MIRROR_HOST = "https://arduino-downloads.amperka.ru"


def _env_list(name: str, default: str) -> list[str]:
    val = os.environ.get(name)
    if not val:
        val = default
    return [p.strip() for p in val.split(",") if p.strip()]


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
        "--architectures",
        default=_env_list("ARCHITECTURES", "avr"),
        help="Comma-separated architectures to keep (default: avr).",
    )
    p.add_argument(
        "--packages",
        default=_env_list("PACKAGES", "arduino"),
        help="Comma-separated packager names to keep (default: arduino).",
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


def sync_cmd(args: argparse.Namespace) -> int:
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    sync_bucket(
        manifest,
        remote=args.remote,
        bucket=args.bucket,
        prefix=args.prefix,
    )
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
        default=os.environ.get("INPUT_INDEX", OFFICIAL_INDEX_URL),
        help="Official package_index.json URL or path.",
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

    p_s = sub.add_parser("sync", help="Reconcile a bucket against a manifest.")
    p_s.add_argument(
        "--manifest",
        type=Path,
        default=Path(os.environ.get("MANIFEST_PATH", "manifest.json")),
        help="Manifest to reconcile against (default: manifest.json).",
    )
    p_s.add_argument(
        "--remote",
        default=os.environ.get("RCLONE_REMOTE", "storage"),
        help="rclone remote name (default: storage).",
    )
    p_s.add_argument(
        "--bucket",
        default=os.environ.get("RCLONE_BUCKET", ""),
        help="Target bucket name (required).",
    )
    p_s.add_argument(
        "--prefix",
        default=os.environ.get("RCLONE_PREFIX", ""),
        help="Bucket subdir prefix (default: bucket root).",
    )
    p_s.set_defaults(func=sync_cmd)

    p_r = sub.add_parser("run", help="manifest + sync in one shot.")
    _common_args(p_r)
    p_r.add_argument("--input", default=os.environ.get("INPUT_INDEX", OFFICIAL_INDEX_URL))
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
    p_r.add_argument("--remote", default=os.environ.get("RCLONE_REMOTE", "storage"))
    p_r.add_argument("--bucket", default=os.environ.get("RCLONE_BUCKET", ""))
    p_r.add_argument("--prefix", default=os.environ.get("RCLONE_PREFIX", ""))
    p_r.set_defaults(func=run_cmd)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
