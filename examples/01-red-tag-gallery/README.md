# Example 01 — `RED_TAG` Hazard Gallery vs. Remediated Occupancy

Side-by-side comparison of common LLM-generated silent hazards (`broken/`) versus a remediated module (`remediated/`) passing all 30 static **JBC Edition 1.3** provisions and Z3 contract discharge.

## Run It

```bash
# 1. Inspect broken/ -> exits 1 (RED_TAG: JBC-SEC-001 SQL injection, JBC-FLT-001 silent pass, JBC-SEC-003)
uv run jezz inspect examples/01-red-tag-gallery/broken

# 2. Inspect remediated/ -> exits 0 (APPROVED, 0 violations)
uv run jezz inspect examples/01-red-tag-gallery/remediated

# 3. Prove remediated/ contracts via Z3 -> exits 0 (PROVED_DEDUCTIVE)
uv run jezz prove examples/01-red-tag-gallery/remediated --require-deductive
```
