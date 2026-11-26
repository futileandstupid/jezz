# `jezz` — Licensure-Grade Software Building Codes, Symbolic Contract Proofs, & Coding-Agent Runtime

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![JBC Edition](https://img.shields.io/badge/JBC-Edition%201.3%20(32%20Codes)-0ea5e9.svg)](docs/jbc-codebook-reference.md)
[![Languages](https://img.shields.io/badge/Languages-Python%20%7C%20TypeScript%20%7C%20Rust%20%7C%20Go-10b981.svg)](docs/getting-started.md)

`jezz` (`v0.6.1`) replaces unverified prose "Spec-Driven Development" (`spec.md` / `- [x]` checklists) with **Permit-and-Contract Engineering (`PCE`)**: **32 machine-checkable Software Building Codes (`JBC` Edition 1.3)**, **bounded AST/CST $\to$ Z3 deductive contract proofs**, **switchable Sheet `A-1`/`E-1` architectural CAD + Layout-vs-Schematic (`LVS`) gates**, and a **Kildall-verified $F_8 = 21$ coding-agent runtime** across **Python, TypeScript, Rust, and Go**.

**Start Here:** [**Getting Started (`5 min`)**](docs/getting-started.md) · [**JBC v1.3 Codebook (`32` Rules)**](docs/jbc-codebook-reference.md) · [**Landscape Comparison**](docs/landscape-comparison.md) · [**Reproducibility (`$0` Audits)**](benchmarks/README.md) · [**Examples Gallery**](examples/README.md) · [**Technical White Paper**](docs/whitepaper.md)

---

## 60-Second Quickstart

```bash
# 1. Inspect any Python, TypeScript, Rust, or Go repo (exit 0 = APPROVED, 1 = RED_TAG)
uvx --from git+https://github.com/futileandstupid/jezz@v0.6.1 jezz inspect .

# 2. Discharge @icontract / Pydantic / Zod / Rust / Go contracts deductively via Z3 SMT
uvx --from git+https://github.com/futileandstupid/jezz@v0.6.1 jezz prove . --require-deductive

# 3. Extract interactive Sheet A-1 (Floor Plan) <-> Sheet E-1 (Wiring Schematic) CAD HTML
uvx --from git+https://github.com/futileandstupid/jezz@v0.6.1 jezz blueprint . --write-html blueprint.html

# 4. Scaffold a turnkey CI-verified 2-room project (python | typescript | rust | go)
uvx --from git+https://github.com/futileandstupid/jezz@v0.6.1 jezz init my-service --scaffold rust
```

### 1-Click GitHub Starter Templates & Live PR Showcase

| Ecosystem | Template Repository | Live Showcase |
| :--- | :--- | :--- |
| **Python (`3.14+`)** | [`futileandstupid/jezz-template-python`](https://github.com/futileandstupid/jezz-template-python) | [`futileandstupid/jezz-showcase`](https://github.com/futileandstupid/jezz-showcase) — Live PR #1 blocked on `RED_TAG` + `LVS` `SHORT_CIRCUIT` vs. PR #2 `APPROVED` + `OCCUPANCY_SEALED` |
| **TypeScript (`strict` + `Zod`)** | [`futileandstupid/jezz-template-typescript`](https://github.com/futileandstupid/jezz-template-typescript) | Configured via native [`jezz.toml`](docs/cli-and-mcp-reference.md#3-workspace-configuration-jezztoml--pyprojecttoml) |
| **Rust (`Serde` + `Result`)** | [`futileandstupid/jezz-template-rust`](https://github.com/futileandstupid/jezz-template-rust) | Configured via native [`jezz.toml`](docs/cli-and-mcp-reference.md#3-workspace-configuration-jezztoml--pyprojecttoml) |
| **Go (`1.23+` + `quick.Check`)** | [`futileandstupid/jezz-template-go`](https://github.com/futileandstupid/jezz-template-go) | Configured via native [`jezz.toml`](docs/cli-and-mcp-reference.md#3-workspace-configuration-jezztoml--pyprojecttoml) |

### Reusable GitHub Action (`action.yml`)

```yaml
- name: Enforce JBC v1.3, Blueprint LVS, and Z3 Contract Proof Gate
  uses: futileandstupid/jezz@v0.6.1
  with:
    mode: ${{ github.event_name == 'pull_request' && 'both' || 'full' }}
    blueprint-target: "blueprint.json"
    prove: "require-deductive"
```

---

## Headline Empirical Results ([Reproduce Independently](benchmarks/README.md))

Across **`89` controlled A/B workspace builds** (`Tracks 4, 7, 10, & 21`) and **`7.71M` LOC of brownfield OSS audits** (`Tracks 2, 8, & 11`):

| Metric (`Track 21` Matched Polyglot A/B: `TS` + `Rust` + `Go`, `N = 24`) | Control: Prose SDD (`spec.md` / `[x]`) | Treatment: `jezz` `PCE` (`JBC` + Contracts) |
| :--- | :---: | :---: |
| **Markdown `- [x]` Task Validation** | `12 / 12` (`100.0%`) | N/A (compiled schemas & contracts) |
| **`JBC` Occupancy (`0` Unwaived Violations)** | `1 / 12` (`8.3%`, `23` violations) | **`12 / 12` (`100.0%`, `0` violations)** |
| **Held-Out Property / Fuzz Oracle Survival** | `8 / 12` (`33.3%` silent defect escape) | **`12 / 12` (`100.0%`, `0.0%` escape)** |
| **Human-Reviewed Specification Volume** | `521.2` lines/case (`6,254` total) | **`39.2` lines/case (`13.3x` smaller)** |
| **Effective Billed Input / Total Output Tokens** | `1,080,208` in / `506,675` out | **`1,035,218` in (`-4.2%`) / `219,268` out (`-56.7%`)** |
| **Mean End-to-End Wall-Clock Latency** | `216.87s` | **`84.93s` (`-60.8%`, faster in `6/6` pairs)** |

Verify every published ratio in `< 2s` via `uv run python benchmarks/verify_published_claims.py`.

---

## Architecture — The Four Pillars

| Pillar | Modules | Specification & Guarantees |
| :--- | :--- | :--- |
| **1. Agent Behavior Basis** | `jezz.behaviors` | [**Pillar 1 Spec**](docs/pillar-1-behavior-basis.md) — Exhaustive $F_8 = 21$ atomic behavior basis (`BHV-01`..`BHV-21`), adjacency legality, and Kildall monotone dataflow fixed-point verification. |
| **2. Software Building Codes, SMT, & Blueprint `LVS`** | `jezz.codes`, `jezz.codes.smt`, `@jezz/blueprint` | [**Pillar 2 Spec**](docs/pillar-2-building-codes.md) · [**Codebook Reference**](docs/jbc-codebook-reference.md) — 32 `JBC` v1.3 provisions (`DBC`, `FLT`, `SEC`, `COG`, `OBS`, `VRF`), polyglot Z3 VCGen (`jezz prove`), and switchable `A-1`/`E-1` CAD + `LVS` (`jezz blueprint`). |
| **3. Permit-and-Contract Engineering (`PCE`)** | `jezz.methodology` | [**Pillar 3A SDD Teardown**](docs/pillar-3a-sdd-teardown.md) · [**Pillar 3B PCE Spec**](docs/pillar-3b-pce-methodology.md) — Reality-probed `ProjectCharter` intake (`jezz charter`), `LotLineAssessment` shear gates, and dual-digest SHA-256 + Z3 `CertificateOfOccupancy` seals (`jezz audit`). |
| **4. Pydantic AI Runtime & Statutory MCP Server** | `jezz.agents`, `jezz.mcp_server` | [**Pillar 4 Spec**](docs/pillar-4-pydantic-ai-runtime.md) · [**CLI & MCP Reference**](docs/cli-and-mcp-reference.md) — Typed `pydantic-graph` EFSM across `.py`/`.ts`/`.rs`/`.go`, scheme-routed secrets (`env://`, `pass://`, `op://`, `keychain://`), and 14-tool hot-reloading `stdio` MCP server. |

---

## Documentation & Empirical Reports

- **Guides & Reference:** [Getting Started](docs/getting-started.md) · [CLI, MCP, `jezz.toml`, & Action Reference](docs/cli-and-mcp-reference.md) · [JBC v1.3 Codebook Manual](docs/jbc-codebook-reference.md) · [Ecosystem Landscape Comparison](docs/landscape-comparison.md) · [Interactive CAD Blueprint](docs/interactive-blueprint.md) · [Primary-Source Bibliography](docs/references.md)
- **Empirical Field Trials & Benchmarks:** [White Paper](docs/whitepaper.md) · [Reproducibility Suite](benchmarks/README.md) · [Track 2 (`7.41M` Python LOC)](docs/track-2-brownfield-field-trial.md) · [Track 4 (Python A/B Waves 4A–4E)](docs/track-4-sdd-vs-pce-benchmark.md) · [Track 5 (Longitudinal Crucible)](docs/track-5-multi-mandate-crucible.md) · [Track 7 (Prompt Caching)](docs/track-7-prompt-caching-and-providers.md) · [Track 8 (`300K` Polyglot LOC)](docs/track-8-polyglot-inspector.md) · [Track 10 (Claude & GPT-OSS)](docs/track-10-cross-provider-benchmark.md) · [Track 11 (OSS `--ratchet` Renovations)](docs/track-11-brownfield-renovation-crucible.md) · [Track 21 (24-Build Polyglot A/B)](docs/track-21-polyglot-live-benchmark.md) · [Full Roadmap (Tracks 1–26)](docs/empirical-testing-roadmap.md)

## Development & Self-Hosted Verification Gates

```bash
uv run pytest --cov=jezz --cov-fail-under=92
uv run jezz inspect .
uv run jezz prove . --require-deductive
uv run mypy .
uv run ruff check . && uv run ruff format --check .
npm --prefix packages/jezz-blueprint run typecheck && npm --prefix packages/jezz-blueprint test
```

## License

Licensed under the [Apache License, Version 2.0](LICENSE).
