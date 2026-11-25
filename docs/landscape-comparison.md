# Ecosystem Landscape Comparison — Where `jezz` Fits (`2025–2026`)

Coding-agent governance, architectural linting, and formal verification currently fragment into five disconnected tool categories. This document maps `jezz` `v0.6.0` against primary open-source and commercial offerings across each category, with clickable repository, specification, and paper citations.

---

## 1. Five-Category Capability Matrix

| Capability / Guarantee | Prose SDD ([`spec-kit`](https://github.com/github/spec-kit), [`OpenSpec`](https://github.com/Fission-AI/OpenSpec), [`BMAD`](https://github.com/bmad-code-org/BMAD-METHOD)) | Contract-Aware SDD ([AWS Kiro](https://kiro.dev/), [Tessl](https://tessl.io/)) | Arch & AST Linters ([`Tach`](https://github.com/gauge-sh/tach), [`ArchUnit`](https://www.archunit.org/), [`Semgrep`](https://semgrep.dev/)) | Arch-as-Code ([`LikeC4`](https://likec4.dev/), [`Structurizr`](https://structurizr.com/)) | Deductive Vericoding ([`Dafny`](https://dafny.org/), [`Verus`](https://github.com/verus-lang/verus), [`Lean 4`](https://lean-lang.org/)) | **`jezz` `v0.6.0` (`PCE` + `JBC` v1.3)** |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **Target Languages** | Prompt-only (any) | IDE-coupled | Single-lang or pattern rules | Hand-authored DSL | Proof languages / Rust subset | **Native `.py`, `.ts`/`.tsx`, `.rs`, `.go`** |
| **Specification Medium** | Multi-hundred-line Markdown + `- [ ]` | EARS prose + generated PBT | Config TOML / YAML rules | C4 DSL files (drift-prone) | Full theorem-prover annotations | **Typed `EngineeringMandate` + compiled DbC + PBT** |
| **Statutory Building Code (`32` Rules)** | None (`constitution.md` prompt begging) | None | Partial (imports or security rules only) | None | Memory/functional specs only | **`JBC` v1.3 (`DBC`, `FLT`, `SEC`, `COG`, `OBS`, `VRF`)** |
| **Deductive SMT Contract Proofs** | No | No (randomized PBT) | No | No | **Yes (full inductive proofs)** | **Yes (bounded Z3 `QF_LIA`/`NRA`/`SLIA` VCGen)** |
| **Zero-Drift AST $\to$ CAD + `LVS` Gate** | No | No | Import DAGs only (`Tach`) | Manual DSL (no AST `LVS` gate) | No | **Switchable Sheet `A-1`/`E-1` + `verify_blueprint_lvs`** |
| **Verified Agent Control-Flow EFSM** | Slash-command prompts | Proprietary IDE agent | None (linter only) | None (renderer only) | Ad-hoc repair loops | **$F_8 = 21$ basis + Kildall dataflow fixed point** |
| **Expiring Waivers & Tamper-Evident Seal** | `# noqa` / unverified `[x]` | None | Inline ignore comments | None | Proof kernel check | **`CodePermit` sunsets + SHA-256 & Z3 `CertificateOfOccupancy`** |

---

## 2. Category-by-Category Technical Analysis

### 2.1 Prose "Spec-Driven Development" (`spec-kit`, `OpenSpec`, `BMAD`)

- **Representative Projects:** GitHub [`github/spec-kit`](https://github.com/github/spec-kit) ([`a55782f1f1`](https://github.com/github/spec-kit/tree/a55782f1f1); [Delimarsky, 2025](https://github.blog/ai-and-ml/generative-ai/spec-driven-development-with-ai-get-started-with-a-new-open-source-toolkit/)), Fission [`Fission-AI/OpenSpec`](https://github.com/Fission-AI/OpenSpec), and [`bmad-code-org/BMAD-METHOD`](https://github.com/bmad-code-org/BMAD-METHOD) (surveyed by [Böckeler, 2025](https://martinfowler.com/articles/exploring-gen-ai/sdd-3-tools.html)).
- **Shared Mechanism:** Interpose 3–4 LLM-authored Markdown artifacts (`constitution.md` $\to$ `spec.md` $\to$ `plan.md`/`design.md` $\to$ `tasks.md`) ahead of code synthesis, tracking completion via `- [x]` checkboxes.
- **Empirical Failure Mode (`Tracks 4, 10, & 21`):** Because prose specifications have no compiled AST/symbol binding to the code, multi-hop English expansion introduces **Specification Laundering**—hallucinating domain semantics (e.g., Hamilton largest-remainder rounding in `Track 21` Rust `case_rs_01`) and rubber-stamping them with self-authored unit tests (`100%` `[x]` pass, `8.3%` `JBC` occupancy, `33.3%` held-out oracle failure). Upgrading the prose Architect to a larger model (`Gemini 4 Hydrogen` or `Claude Opus 5.5`) *increases* downstream `JBC` violations (`+34.3%` to `+100%`).

### 2.2 Contract-Aware & Spec-As-Source Platforms (`AWS Kiro`, `Tessl`)

- **Representative Projects:** [AWS Kiro](https://kiro.dev/) ([`kirodotdev/Kiro`](https://github.com/kirodotdev/Kiro), grounded in EARS syntax from [Mavin et al., 2009](https://doi.org/10.1109/RE.2009.9)) and [Tessl](https://tessl.io/).
- **Strengths:** Kiro translates EARS clauses into property-based tests (`Hypothesis` / `fast-check`), substantially improving upon pure prose SDD by testing universal input properties rather than 2–3 happy-path examples.
- **Remaining Gaps Closed by `jezz`:** Neither Kiro nor Tessl enforces a deterministic multi-language **Software Building Code** (`JBC` Articles `FLT`/`SEC`/`COG`/`OBS`), subpackage quotient acyclicity (`JBC-COG-006`), AST-extracted `LVS` architectural schematics, or Z3 deductive postcondition proofs—and both couple teams to proprietary cloud/IDE runtimes rather than an open Apache-2.0 CLI/MCP/GitHub Action (`action.yml`).

### 2.3 Architectural Fitness & Static Rule Engines (`Tach`, `ArchUnit`, `dependency-cruiser`, `Semgrep`)

- **Representative Projects:** [`gauge-sh/tach`](https://github.com/gauge-sh/tach) and [`seddonym/import-linter`](https://github.com/seddonym/import-linter) (Python), [`ArchUnit`](https://www.archunit.org/) (Java/Kotlin), [`sverweij/dependency-cruiser`](https://github.com/sverweij/dependency-cruiser) (TS/JS), and [`semgrep/semgrep`](https://github.com/semgrep/semgrep).
- **Strengths:** Fast, deterministic enforcement of module import boundaries (`Tach`, `dependency-cruiser`) or pattern-matched security sinks (`Semgrep`).
- **Remaining Gaps Closed by `jezz`:** Single-ecosystem boundary linters do not enforce cross-language equivalence across `.py`/`.ts`/`.rs`/`.go`, Hitz–Montazeri `LCOM4` class cohesion (`JBC-COG-005`), compartment portal-ratio ceilings (`JBC-COG-006`), whole-workspace inter-procedural taint/validator call-flow (`call_flow.py`), Z3 symbolic contract proofs (`jezz prove`), or closed-loop `RED_TAG -> REVERT` agent orchestration.

### 2.4 Architecture-as-Code & C4 Modeling (`LikeC4`, `Structurizr`)

- **Representative Projects:** [`likec4/likec4`](https://github.com/likec4/likec4) ([`likec4.dev`](https://likec4.dev/)) and [`structurizr/dsl`](https://github.com/structurizr/dsl) ([`structurizr.com`](https://structurizr.com/)).
- **Strengths:** Expressive interactive architecture diagrams authored in declarative DSLs.
- **Remaining Gaps Closed by `jezz`:** Hand-authored C4 DSLs suffer **model-code drift** the moment an agent adds an undocumented import. `jezz blueprint` extracts `BlueprintGraph` **deterministically from live Python/TS/Rust/Go AST/CSTs**, renders switchable **Sheet `A-1` (Floor Plan) $\leftrightarrow$ Sheet `E-1` (`elkjs` Manhattan Wiring Schematic)** CAD drawings, and enforces **Layout-vs-Schematic (`LVS`)** equivalence in CI (`--verify-target blueprint.json`).

### 2.5 Deductive Vericoding & Proof-Oriented Languages (`Dafny`, `Verus`, `Lean 4`, `CrossHair`)

- **Representative Projects:** [`dafny-lang/dafny`](https://github.com/dafny-lang/dafny) ([Leino, 2010](https://doi.org/10.1007/978-3-642-17511-4_20)), [`verus-lang/verus`](https://github.com/verus-lang/verus) ([Lattuada et al., 2023](https://doi.org/10.1145/3586037)), [`leanprover/lean4`](https://github.com/leanprover/lean4) ([Moura & Ullrich, 2021](https://doi.org/10.1007/978-3-030-79876-5_37)), [`pschanely/CrossHair`](https://github.com/pschanely/CrossHair), and [`Z3Prover/z3`](https://github.com/Z3Prover/z3) ([de Moura & Bjørner, 2008](https://doi.org/10.1007/978-3-540-78800-3_24)).
- **Trade-Off & `jezz`'s Neurosymbolic Sweet Spot:** Full deductive vericoding (`Dafny`, `Verus`, `Lean 4`) provides the gold standard of mathematical proof over inductive loop invariants and quantified heap properties, but requires rewriting production software in specialized proof languages (or heavy ghost-code annotations) where LLMs incur steep proof-repair failure rates. `jezz.codes.smt` (`Track 27`) targets the **zero-annotation / idiomatic-contract sweet spot**: lowering standard production Python (`@icontract`, Pydantic `Field`), TypeScript (`Zod`, `@requires`/`@ensures`), Rust (`Result`, `assert!`), and Go (`(T, error)` guards) directly into bounded Z3 `QF_LIA`/`QF_NRA`/`QF_SLIA` verification conditions (`<= 500ms`), proving decidable contracts deductively (`PROVED_DEDUCTIVE`), catching vacuous preconditions (`VACUOUS_CONTRACT`) and concrete counterexample witnesses (`COUNTEREXAMPLE_FOUND`), and explicitly classifying opaque I/O bodies as `BOUNDED_BY_PBT` rather than claiming false certainty.
