# Contributing to `jezz`

Contributions to `jezz` are governed under **Permit-and-Contract Engineering (`PCE`)** and self-hosted enforcement of the **Jezz Software Building Code (`JBC` Edition 1.3)**.

## 1. Engineering Methodology & Framing Rules

- **Invariants Over Unverified Prose:** Frame changes around falsifiable `DomainInvariant`s mapped to concrete AST/CST verification symbols and pairwise-disjoint `ModuleWriteScope`s—never Agile user stories or unverified prose task lists.
- **Verification Bifurcation:**
  - **Defect Repair (`build_abductive_bugfix_graph`):** Author a deterministic reproduction test first and confirm failure against unmodified `main` prior to implementing the fix.
  - **Greenfield & Renovation (`build_greenfield_contract_first_graph`):** Define closed schemas (`extra="forbid"` / `.strict()`), runtime boundary guards (`@jezz_callable` / `@icontract`), and property-based `Hypothesis` suites upfront.
- **Standing Structural Invariants:**
  - Every production Python module under `src/jezz/**/*.py` must remain `<= 1,000` physical lines (`tests/test_track18_module_decomposition.py`).
  - Respect `JBC-COG-006` subpackage quotient acyclicity and compartment/package floorplate caps (`<= 35` modules per compartment, `<= 100` modules per package under `standard`).
  - Never hardcode host filesystem paths (`JBC-SEC-006`); derive all paths dynamically from `Path(__file__).resolve()`, `importlib.resources`, `tempfile` / `tmp_path`, or `ContainedPath.from_parts(anchor, rel)`.
  - If modifying `src/jezz/codes/codebook.py` or `codebook_articles.py`, regenerate `docs/jbc-codebook-reference.md` via `uv run python benchmarks/generate_codebook_docs.py`.

## 2. Local Environment Setup

Requires Python `>= 3.14`, [`uv`](https://docs.astral.sh/uv/), and Node.js `>= 22` (for `@jezz/blueprint`):

```bash
uv sync --locked --all-groups
uv run jezz init . --pre-commit
npm --prefix packages/jezz-blueprint ci
```

## 3. Mandatory Verification Gates Before Opening a Pull Request

Every pull request must pass all statutory, symbolic, static, and dynamic gates locally:

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy .
uv run jezz inspect .
uv run jezz prove . --require-deductive
uv run pytest --cov=jezz --cov-fail-under=92
npm --prefix packages/jezz-blueprint run typecheck && npm --prefix packages/jezz-blueprint test
```
