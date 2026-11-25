# Example 04 — Sealed 5-Phase `PCE` `EngineeringDossier` & Z3 Proof Digest

Demonstrates a complete, cryptographically sealed **`EngineeringDossier`** (`dossier.json`) binding:
1. **`EngineeringMandate`** (`MND-QUOTA-EXAMPLE-04`) mapping `INV-001` and `INV-002` to compiled Rust CST symbols (`allocate_quota`, `test_allocate_quota_clamps_to_cap`).
2. **`BehaviorGraph`** (`build_greenfield_contract_first_graph`) verified via Kildall dataflow analysis over the $F_8 = 21$ behavior basis.
3. **`InspectionReport`** (`APPROVED` across 30 static + 2 dynamic `JBC` Edition 1.3 provisions).
4. **`CertificateOfOccupancy`** binding both the permutation-invariant SHA-256 `artifact_digest` and the Z3 `smt_proof_digest` (`proved_deductive_count = 1`).

## Run It

```bash
# 1. Inspect & deductively prove the Rust crate
uv run jezz inspect examples/04-minimal-pce-dossier
uv run jezz prove examples/04-minimal-pce-dossier --require-deductive

# 2. Audit the sealed 5-phase EngineeringDossier -> OCCUPANCY_SEALED (exit 0)
uv run jezz audit examples/04-minimal-pce-dossier/dossier.json --repo examples/04-minimal-pce-dossier
```
