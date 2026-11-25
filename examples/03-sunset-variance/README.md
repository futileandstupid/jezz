# Example 03 — Time-Bounded `CodeVariance` Sunset Enforcement

Demonstrates how **JBC Edition 1.3** replaces permanent `# noqa` / `//nolint` comment suppressions with auditable, expiring `CodeVariance` waivers inside a `CodePermit` (`LIFE_SAFETY` codes remain strictly non-waivable).

`adapter.go` exposes a 7-parameter legacy C-ABI adapter (`JBC-COG-002` `STRUCTURAL` limit `<= 6`) covered by `VAR-LEGACY-ORDER-01` expiring on `2026-06-30`.

## Run It

```bash
# 1. Before sunset (2026-06-15) -> exits 0 (APPROVED_WITH_VARIANCES)
uv run jezz inspect examples/03-sunset-variance --permit examples/03-sunset-variance/permit.json --as-of 2026-06-15

# 2. Past sunset (2026-07-01) -> exits 1 (RED_TAG: expired variance reverts to violation)
uv run jezz inspect examples/03-sunset-variance --permit examples/03-sunset-variance/permit.json --as-of 2026-07-01
```
