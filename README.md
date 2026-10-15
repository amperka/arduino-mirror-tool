# arduino-mirror

Static, filtered mirror of Arduino Boards Manager packages and Library Manager
libraries for networks where `downloads.arduino.cc` is unreachable. It keeps the
latest selected releases, rewrites configured-origin archive URLs to a mirror
host, verifies downloaded archive bytes, and publishes independently to an
GCS bucket, S3-compatible bucket, or a local directory.

Package archives belong to `p/` and library archives to `l/`; the complete
origin-relative path follows that stable namespace.  Indexes follow the same
rule: `p/packages/package_index.json` and `l/libraries/library_index.json`.

## For end users

Arduino IDE 2.x users add the package index to **File → Preferences →
Additional Boards Manager URLs**:

```text
https://arduino-downloads.amperka.ru/p/packages/package_index.json
```

Install boards normally; the mirror overrides the official `arduino:*` entries
for the supported architectures (avr, samd, sam, megaavr, mbed_nano,
mbed_rp2040).

> The mirror index has no Arduino `.sig` signature, so the IDE logs a benign
> "untrusted" warning. Installation is unaffected.

## CLI

The `packages` and `libraries` commands run independent publication pipelines.
Each command fetches one HTTP index, optionally merges a local JSON overlay,
selects latest configured-origin releases, verifies archives, uploads archives
before its index, then cleans stale objects only within its own prefix.

```bash
# Publish to GCS using Application Default Credentials (ADC).
arduino-mirror packages --target gcs --bucket amperka-arduino-downloads
arduino-mirror libraries --target gcs --bucket amperka-arduino-downloads

# Publish configured Board Manager packages to S3-compatible storage.
arduino-mirror packages \
  --target s3 \
  --bucket my-bucket \
  --endpoint storage.yandexcloud.net

# Publish selected latest Library Manager libraries to the same target.
arduino-mirror libraries \
  --target s3 \
  --bucket my-bucket \
  --endpoint storage.yandexcloud.net

# Exercise one publication pipeline without S3 credentials.
arduino-mirror libraries \
  --input http://127.0.0.1:8080/libraries/library_index.json \
  --target local \
  --local-root ./mirror-out

# Inspect a plan without reading or writing a target.
arduino-mirror packages --dry-run --target local
```

Use `--log-level` (or `-l`) with `DEBUG`, `INFO`, `WARNING`, `ERROR`,
`CRITICAL`, or `NOTSET` to set operator-visible logging; the default is `INFO`.
CLI values override non-empty environment variables, which override defaults.
Important variables are `PACKAGES_INPUT_INDEX`, `LIBRARIES_INPUT_INDEX`,
`PACKAGES_LOCAL_INDEX`, `LIBRARIES_LOCAL_INDEX`, `MIRROR_HOST`, `TARGET_KIND`,
`TARGET_BUCKET`, `TARGET_ENDPOINT`, `TARGET_PREFIX`, `AWS_ACCESS_KEY_ID`,
`AWS_SECRET_ACCESS_KEY`, `ARCHITECTURES`, `PACKAGES`, `PINNED_TOOLS`, and
`PINNED_PLATFORMS`.
Archive bytes are held only in automatically cleaned temporary storage during
publication.

The `packages` command also retains exact package tools from `--pinned-tools` or
`PINNED_TOOLS`, formatted as comma-separated `packager:name@version`
identities. The default is
`builtin:ctags@5.8-arduino11,builtin:serial-discovery@1.0.0`. Pinned tools are
added to the latest platform dependencies; malformed non-empty lists fail before
the source index is fetched. To retain an additional tool version, include both
the defaults and the extra exact identity in `PINNED_TOOLS`, for example
`builtin:ctags@5.8-arduino11,builtin:serial-discovery@1.0.0,builtin:serial-discovery@1.1.0`.

Exact platform releases can also be retained with `--pinned-platforms` or
`PINNED_PLATFORMS`, as comma-separated `packager:architecture@version`
identities, for example `arduino:avr@1.8.8`. Platform pins have no default and
are independent of `PACKAGES` and `ARCHITECTURES`; each selected pin retains
its declared exact tool dependencies. An absent or unavailable platform pin is
omitted with a warning and never selects a replacement version.

Pass `--local-index PATH` or set `PACKAGES_LOCAL_INDEX` / `LIBRARIES_LOCAL_INDEX`
to merge one same-family JSON overlay after the remote index is fetched and
before release selection and package-tool pinning. The overlay adds records and
overlays matching package, platform, tool, library, and tool-system records;
tool systems match by `host`. Archive URLs under the configured input origin are
mirrored normally. Other URLs remain unchanged in the published index and are
not uploaded by the mirror.

## Develop and test

The project uses [uv](https://docs.astral.sh/uv):

```bash
uv sync --dev
uv run pytest -m unit
uv run pytest -m integration
uv run pytest -m e2e
uv run ruff check .
uv run ruff format --check .
uv run zuban check
uv run lint-imports
openspec validate --all --json
```

The integration and end-to-end suites use a local HTTP origin and exercise local
storage and the real GCS SDK against a loopback JSON API stub. These tests do not
validate live WIF/IAM or resumable uploads. Unit tests also validate storage
client key mapping, retries, and cancellation without cloud credentials.

## Deployment

The scheduled GitHub Actions workflows independently run `arduino-mirror packages`
and `arduino-mirror libraries` in the `prod` environment. Both publish to the
existing GCS bucket `gs://amperka-arduino-downloads`, with no outer key prefix.
The public host remains `https://arduino-downloads.amperka.ru`.

Configure these repository secrets (or `prod` environment secrets):

| Name | Value |
| --- | --- |
| `GCP_WIF_PROVIDER` | Full `projects/NUMBER/locations/global/workloadIdentityPools/POOL/providers/PROVIDER` resource name |
| `GCP_WIF_SERVICE_ACCOUNT` | Service-account email to impersonate |

The workflows grant `id-token: write` and use
[`google-github-actions/auth@v3`](https://github.com/google-github-actions/auth)
to export an ADC credential file. The SDK uses these refreshable credentials;
no static access token, service-account key, S3 endpoint, or HMAC key is needed.
Generated `gha-creds-*.json` files are ignored by Git and removed by the auth
action after the job.

Required infrastructure, managed outside this repository:

- The WIF trust must admit this repository and the `prod` environment context.
  The external principal needs `roles/iam.workloadIdentityUser` on the service
  account.
- The service account needs object list/create/get/delete permissions on this
  bucket; bucket-scoped
  [`roles/storage.objectAdmin`](https://docs.cloud.google.com/storage/docs/access-control/iam-roles)
  covers publication, overwrite, and stale cleanup. Bucket creation or IAM
  administration is not needed.
- The existing public host must route to this GCS bucket and allow anonymous
  object downloads. This change does not alter bucket IAM, DNS, or HTTPS routing.

For local publication, configure ADC with `gcloud auth application-default login`
or supply `GOOGLE_APPLICATION_CREDENTIALS` pointing to an ADC configuration, then
use `--target gcs --bucket amperka-arduino-downloads`. A preview needs no ADC:

```bash
arduino-mirror packages --dry-run --target gcs --bucket amperka-arduino-downloads
```

The packages workflow preserves the local package overlay and resolves
`MIRROR_HOST`, `ARCHITECTURES`, `PACKAGES`, `PINNED_TOOLS`, and
`PINNED_PLATFORMS` from GitHub variables. The libraries workflow also resolves
`MIRROR_HOST` from GitHub variables. Empty values retain the CLI defaults.

After an authorized first production run of **both mirror workflows**, verify the
public indexes at `/p/packages/package_index.json` and
`/l/libraries/library_index.json` and download at least one archive from each.
These two workflows never read legacy S3 secrets, so those secrets cannot redirect
the mirror pipelines back to the old target.

The separate `xod-fix-packages` workflow still publishes through rclone/S3 and
requires `TARGET_BUCKET`, `TARGET_ENDPOINT`, `TARGET_ACCESS_KEY_ID`, and
`TARGET_SECRET_ACCESS_KEY`. Keep those secrets and storage keys until that
workflow has been migrated and verified separately. Local/S3 CLI support remains
available, and the CLI default remains `s3`. No secrets are deleted automatically.
