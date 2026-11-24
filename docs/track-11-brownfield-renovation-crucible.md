# Track 11 — External Brownfield OSS Renovation Crucible (`jezz renovate --ratchet`)

## 1. Objective & Engineering Motivation

Tracks 2 and 8 audited `7.71M` LOC of external Python, TypeScript, Rust, and Go repositories in read-only inspection mode, while Tracks 5 and 9 validated `jezz renovate` on self-contained workspaces where the entire repository reached `0` violations. Real-world open-source adoption, however, rarely permits rewriting `700` legacy modules in one commit: brownfield onboarding requires **surgical, scope-isolated renovation** where touched files achieve full **Jezz Software Building Code (`JBC` Edition 1.2)** occupancy (or formally time-bounded `CodeVariance` waivers) while untouched legacy debt remains strictly isolated and whole-graph cyclic/encapsulation invariants (`JBC-COG-003`) are preserved.

Track 11 implements the **Differential Final Occupancy Ratchet (`jezz renovate --ratchet`)** and validates it end-to-end against three external open-source repositories across **Go (`go-chi/chi`), Rust (`serde-rs/json`), and Python (`github/spec-kit` — `291,626` LOC)**.

---

## 2. Architectural Extensions

1. **Differential Final Occupancy Gate (`inspect_differential_final`, `src/jezz/codes/ratchet.py`):**
   - Evaluates per-unit `JBC` v1.2 static rules strictly over `DifferentialScope.changed_paths`, runs whole-graph `JBC-COG-003` Tarjan SCC cycle and encapsulation analysis across the complete repository filtered to `changed_paths`, and appends dynamic compiler/suite attestations (`JBC-VRF-005` & `JBC-VRF-006`) to issue a true Final `InspectionReport` (`APPROVED`, `APPROVED_WITH_VARIANCES`, or `RED_TAG`) without false `RED_TAG` blockers from untouched legacy modules.
2. **Ratchet-Scoped `PceWorkflowOrchestrator` & `EngineeringDossier` Seal (`src/jezz/agents/nodes.py`, `src/jezz/methodology/dossier.py`, `src/jezz/cli.py`):**
   - Added `PceRuntimeBudget.ratchet_to_scope: bool = False`, `EngineeringDossier.ratchet_scoped: bool = False`, and CLI flag `jezz renovate --ratchet` (persisted automatically by `--write-dossier`).
   - When enabled, `InspectCodeNode` (`BHV-04`), `VerifyStaticNode` (`BHV-18`), `VerifyDynamicNode` (`BHV-19`), and `CertifyNode` (`BHV-21`) enforce differential occupancy over `mandate.authorized_files() | sandbox.modified_files`, while `CertificateOfOccupancy` hashes only the scoped slice (`audit_pce_dossier` independently recomputes and verifies the scoped SHA-256 digest against the full repository AST/CST symbol set).
   - `UvSubprocessGateRunner` scopes `mypy` under `--ratchet` via `uv run mypy --follow-imports=silent <authorized_py_files>` so upstream type debt in untouched sibling modules does not block surgical Python renovations.

---

## 3. Empirical Calibrations Surfaced on External Monorepos (`Calibrations 11A–11C`)

Executing `jezz renovate --ratchet` against `serde-rs/json` and `291,626`-LOC `github/spec-kit` surfaced three real-world monorepo calibrations, all locked in by permanent regression tests (`tests/test_track11_differential_renovation.py`):

| Calibration | Root Cause Surfaced | Architectural Fix |
| :--- | :--- | :--- |
| **`11A` — Monorepo Unit Ceilings & Safe `ValidationError` Formatting** | `github/spec-kit` contains `700` Python units, exceeding `Field(max_length=512)` on `find_type_alias_misuse` (`workspace.py`) and `compute_structural_health` (`verifier.py`). Formatting `str(exc)` on the resulting `ValidationError` attempted to serialize `SourceUnit.tree` (`ast.Module`). | Widened workspace unit/symbol ceilings to `100,000` / `200,000` (`workspace.py`, `ast_graph.py`, `verifier.py`) and added `_format_cli_exception` (`cli.py`) using `exc.errors(include_input=False, include_context=False)`. |
| **`11B` — Rust Inline-Module (`mod { use super::*; }`) Lexical Scope Resolution** | In `serde-rs/json`'s `src/lexical/math.rs`, inline submodules `mod small { use super::*; }` and `mod large { use super::*; }` resolved `super::*` against the file's `lexical.math` path rather than `lexical.math.small`, fabricating a false `lexical <-> ... <-> lexical.math` cycle. | Added `_enclosing_inline_mod_names`, `_effective_rs_module`, and multi-`super::` resolution in `src/jezz/codes/polyglot_rust.py`, resolving `use super::*;` inside `mod small` accurately to `lexical.math`. |
| **`11C` — Referentially Transparent Unit Memoization & Differential Rough-In Preview** | On `github/spec-kit` (`291,626` LOC, `32,419` baseline violations), `_tool_preview_rough_in` and `_parse_and_rough_in_check` re-parsed all `700` files and ran full-repo 29-rule `inspect_rough_in` (`>12s`), exceeding `FunctionToolset(timeout=10.0)`. | Added `@functools.lru_cache(maxsize=8192)` on immutable `_parse_cached_source_unit` (`discovery.py`), switched preview/validator cross-module checks to `inspect_differential_rough_in`, and raised toolset timeouts to `30.0s` — cutting warm preview latency on `spec-kit` to **`1.61s`** (`0.09s` warm unit load). |

---

## 4. Live External OSS Crucible Results (`3 / 3` Certified & `OCCUPANCY_SEALED`)

All three probes executed via `jezz renovate --ratchet` using symmetric tiered routing (`--architect-model google:gemini-4-hydrogen --builder-model google:gemini-3.8-flash`) with real ecosystem static/dynamic compilers (`go vet` + `go test ./...`, `cargo check --all-targets` + `cargo test`, `mypy` + `pytest`) followed by independent out-of-band `jezz inspect --changed-since HEAD` and `jezz audit dossier.json` verification:

| Probe | Repository & Commit | Language & Total Units | Baseline Repo $\to$ Post-Renovation Violations | Target Scope Violations (Before $\to$ After) | Ruling & Seal | Requests / Tools / Latency | Cache Hit Ratio (`cache_read / input`) |
| :--- | :--- | :--- | :---: | :---: | :---: | :---: | :---: |
| **`11A_go_chi`** | `go-chi/chi @ c64ed7d663` | Go (`90` units, `88` untouched) | `266` $\to$ **`261` (`-5`)** | `5` $\to$ **`0` unwaived** (`0` waived) | **`APPROVED` / `OCCUPANCY_SEALED`** (`76a9e711...`) | `15` reqs / `13` tools / `238.92s` | **`79.0%`** (`194,080 / 245,758`) |
| **`11B_serde_json`** | `serde-rs/json @ f51fa15a70` | Rust (`61` units, `58` untouched) | `269` $\to$ **`256` (`-13`)** | `18` $\to$ **`0` unwaived** (`5` waived via `CodePermit`) | **`APPROVED_WITH_VARIANCES` / `OCCUPANCY_SEALED`** (`d2e2da81...`) | `11` reqs / `9` tools / `201.70s` | **`79.8%`** (`218,165 / 273,369`) |
| **`11C_github_spec_kit`** | `github/spec-kit @ a55782f1f1` | Python (`700` units, `699` untouched) | `32,419` $\to$ **`32,403` (`-16`)** | `16` $\to$ **`0` unwaived** (`0` waived) | **`APPROVED` / `OCCUPANCY_SEALED`** (`20b92409...`) | `17` reqs / `15` tools / `312.56s` | **`76.9%`** (`201,284 / 261,896`) |

### Surgical Diff Highlights

1. **`go-chi/chi` (`middleware/basic_auth.go`, `middleware/strip.go` — `+25 / -0` lines):**
   - Remediated `5` `JBC-DBC-004`/`005` boundary hazards while preserving exact public signatures (`BasicAuth`, `StripSlashes`, `RedirectSlashes`, `StripPrefix`) and `100%` upstream `go test ./...` compatibility—including CRLF header-injection (`\r\n`), empty-realm, `maxBasicAuthRealmLen = 256`, and `maxBasicAuthCreds = 10000` guards in `BasicAuth`.
2. **`serde-rs/json` (`build.rs`, `src/lexical/bignum.rs`, `src/lexical/algorithm.rs` — `+37 / -36` lines):**
   - Upgraded `build.rs` from `fn main()` with `6` `LIFE_SAFETY` `.unwrap()` panics (`JBC-DBC-003`) to `fn main() -> Result<(), Box<dyn std::error::Error>>` propagating `OUT_DIR` / `CARGO_CFG_*` / `fs::write` errors via `.ok_or(...)?` and `?`; replaced `6` wildcard `use super::...::*;` imports (`JBC-COG-003`) with explicit item imports; flattened `fast_path`'s 4-branch `else if` ladder (`JBC-COG-004`) into early guard returns; and exercised `PERMIT-SERDE-LEXICAL-11B` (`VAR-SERDE-BUILDRS-STDOUT-01` for Cargo's mandatory `println!("cargo:...")` stdout protocol and `VAR-SERDE-NOSTD-IMPORT-01` for `no_std + alloc` `#[allow(unused_imports)]`) to seal `APPROVED_WITH_VARIANCES`.
3. **`github/spec-kit` (`src/specify_cli/workflows/expressions/operators.py` — `+41 / -21` lines):**
   - Renovated GitHub `spec-kit`'s own workflow expression operator module (`16` baseline violations) without adding `jezz` as a runtime dependency: eliminated all `12` `Any` type erasions (`JBC-DBC-002`) and all `4` `# type: ignore[operator]` suppressions (`JBC-VRF-004`) via explicit `object` typing, `isinstance(right, (Container, Iterable))` narrowing, and typed `_compare_numbers` / `_compare_strings` helpers, passing all `497` upstream expression unit tests and strict `mypy`.
