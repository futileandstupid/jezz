# Getting Started with `jezz` (`v0.6.1`)

`jezz` can be adopted incrementally across **Python (`.py`)**, **TypeScript (`.ts`/`.tsx`)**, **Rust (`.rs`)**, and **Go (`.go`)**—starting as a zero-config static/symbolic inspector in 30 seconds and scaling up to full 5-phase **Permit-and-Contract Engineering (`PCE`)**.

---

## Rung 1 — Zero-Config Inspection, Z3 Proofs, & CAD Blueprints (`30s`)

Run `jezz` directly against any repository via [`uvx`](https://docs.astral.sh/uv/) (requires Python `>=3.14`):

```bash
# 1. Inspect any Python, TypeScript, Rust, or Go codebase against 30 static JBC v1.3 provisions
uvx --from git+https://github.com/futileandstupid/jezz@v0.6.1 jezz inspect .

# 2. Differential Occupancy Ratchet: gate only files modified on your PR/branch
uvx --from git+https://github.com/futileandstupid/jezz@v0.6.1 jezz inspect . --changed-since origin/main

# 3. Discharge @icontract / Pydantic / Zod / Rust / Go contracts deductively via Z3 SMT
uvx --from git+https://github.com/futileandstupid/jezz@v0.6.1 jezz prove .

# 4. Render interactive Sheet A-1 (Floor Plan) <-> Sheet E-1 (Wiring Schematic) CAD HTML
uvx --from git+https://github.com/futileandstupid/jezz@v0.6.1 jezz blueprint . --write-html blueprint.html
```

---

## Rung 2 — Scaffold a Turnkey Starter Project (`Python`, `TypeScript`, `Rust`, `Go`)

Bootstrap a clean 2-room (`domain` + `gateway`) project pre-wired with closed wire schemas, a Z3-discharged postcondition, property-based tests, a sealed `blueprint.json` `LVS` netlist, and `.github/workflows/jbc-gate.yml`:

```bash
# Materialize Python, TypeScript, Rust, or Go starter locally
uvx --from git+https://github.com/futileandstupid/jezz@v0.6.1 jezz init my-service --scaffold typescript
cd my-service
npm install && npm run typecheck && npm test
```

Or click **Use this template** on GitHub:
- **Python (`3.14+`):** [`futileandstupid/jezz-template-python`](https://github.com/futileandstupid/jezz-template-python)
- **TypeScript (`strict` + `Zod`):** [`futileandstupid/jezz-template-typescript`](https://github.com/futileandstupid/jezz-template-typescript)
- **Rust (`Serde` + `Result`):** [`futileandstupid/jezz-template-rust`](https://github.com/futileandstupid/jezz-template-rust)
- **Go (`1.23+` + `quick.Check`):** [`futileandstupid/jezz-template-go`](https://github.com/futileandstupid/jezz-template-go)
- **Live PR Showcase:** [`futileandstupid/jezz-showcase`](https://github.com/futileandstupid/jezz-showcase)

---

## Rung 3 — Add the CI Gate (`action.yml`) & IDE MCP Server (`jezz mcp`)

### GitHub Actions Gate (`.github/workflows/jbc-gate.yml`)

```yaml
- name: Enforce Jezz JBC v1.3, Blueprint LVS, and Z3 Contract Proof Gate
  uses: futileandstupid/jezz@v0.6.1
  with:
    mode: ${{ github.event_name == 'pull_request' && 'both' || 'full' }}
    profile: "standard"             # standard | strict_internal
    blueprint-target: "blueprint.json"
    prove: "require-deductive"      # '' | check | require-deductive
```

### Non-Python Configuration (`jezz.toml`)

TypeScript, Rust, and Go repositories configure `jezz` natively via a root `jezz.toml` (no `pyproject.toml` needed):

```toml
[models]
default = "google:gemini-3.8-flash"
prompt_cache = "auto"

[gates]
timeout_sec = 120.0
```

### Interactive Coding-Agent Guardrails (Antigravity, Claude Code, Cursor, JetBrains)

Install the read-only `stdio` MCP server (`14` tools including pre-write `jbc_preview_source` and `jbc_prove_contracts`) plus optional Git pre-commit / Stop lifecycle ratchets:

```bash
uvx --from git+https://github.com/futileandstupid/jezz@v0.6.1 jezz init . --pre-commit
```

---

## Rung 4 — Full 5-Phase `PCE` Charter, Synthesis, & Surgical Brownfield Renovation

```bash
# 1. Phase 0/I: Reality-probed 3-axis intake -> sealed ProjectCharter + phased permit queue
uv run jezz charter . \
  --title "Settlement Clearing Ledger" \
  --vision "Deterministic multi-party integer-cent clearing with SQLite persistence." \
  --interactive \
  --write-charter charter.json \
  --write-mandates-dir permits/

# 2. Execute greenfield synthesis or --plan-only dry run
uv run jezz run . --mandate permits/01_MND-CHARTER-01.json --progress

# 3. Renovate a scoped slice of a legacy codebase without rewriting untouched legacy modules
uv run jezz renovate . --ratchet --prompt "Eliminate JBC violations in src/payments/wire.ts" --write-dossier dossier.json

# 4. Independently verify the dual-digest SHA-256 + Z3 CertificateOfOccupancy seal
uv run jezz audit dossier.json --repo .
```

Explore [`examples/`](https://github.com/futileandstupid/jezz/tree/main/examples) next for hands-on walkthroughs of `RED_TAG` remediation, `BlueprintGraph` `LVS` faults, expiring `CodeVariance` waivers, and tamper-evident `EngineeringDossier` seals.
