# OpenSpec

`openspec/specs/` is the normative description of the behaviour currently
implemented by Arduino Mirror. Read the capability specifications there before
changing code or operational behaviour.

`openspec/changes/` contains only active, proposed work. Completed planning
artifacts are retained under `openspec/changes/archive/` as historical context;
they do not define the current contract and may describe superseded layouts or
prototypes.

## Current capabilities

| Capability | Covers |
| --- | --- |
| `publication-pipeline` | Commands, selection, verified publication, cancellation, and observability |
| `archive-index-layout` | Public archive URLs and target object-key layout |
| `network-retry` | Transient HTTP, S3-compatible, and GCS retry policy |
| `archive-availability-fallback` | Replanning after a permanently unavailable archive |
| `xod-fix-package-publication` | XOD archive builds and isolated synchronization to the shared GCS bucket |

## Traceability

| Capability | Implementation | Executable evidence |
| --- | --- | --- |
| `publication-pipeline` | `application/publication.py`, selection policies, CLI, local, S3 and GCS targets | unit selection/target tests; local HTTP/GCS SDK integration; CLI e2e |
| `archive-index-layout` | `domain/publication.py`, `application/selection_common.py`, `entrypoints/config.py` | selection, local-target, S3-target, and GCS-target tests |
| `network-retry` | `infra/retry.py`, HTTP source, archive downloader, S3 and GCS targets | retry, HTTP-source, archive-download, S3-target, and GCS-target tests |
| `archive-availability-fallback` | `domain/publication.py`, selection policies, publication use case | fallback selection and publication tests |
| `xod-fix-package-publication` | XOD build script and GitHub Actions workflow | shared GCS bucket/WIF workflow contract; offline rclone authentication smoke |

The retry specification explicitly records one verified current limitation:
MinIO listing errors raised while consuming its iterator are not retried. The
archived change artifact promised broader list retry, so it is historical rather
than normative.

Validate both active changes and baseline specifications with:

```bash
openspec validate --all --strict --json
```
