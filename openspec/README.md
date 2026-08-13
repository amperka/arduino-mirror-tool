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
| `network-retry` | Transient HTTP and S3-compatible retry policy |
| `archive-availability-fallback` | Replanning after a permanently unavailable archive |

## Traceability

| Capability | Implementation | Executable evidence |
| --- | --- | --- |
| `publication-pipeline` | `application/publication.py`, selection policies, CLI, local and S3 targets | unit selection tests; local integration; CLI e2e |
| `archive-index-layout` | `domain/publication.py`, `application/selection_common.py`, `entrypoints/config.py` | selection, local-target, and S3-target tests |
| `network-retry` | `infra/retry.py`, HTTP source, archive downloader, S3 target | retry, HTTP-source, archive-download, and S3-target tests |
| `archive-availability-fallback` | `domain/publication.py`, selection policies, publication use case | fallback selection and publication tests |

The retry specification explicitly records one verified current limitation:
MinIO listing errors raised while consuming its iterator are not retried. The
archived change artifact promised broader list retry, so it is historical rather
than normative.

Validate both active changes and baseline specifications with:

```bash
openspec validate --all --strict --json
```
