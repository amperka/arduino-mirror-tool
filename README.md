# arduino-mirror

Static, filtered mirror of Arduino Boards Manager packages for networks where
`downloads.arduino.cc` is unreachable. Mirrors the `arduino` package (latest
versions) across avr, samd, sam, megaavr, mbed_nano, and mbed_rp2040
architectures by default, plus the `builtin` package (IDE tools: ctags,
discoveries, serial-monitor) mirrored wholesale with no architecture filter,
and republishes the Boards Manager index with archive URLs rewritten to a
mirror host. Sync runs weekly (GitHub Actions) or on manual dispatch.

## For end users (no VPN)

In Arduino IDE 2.x: **File → Preferences → Additional Boards Manager URLs**, add:

```
https://arduino-downloads.amperka.ru/package_index.json
```

Install boards normally; the mirror overrides the official `arduino:*` entries
for the supported architectures (avr, samd, sam, megaavr, mbed_nano,
mbed_rp2040).

> The mirror index has no Arduino `.sig` signature, so the IDE logs a benign
> "untrusted" warning. Installation is unaffected.

## CLI

A single `arduino-mirror` entrypoint with `manifest` (build the filtered +
host-rewritten index), `sync` (reconcile a target against a manifest), and
`run` (both, the CI entrypoint):

```bash
# 1. Build the filtered + host-rewritten manifest only.
arduino-mirror manifest \
  --input https://downloads.arduino.cc/packages/package_index.json \
  --mirror-host https://arduino-downloads.amperka.ru \
  --architectures avr --packages arduino,builtin --latest-only \
  --manifest manifest.json

# 2. Reconcile an S3 bucket (minio / S3-compatible) against the manifest.
arduino-mirror sync \
  --manifest manifest.json \
  --target s3 --bucket my-bucket --endpoint storage.yandexcloud.net

# 2b. ...or a local directory tree (no credentials; good for dry runs / previews).
arduino-mirror sync --manifest manifest.json --target local --local-root ./mirror-out

# 3. Build + sync in one shot (the GitHub Actions entrypoint).
arduino-mirror run
```

Every flag also reads from an `UPPER_CASE` env var (e.g. `MIRROR_HOST`,
`TARGET_BUCKET`, `TARGET_KIND`). `sync` talks to an abstract `MirrorTarget`:
`--target s3` (minio / S3-compatible) or `--target local` (a plain directory
tree with the same key layout) — handy for offline runs and previews.

## Develop / test locally

The project uses [uv](https://docs.astral.sh/uv). CI gates every push on
`ruff` (lint + format) and `zuban`, plus the pytest suite — run them locally
before pushing:

```bash
uv sync --dev                                          # install package + dev deps
uv run ruff check . && uv run ruff format --check .    # lint gate
uv run zuban check .                                   # extra checks
uv run pytest -q                                       # filtering, host rewrite, delete-safety
```

Tests drive the pure logic (filter, host-rewrite, list-diff reconciliation)
directly — no network, no S3 credentials. Use `--target local` to exercise the
full `sync` path offline.

## Repo layout

```text
src/arduino_mirror/
  core.py     # pure logic: filter, host-rewrite, list-diff helpers
  sync.py     # MirrorTarget abstraction + S3Target (minio) / LocalTarget
  cli.py      # arduino-mirror entrypoint (manifest / sync / run)
  __main__.py # `python -m arduino_mirror`
.github/workflows/mirror.yml  # scheduled GHA job (lint/test -> manifest -> sync)
tests/                      # unit tests + fixture index
```

## Secrets (repo → Settings → Secrets)

| name | value |
|---|---|
| `TARGET_ENDPOINT` | Yandex S3 endpoint, e.g. `storage.yandexcloud.net` |
| `TARGET_ACCESS_KEY_ID` | S3 access key |
| `TARGET_SECRET_ACCESS_KEY` | S3 secret key |
| `TARGET_BUCKET` | target bucket name |

The bucket must allow **public read** (anonymous GET). `S3Target` also applies a
`public-read` bucket policy on the managed prefix (best-effort).

## Notes / gotchas

- **Size:** the mirror covers six architectures (avr, samd, sam, megaavr,
  mbed_nano, mbed_rp2040) with all their toolchain dependencies, plus the
  `builtin` package's IDE tools (under `tools/`, `discovery/`, `monitor/`).
  avr alone is ~270 MB (mostly `avr-gcc`); adding ARM-based cores (samd, sam,
  mbed_*) pulls in `arm-none-eabi-gcc`, `bossac`, `openocd`, `rp2040tools`,
  etc. Widen `ARCHITECTURES`/`PACKAGES` only if you accept the bandwidth +
  storage cost.
- **`builtin` is mirrored without an architecture filter.** It has no
  platforms — only tool releases — so `--architectures` does not apply to it.
  With `--latest-only` (the default), only the newest version of each builtin
  tool name is kept; `--all-versions` keeps every release.
- **Stale cleanup is directory-scoped.** Only keys under the top-level dirs the
  mirror writes (`cores/`, `tools/`, `discovery/`, `monitor/`) are ever
  deleted; root files and unrelated subdirectories are left untouched.
- **Empty manifest aborts.** If the upstream index can't be fetched, `sync`
  exits without touching the bucket.
