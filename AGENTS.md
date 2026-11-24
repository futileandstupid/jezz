# jezz — Project Instructions

## Overview & Core Pillars
`jezz` is an experimental software engineering and coding-agent framework structured around four pillars:

1. **Complete Agent Behavior Basis:** An exhaustive taxonomy of atomic behaviors employed by coding agents.
   - **Completeness invariant:** Every coding-agent task must be representable as a directed graph over all or a subset of these behaviors.
   - **Cardinality constraint:** The total number of atomic behaviors must be a **Fibonacci number** (e.g., 8, 13, 21, 34, 55).
2. **Enforceable Software Building Codes:** A codified system of software design and verification standards modeled after civil/structural building codes (licensure-grade rigor, explicit code citations, and mandatory enforcement rather than QA theatre).
   - Anchor in established standards where sound (e.g., ISO/IEC 25010, IEEE 1016/29148, Design by Contract, formal/static verification standards, CWE/CERT/NASA Power of 10) and define net-new codes where existing standards fail or omit non-human/LLM failure modes.
   - Codes must have canonical identifiers, objective pass/fail criteria, and machine-checkable or formally auditable enforcement.
3. **Formalized Engineering Methodology (Anti-SDD / Anti-Agile):** A rigorous development and verification process built atop Pillars 1 and 2.
   - **Naming & framing constraint:** **Never** label or market `jezz`'s methodology as "Spec-Driven Development" ("SDD") or frame it around Agile ceremonies.
   - Include a rigorous critical analysis of industry "SDD" offerings (specifically GitHub `spec-kit`'s *"Code was king / Power Inversion"* thesis, Fission `OpenSpec`, and AWS Kiro), exposing their omission of prior formal engineering foundations (Design by Contract, formal methods, MDD, BDD/TDD) and reliance on unverified prose specs.
4. **Pydantic AI Implementation:** Full runtime implementation of the agent architecture, behavior graphs, and code-enforcement gates using `pydantic-ai` (and `pydantic-graph` / `pydantic` models where typed state/node graphs apply).

## Technical Stack & Conventions
- **Runtime:** Python `>=3.14` (`.python-version`).
- **Package & Environment Management:** Use `uv` exclusively (`uv add`, `uv sync`, `uv run`). Never invoke `pip` or mutate `.venv` directly.
- **Core Agent Framework:** `pydantic-ai` (`pydantic-graph`, `pydantic-evals`) and `pydantic` (strict runtime validation and static typing across all schemas, behavior nodes, and building-code checks).
- **Design Discipline:** Prefer explicit data models, invariants, preconditions/postconditions (`icontract`), property-based verification (`hypothesis`), and deterministic static checks over untyped dicts or prompt-only guardrails.
- **Dynamic Path Anchoring (`JBC-SEC-006`):** Never hardcode filesystem paths (nor pass string literals directly to `Path(...)` / `os.path.*(...)` in production code, nor bake host paths into generated configs/hooks). Every path must be calculated dynamically from known file, package, or injected root locations (`Path(__file__).resolve()`, `importlib.resources`, `tmp_path` / `tempfile`, `${PLUGIN_ROOT}` / `dirname -- "$0"`, or `ContainedPath.from_parts(anchor, rel)`).

## Verification Commands
- **Tests, Property-Based Suites, & Coverage Gate (`>=92%`):** `uv run pytest --cov=jezz --cov-fail-under=92`
- **Whole-Workspace `JBC` v1.2 Inspection:** `uv run jezz inspect .`
- **Static Type Checking:** `uv run mypy .`
- **Lint & Format Check:** `uv run ruff check .` and `uv run ruff format --check .`
