# Project Instructions — JBC Edition 1.3 Go Workspace

## Methodology & Architecture
- Govern engineering work via **Permit-and-Contract Engineering (`PCE`)** and **Jezz Software Building Code (`JBC` Edition 1.3)** configured in [`jezz.toml`](jezz.toml). Never frame deliverables around Agile user stories (`As a ..., I want ...`), "SDD", or Markdown task checklists (`- [ ]`).
- Maintain strict compartment isolation enforced by [`blueprint.json`](blueprint.json) Layout-vs-Schematic (`LVS`):
  - **`internal/domain/`** — Validated structs (`Validate() error`), sentinel errors, and Z3-provable `// @requires` / `// @ensures` contract comments paired with explicit range guards.
  - **`internal/gateway/`** — Bounded `json.Decoder` wire adapters with `DisallowUnknownFields()`, trailing-token rejection, and `fmt.Errorf("...: %w", err)` causal wrapping.
- Never hardcode host filesystem paths (`JBC-SEC-006`) or discard errors via `_ = ...` (`JBC-FLT-001`).

## Statutory Verification Commands
- **Go Vet & `testing/quick` Property Suite:**
  `go vet ./... && go test ./...`
- **JBC Edition 1.3 Static Inspection:**
  `uvx --from git+https://github.com/futileandstupid/jezz@v0.6.0 jezz inspect .`
- **Z3 Deductive Contract Proof Discharge:**
  `uvx --from git+https://github.com/futileandstupid/jezz@v0.6.0 jezz prove . --require-deductive`
- **Blueprint CAD & LVS Equivalence Gate (Refresh `blueprint.json` When Modifying Boundaries):**
  `uvx --from git+https://github.com/futileandstupid/jezz@v0.6.0 jezz blueprint . --verify-target blueprint.json`
