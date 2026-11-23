# Track 6 — Full $F_8 = 21$ Behavior Runtime Closure & Persistent Workspace Memory

## 1. Objective & Architectural Closure

Prior to Track 6, Pillar 1 (`src/jezz/behaviors/taxonomy.py`) defined the $F_8 = 21$ atomic coding-agent behaviors (`BHV-01`..`BHV-21`) and `BEHAVIOR_EXECUTION_BOARD` (`src/jezz/agents/specialists.py`) mapped all 21 to execution engines and toolsets, while the compiled `pydantic-graph` runtime (`src/jezz/agents/nodes.py`) enacted `17 / 21` `BehaviorKind`s across `18` node classes.

Track 6 closes the remaining `4` `BehaviorKind`s as first-class `AtomicBehaviorGraphNode` state-machine vertices, achieving **`21 / 21` (`100%`) runtime behavior basis coverage** (`22` reachable `AtomicBehaviorGraphNode` classes verified at runtime by `validate_runtime_node_coverage()`), symmetric `12`-slot (`4 ARCHITECT + 4 BUILDER + 4 INSPECTOR`) specialist model routing, rollback-safe dependency provisioning, and persistent cross-mandate workspace memory (`.jezz/memory.json`).

---

## 2. Newly Enacted Runtime Nodes & Subsystems

| Code | BehaviorKind | Runtime Node | Specialist Role (`SpecialistRoleSlot`) | Key Guarantees |
| :--- | :--- | :--- | :--- | :--- |
| `BHV-05` | `INSPECT_ENV` | `InspectEnvNode` | `env_inspector` (`INSPECT_ENV`, `INSPECTOR` tier) | Hydrates `.jezz/memory.json` (`WorkspaceMemoryLedger`), senses Python runtime & `ManifestDependencies`, and computes unprovisioned `required_dependencies` (zero-token fast path when `required_dependencies == ()`). |
| `BHV-06` | `CONSULT` | `ConsultNode` | `standards_consultant` (`CONSULT`, `INSPECTOR` tier) | Conditionally entered when `mandate.consultation_targets` is non-empty; resolves `JBC` v1.2 provisions (`_tool_consult_building_code`), confined workspace docs, and installed APIs into `WorldModelState.external_citations` and downstream prompt constraints. |
| `BHV-07` | `CURATE_MEMORY` | `CurateMemoryNode` | `memory_curator` (`CURATE_MEMORY`, `INSPECTOR` tier) | Enacted on `HYPOTHESIZE -> CURATE_MEMORY -> CHECKPOINT | INSTRUMENT` remediation cycles; compacts observations (`compact_world_model_deterministically` below `memory_compaction_threshold`, `build_curate_memory_agent` at/above threshold) while enforcing **zero dropped `invariant_id`s** and **zero evicted `falsified_paths` negations** (`validate_curated_digest_invariants`), and persists sealed mandates + negations to `.jezz/memory.json` at `CERTIFY`. |
| `BHV-13` | `PROVISION` | `ProvisionNode` | `dependency_provisioner` (`PROVISION`, `BUILDER` tier) | Enacted strictly after `CheckpointContractNode` (`BHV-12`); `@output_validator` (`_validate_provision_output`) blocks undeclared packages; executes `ProvisionPort` (`InProcessProvisionRunner` or `UvSubprocessProvisionRunner` invoking `uv add` with `shell=False` and explicit timeout); reloads `BuildingInspector` (`refresh_inspector_after_provision`) for `JBC-VRF-003`; exposes target `.venv` `site-packages` to `verify_runtime_importable_patches` & `_tool_inspect_package_api`; and atomically restores `pyproject.toml`, `uv.lock`, and `initial_inspector` on rollback. |

---

## 3. Statutory & Kildall Dataflow Invariants Verified (`tests/test_track6_full_basis_runtime.py`)

1. **`21 / 21` Runtime Bijection & `pydantic-graph` Reachability:** `validate_runtime_node_coverage()` verifies `set(PCE_BEHAVIOR_NODE_MAP) == set(BehaviorKind)` (`len == 21`, Fibonacci $F_8$), and all `22` concrete `AtomicBehaviorGraphNode` subclasses are registered in `build_pce_workflow_graph().nodes`.
2. **Composite 21-Behavior Kildall Soundness:** Execution traces exercising all $F_8 = 21$ `BehaviorKind`s simultaneously (`FRAME -> ELICIT -> FRAME -> BIND_CODES -> INSPECT_ENV -> INSPECT_CODE -> CONSULT -> DECOMPOSE -> CHECKPOINT -> PROVISION -> SPECIFY -> INSTRUMENT -> CHECKPOINT -> DELEGATE -> RECONCILE -> VERIFY_STATIC -> REVERT -> HYPOTHESIZE -> CURATE_MEMORY -> CHECKPOINT -> SYNTHESIZE -> REFACTOR -> VERIFY_STATIC -> VERIFY_DYNAMIC -> CERTIFY`) satisfy all local transition and global Kildall lattice laws (`verify_behavior_graph(graph).is_valid == True`).
3. **PEP 508 Injection Guard & Unpermitted Package Block:** `DependencyRequirement` (`src/jezz/methodology/mandate.py`) rejects CLI flags (`--index-url`), URLs (`@ https://`), path traversal (`../`), shell metacharacters, and duplicate `(package, scope)` entries; `build_provision_agent` forces `ModelRetry` on any undeclared package spec; and `audit_pce_dossier` (`src/jezz/methodology/dossier.py`) authorizes `pyproject.toml` / `uv.lock` mutations *if and only if* `mandate.required_dependencies` is non-empty.
4. **Atomic Manifest/Lockfile Rollback & Cross-`.venv` Smoke Import:** Exhausted remediation budgets after `ProvisionNode` restore exact pre-run `pyproject.toml` bytes, unlink newly created `uv.lock` files, and revert `BuildingInspector` to `initial_inspector`; `_with_workspace_sys_path` (`src/jezz/agents/workspace.py`) exposes packages installed in `<repo>/.venv/**/site-packages` during smoke-import and package introspection without mutating host `sys.path` after exit.
5. **Non-Lossy Negation Compaction & Longitudinal `.jezz/memory.json` Continuity:** `Hypothesis` property tests and sequential two-mandate (`MND-SEQ-001 -> MND-SEQ-002`) runs confirm `CurateMemoryNode` rejects any digest that drops active invariants or `falsified_paths` negations, persists `WorkspaceMemoryLedger` inside `<repo>/.jezz/memory.json` via `ContainedPath` (`JBC-SEC-005`/`006`), and hydrates prior sealed mandates, architectural notes, and falsified paths into subsequent mandates.
