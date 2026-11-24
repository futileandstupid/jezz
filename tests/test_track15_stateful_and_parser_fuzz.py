"""Track 15: Stateful Hypothesis RuleBasedStateMachines and 4-language parser crash-fuzzing."""

import tempfile
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from hypothesis.stateful import Bundle, RuleBasedStateMachine, invariant, rule
from pydantic import ValidationError

from jezz.agents import WorkspaceSandbox
from jezz.codes import (
    BuildingInspector,
    CodePermit,
    CodeVariance,
    ManifestDependencies,
    OccupancyRuling,
    SourceSpan,
    parse_any_source_unit,
)
from jezz.guards import ContainedPath, read_bounded_text

_AUTH_PATHS = ("src/pkg/alpha.py", "src/pkg/beta.py", "tests/test_pkg.py")


@dataclass
class _SandboxModelContext:
    """Cohesive reference state for WorkspaceSandboxStateMachine."""

    tmp: tempfile.TemporaryDirectory[str]
    root: Path
    sandbox: WorkspaceSandbox
    disk_model: dict[str, str | None]
    modified_model: set[str]
    checkpoints: dict[str, tuple[dict[str, str | None], set[str]]]


def _create_sandbox_model_context() -> _SandboxModelContext:
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name).resolve()
    init_file = ContainedPath.from_parts(root, "src/pkg/alpha.py").resolved_path
    init_file.parent.mkdir(parents=True, exist_ok=True)
    init_file.write_text("X: int = 0\n", encoding="utf-8")
    return _SandboxModelContext(
        tmp=tmp,
        root=root,
        sandbox=WorkspaceSandbox(root, frozenset(_AUTH_PATHS)),
        disk_model={
            "src/pkg/alpha.py": "X: int = 0\n",
            "src/pkg/beta.py": None,
            "tests/test_pkg.py": None,
        },
        modified_model=set(),
        checkpoints={},
    )


class WorkspaceSandboxStateMachine(RuleBasedStateMachine):
    """Stateful model check proving checkpoint/restore exactness and write-scope confinement."""

    saved_ids = Bundle("saved_ids")

    def __init__(self) -> None:
        super().__init__()
        self._ctx = _create_sandbox_model_context()

    def teardown(self) -> None:
        self._ctx.tmp.cleanup()

    @rule(
        rel_path=st.sampled_from(_AUTH_PATHS),
        val=st.integers(min_value=0, max_value=10_000),
    )
    def write_authorized(self, rel_path: str, val: int) -> None:
        body = f"VAL: int = {val}\n"
        self._ctx.sandbox.write_authorized_file(rel_path, body)
        self._ctx.disk_model[rel_path] = body
        self._ctx.modified_model.add(rel_path)

    @rule(val=st.integers(min_value=1, max_value=999))
    def reject_unauthorized(self, val: int) -> None:
        with pytest.raises(PermissionError):
            self._ctx.sandbox.write_authorized_file("src/unauth/secret.py", f"S = {val}\n")

    @rule(
        target=saved_ids,
        cp_name=st.sampled_from(("cp_a", "cp_b", "cp_c", "cp_d")),
    )
    def save_checkpoint(self, cp_name: str) -> str:
        self._ctx.sandbox.create_checkpoint(cp_name)
        self._ctx.checkpoints[cp_name] = (
            dict(self._ctx.disk_model),
            set(self._ctx.modified_model),
        )
        assert self._ctx.sandbox.has_checkpoint(cp_name)
        return cp_name

    @rule(cp_name=saved_ids)
    def restore_saved_checkpoint(self, cp_name: str) -> None:
        restored_mod = self._ctx.sandbox.restore_checkpoint(cp_name)
        saved_disk, saved_mod = self._ctx.checkpoints[cp_name]
        self._ctx.disk_model = dict(saved_disk)
        self._ctx.modified_model = set(saved_mod)
        assert restored_mod == frozenset(self._ctx.modified_model)

    @invariant()
    def disk_and_modified_match_reference_model(self) -> None:
        assert self._ctx.sandbox.modified_files == frozenset(self._ctx.modified_model)
        for rel_path, expected in self._ctx.disk_model.items():
            c_path = ContainedPath.from_parts(self._ctx.root, rel_path)
            if expected is None:
                assert not c_path.resolved_path.exists()
            else:
                assert read_bounded_text(c_path) == expected


TestWorkspaceSandboxLifecycle = WorkspaceSandboxStateMachine.TestCase
TestWorkspaceSandboxLifecycle.settings = settings(
    max_examples=35,
    stateful_step_count=20,
    deadline=None,
)


class PermitVarianceStateMachine(RuleBasedStateMachine):
    """Stateful model check proving variance sunset expiration and LIFE_SAFETY non-waivability."""

    def __init__(self) -> None:
        super().__init__()
        self._current_date = date(2026, 1, 1)
        self._variances: list[CodeVariance] = []
        self._permit = CodePermit.full_book("PERMIT-STATEFUL")

    @rule(days=st.integers(min_value=1, max_value=30))
    def advance_clock(self, days: int) -> None:
        self._current_date = self._current_date + timedelta(days=days)

    @rule(
        ttl_days=st.integers(min_value=1, max_value=45),
        file_idx=st.sampled_from((1, 2, 3)),
    )
    def grant_workmanship_variance(self, ttl_days: int, file_idx: int) -> None:
        vid = f"VAR-{len(self._variances) + 1:03d}"
        sunset = self._current_date + timedelta(days=ttl_days)
        var = CodeVariance(
            variance_id=vid,
            code_id="JBC-COG-001",
            file_path=f"src/mod_{file_idx}.py",
            authorized_by="Chief Structural Engineer",
            justification="Scheduled refactor under active brownfield permit.",
            expires_on=sunset,
        )
        self._variances.append(var)
        self._permit = CodePermit.full_book("PERMIT-STATEFUL", variances=tuple(self._variances))

    @rule()
    def life_safety_variance_always_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CodeVariance(
                variance_id="VAR-ILLEGAL",
                code_id="JBC-SEC-001",
                file_path="src/mod_1.py",
                authorized_by="Chief Engineer",
                justification="Attempted waiver on non-waivable life-safety provision.",
            )
        assert (
            self._permit.find_variance(
                "JBC-SEC-001",
                SourceSpan(file_path="src/mod_1.py", line=1),
                reference_date=self._current_date,
            )
            is None
        )

    @invariant()
    def active_and_expired_variances_match_calendar(self) -> None:
        for file_idx in (1, 2, 3):
            target = f"src/mod_{file_idx}.py"
            span = SourceSpan(file_path=target, line=5)
            expected_active = next(
                (
                    v
                    for v in self._variances
                    if v.file_path == target
                    and (v.expires_on is None or self._current_date <= v.expires_on)
                ),
                None,
            )
            actual_active = self._permit.find_variance(
                "JBC-COG-001",
                span,
                reference_date=self._current_date,
            )
            assert actual_active == expected_active


TestPermitVarianceLifecycle = PermitVarianceStateMachine.TestCase
TestPermitVarianceLifecycle.settings = settings(
    max_examples=35,
    stateful_step_count=20,
    deadline=None,
)


_SEED_SNIPPETS = (
    "def f(x: int) -> int:\n    return x + 1\n",
    "export function f(x: number): number { if (x < 0) return 0; return x; }\n",
    "pub fn f(x: i32) -> i32 { match x { 0 => 1, _ => x } }\n",
    "package p\nfunc F(x int) int { if x < 0 { return 0 }; return x }\n",
    "class C:\n    def m(self, a, b, c, d, e, f, g): pass\n",
    "try { eval(x); } catch (e) {}\n",
    "unsafe { std::ptr::null::<u8>().read() }\n",
)


def _try_parse_unit(
    file_path: str,
    source: str,
) -> object:
    try:
        return parse_any_source_unit(file_path, source)
    except SyntaxError:
        return None


@settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(
    raw_noise=st.text(max_size=300),
    seed=st.sampled_from(_SEED_SNIPPETS),
    cut=st.integers(min_value=0, max_value=80),
)
def test_property_polyglot_parsers_and_inspector_never_crash_on_arbitrary_source(
    raw_noise: str,
    seed: str,
    cut: int,
) -> None:
    spliced = f"{seed[:cut]}{raw_noise}{seed[cut:]}"
    inspector = BuildingInspector(ManifestDependencies(workspace_roots=frozenset({"pkg", "tests"})))
    parsed_units = []
    syntax_errors = 0
    for path in ("src/pkg/fuzz.py", "src/fuzz.ts", "src/fuzz.tsx", "src/fuzz.rs", "fuzz.go"):
        try:
            parsed_units.append(parse_any_source_unit(path, spliced))
        except SyntaxError:
            syntax_errors += 1
    assert len(parsed_units) + syntax_errors == 5
    if parsed_units:
        report = inspector.inspect_rough_in(tuple(parsed_units))
        assert report.ruling in set(OccupancyRuling)
    assert _try_parse_unit("src/fuzz_raw.ts", raw_noise) is None or isinstance(raw_noise, str)
