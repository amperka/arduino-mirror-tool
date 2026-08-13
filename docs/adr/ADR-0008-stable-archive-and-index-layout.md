# ADR-0008: Use stable archive namespaces and mirror source index paths

- **Status:** Accepted
- **Date:** 2026-08-13
- **Supersedes:**
- **Superseded by:**

## Context

ADR-0005 assigned archive storage paths directly from the package and library
family names and placed both index files at target root. Library Manager archive
URLs already contain `/libraries/`, so prefixing them creates
`libraries/libraries/...`; Boards Manager archive paths do not uniformly contain
`/packages/`, so stripping family-named source segments would be unreliable.
Root index placement also differs from the source URLs clients use.

The viable alternatives were: preserve family-named archive prefixes and
normalize known source segments; use fixed short archive namespaces while
preserving the full origin-relative path; or derive archive namespace from
source paths. For index objects, the alternatives were fixed root names, fixed
family paths, or the configured source index path.

## Decision

The `packages` and `libraries` command identities remain unchanged. Their
archive logical namespaces are fixed to `p/` and `l/`, respectively, followed by
the complete origin-host-relative path. The same rule applies to transformed
indexes: `p/packages/package_index.json` and `l/libraries/library_index.json`.
An optional target prefix applies outside these logical keys. Archive
reconciliation excludes that family index object.

## Alternatives Considered

### Remove source segments named `packages` or `libraries`

Rejected. Boards Manager archive URLs have no guaranteed `/packages/` segment,
and rules based on path spelling couple the storage layout to upstream URL
conventions.

### Derive an archive namespace from the origin path

Rejected. Different archive types and upstream releases can have different path
roots, so this does not provide a stable namespace for reconciliation and
cleanup.

### Preserve only the index filename

Rejected. Removing configured source-path segments creates a special index rule
and breaks the one uniform mapping used for every mirrored object.

## Consequences

- **Positive:** Archive keys remain stable regardless of whether an upstream
  archive URL contains a family-named path segment.
- **Positive:** Each index shares a stable namespace with every archive it
  names.
- **Positive:** Local and S3-compatible targets use identical logical layout.
- **Negative / trade-offs:** Existing archive URLs and root index URLs become
  legacy paths; the publisher does not clean or migrate them.
- **Accepted risks:** A mirror host that does not expose the configured target
  prefix must be configured with the matching public base path.
