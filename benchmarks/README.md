# `jezz` Empirical Reproducibility Guide (`$0` Static Audits & Budgeted Live A/B Suites)

Every empirical table reported in [`docs/whitepaper.md`](../docs/whitepaper.md) can be reproduced independently across three escalating cost tiers.

---

## Level 0 — Offline Telemetry, Self-Hosted `JBC` v1.3, & Z3 Proof Reproduction (`$0` / `< 15s`)

Requires zero API keys and zero network calls once dependencies are synced:

```bash
# 1. Recompute all published whitepaper occupancy, token, cache, and latency claims from raw tables
uv run python benchmarks/verify_published_claims.py

# 2. Verify jezz's own 142-unit repository against all 30 static JBC Edition 1.3 provisions
uv run jezz inspect .

# 3. Discharge jezz's own 198+ contract obligations deductively via Z3 SMT
uv run jezz prove . --require-deductive

# 4. Verify all four polyglot starter templates (Python, TypeScript, Rust, Go)
uv run pytest tests/test_track28_polyglot_templates.py tests/test_track29_repro_and_examples.py
```

---

## Level 1 — External Open-Source Brownfield Corpus Reproduction (`$0` LLM Cost / Git Network Only)

[`brownfield_corpus.lock.json`](brownfield_corpus.lock.json) pins exact Git repository URLs and commit SHAs for the external open-source repositories audited in Tracks 2, 8, and 11. [`reproduce_brownfield.py`](reproduce_brownfield.py) shallow-clones each pinned commit into an ephemeral `tempfile` directory (`JBC-SEC-006`) and executes `BuildingInspector` + `WorkspaceAstGraph`:

```bash
# Fast smoke tier (~8s): go-chi/chi @ c64ed7d663 + spf13/cobra @ adbc881390 (126 Go units)
uv run python benchmarks/reproduce_brownfield.py --tier smoke

# Core polyglot tier (~65s): 6 TypeScript, Rust, & Go repos (1,110 units / 300,067 LOC)
uv run python benchmarks/reproduce_brownfield.py --tier core

# Full tier (~120s): adds github/spec-kit @ a55782f1f1 (291,626 Python LOC) & AWS Kiro sample
uv run python benchmarks/reproduce_brownfield.py --tier full
```

Air-gapped checkouts can also be supplied without network access via `--local-checkout <id>=<dir>`.

---

## Level 2 — Live Controlled SDD-vs-`PCE` A/B Benchmark Suites (`jezz benchmark`)

Live head-to-head trials pit **Control Arm (`Prose SDD` — `OpenSpec` / Kiro `spec.md -> design.md -> tasks.md`)** against **Treatment Arm (`jezz` `PCE` — 5-phase contract-first EFSM + `JBC` gates)** on identical tasks and held-out adversarial property/fuzz oracles (`Hypothesis`, SplitMix64, `testing/quick`):

| Budget Tier | Languages / Cases | Builds (`SDD` + `PCE`) | Est. Gross Tokens | Est. API Cost (Sonnet-Class) | CLI Invocation |
| :--- | :--- | :---: | ---: | ---: | :--- |
| **`smoke`** (`1` lang) | Go (`2` cases) | `4` | `~0.9M` | `~$1.10` (`$0` via `antigravity:*`) | `uv run jezz benchmark /tmp/jezz-bench --language go --model anthropic:claude-sonnet-4-5` |
| **`core`** (`3` polyglot) | TypeScript + Rust + Go (`6` cases) | `12` | `~1.8M` | `~$3.40` (`$0` via `antigravity:*`) | `uv run jezz benchmark /tmp/jezz-bench --language typescript --model antigravity:claude-sonnet-5-5-medium` (repeat for `rust`, `go`) |
| **`full`** (`4` langs) | Python + TS + Rust + Go (`12` cases) | `24` | `~5.2M` | `~$9.80` | `uv run jezz benchmark /tmp/jezz-bench --language all --model google:gemini-3.8-flash` |

---

## Containerized One-Command Reproduction (`Docker` / `Podman`)

[`Containerfile`](Containerfile) packages Python 3.14, Node 22, Rust stable, Go 1.23, and locked `uv` / `npm` dependencies:

```bash
docker build -f benchmarks/Containerfile -t jezz-repro:0.6.0 .
docker run --rm jezz-repro:0.6.0
```
