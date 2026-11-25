# Example 02 — Architectural Blueprint & Layout-vs-Schematic (`LVS`) Gate

Demonstrates coarse-grained `BlueprintGraph` extraction, interactive **Sheet `A-1` Floor Plan / Sheet `E-1` Wiring Schematic** rendering, and `LVS` enforcement catching unauthorized cross-room wiring (`SHORT_CIRCUIT` / `UNAUTHORIZED_ROOM`).

## Run It

```bash
# 1. Verify reconciled/ against sealed target_blueprint.json -> exits 0 (LVS_CLEAN)
uv run jezz blueprint examples/02-blueprint-lvs/reconciled \
  --verify-target target_blueprint.json

# 2. Verify drifted/ (where ui/ opens an unauthorized conduit to storage/) -> exits 1 (LVS_FAILED)
uv run jezz blueprint examples/02-blueprint-lvs/drifted \
  --verify-target examples/02-blueprint-lvs/reconciled/target_blueprint.json

# 3. Render interactive browser CAD Sheet A-1 <-> Sheet E-1
uv run jezz blueprint examples/02-blueprint-lvs/reconciled --write-html blueprint.html
```
