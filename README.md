# arduino-mirror

Static, filtered mirror of Arduino Boards Manager packages for networks where
`downloads.arduino.cc` is unreachable.

- **What is mirrored:** platforms whose `(packager, architecture)` matches
  `PACKAGES` × `ARCHITECTURES` (default: `arduino` × `avr`), keeping only the
  **latest** version of each when `--latest-only` is set. All OS flavours of the
  required toolchains are included.
- **How it is published:** GitHub Actions fetches the official
  `package_index.json` and builds a **manifest** (filtered + host-rewritten).
  The `sync` step then reconciles the Yandex S3 bucket against that manifest by
  **listing what is actually there and acting per file** — upload missing/
  changed objects, delete only stale objects under the mirror's own top-level
  directories (`cores/`, `tools/`). Weekly on a schedule, or on manual dispatch.
- **Why no local cache?** The job is idempotent via list-diff: an object is
  downloaded only if it is *missing or changed* in the bucket. In steady state
  (no new releases in a week) nothing is downloaded at all — a GHA cache would be
  dead weight (quota + restore/save time) for zero benefit. New versions are
  rare and pulled once from Arduino's CDN. Local `cache/` only buffers the
  current run's downloads for SHA-256 verification; it is not persisted.
- **Storage backend is pluggable.** The `sync` step talks to an abstract
  `MirrorTarget`. Two backends ship: `s3` (minio / S3-compatible, e.g. Yandex
  S3) and `local` (a plain directory tree with the same key layout). The `local`
  target needs no credentials and is great for dry runs, offline CI, and
  previewing the published tree. Pick with `--target s3|local`
  (env `TARGET_KIND`).
- **Your hand-placed root files are safe.** The mirror writes `package_index.json`
  and the `cores/`, `tools/` trees. Stale cleanup deletes **only** keys under
  those managed directories. Loose root files you maintain by hand
  (`index.txt`, `arduino-*.tar.xz` dist mirrors, anything else at bucket root)
  are never touched, and objects in unrelated subdirectories are ignored too.

## For end users (no VPN)

In Arduino IDE 2.x: **File → Preferences → Additional Boards Manager URLs**,
add:

```
https://arduino-downloads.amperka.ru/package_index.json
```

Then install boards normally from Boards Manager. The mirror overrides the
official `arduino:avr` entry (same name), so archives come from the mirror.

> Note: the mirror index is served without an Arduino `.sig` signature, so the
> CLI logs a benign "Missing signature file" / untrusted warning. Installation
> is unaffected.

## CLI

A single `arduino-mirror` entrypoint with subcommands:

```bash
# 1. Build the filtered + host-rewritten manifest only.
arduino-mirror manifest \
  --input https://downloads.arduino.cc/packages/package_index.json \
  --mirror-host https://arduino-downloads.amperka.ru \
  --architectures avr --packages arduino --latest-only \
  --manifest manifest.json

# 2. Reconcile an S3 bucket against an existing manifest (minio / S3-compatible).
arduino-mirror sync \
  --manifest manifest.json \
  --target s3 --bucket my-bucket \
  --endpoint storage.yandexcloud.net \
  --prefix ""

# 2b. ...or a local directory tree (no credentials, great for dry runs / previews).
arduino-mirror sync \
  --manifest manifest.json \
  --target local --local-root ./mirror-out

# 3. Do both (the GitHub Actions entrypoint).
arduino-mirror run
```

All flags fall back to environment variables when not given. Target-related
variables: `TARGET_KIND` (`s3`|`local`, default `s3`), `TARGET_ENDPOINT`,
`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `TARGET_REGION`,
`TARGET_BUCKET` (S3 bucket name), `TARGET_PREFIX`, `TARGET_LOCAL_ROOT`.
Manifest/input variables: `MIRROR_HOST`, `ARCHITECTURES`, `PACKAGES`,
`LATEST_ONLY`, `INPUT_INDEX`, `MANIFEST_PATH`, `DRY_RUN`.

## Develop / test locally

The project uses [uv](https://docs.astral.sh/uv/). Tests cover the filter+rewrite
logic and the deletion-safety of the bucket reconciliation **without any network
or rclone** (they import the package directly).

```bash
uv sync --dev            # install the package + dev deps (pytest)
uv run pytest -q         # tests: filtering, host rewrite, delete-safety

# Dry-run the manifest builder (no download, no upload):
uv run arduino-mirror manifest --dry-run --input official_index.json
```

## Repo layout

```text
src/arduino_mirror/
  core.py        # pure logic: filter, host-rewrite, list-diff helpers
  sync.py        # MirrorTarget abstraction + S3Target (minio) / LocalTarget
  cli.py         # arduino-mirror entrypoint (manifest / sync / run)
  __main__.py    # `python -m arduino_mirror`
.github/workflows/mirror.yml  # scheduled GHA job (test -> manifest -> sync)
tests/                      # unit tests + fixture index
```

## secrets (repo → Settings → Secrets)

| name | value |
|---|---|
| `TARGET_ENDPOINT` | Yandex S3 endpoint, e.g. `storage.yandexcloud.net` |
| `TARGET_ACCESS_KEY_ID` | S3 access key |
| `TARGET_SECRET_ACCESS_KEY` | S3 secret key |
| `TARGET_BUCKET` | target bucket name |

These map to the `TARGET_ENDPOINT` / `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`
/ `TARGET_BUCKET` variables the `s3` target reads. The bucket must allow
**public read** (anonymous GET) so end users can fetch without credentials;
`S3Target` applies a `public-read` bucket policy on the managed prefix
(best-effort), but also enable anonymous read on the bucket/prefix in Yandex.

## Notes / gotchas

- **Size:** `arduino:avr` latest ≈ 270 MB across 6 OS flavours of 3 toolchains
  (the bulk is `avr-gcc`, ~237 MB). Expand `ARCHITECTURES`/`PACKAGES` only if
  you accept the bandwidth/storage cost.
- **Stale cleanup is directory-scoped.** Only keys under the top-level dirs the
  mirror writes (`cores/`, `tools/` by default) are ever deleted. Any other
  root file or subdirectory in the bucket is left untouched.
- **Empty manifest aborts.** If the upstream index can't be fetched, the sync
  step exits without deleting anything — the bucket stays as-is.
- **TLS:** outbound HTTPS (fetching the upstream index, downloading archives)
  uses `requests`, which verifies certificates against certifi's CA bundle by
  default — no reliance on the system trust store. Point it at a custom CA with
  `REQUESTS_CA_BUNDLE=/path`, or disable verification for a self-signed internal
  mirror with `ARDUINO_MIRROR_INSECURE=1`.
- **Don't drop a stale `.sig` next to the index** — a broken signature logs a
  harder warning. The workflow never uploads one.
