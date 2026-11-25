"""Deterministic $0 shallow-clone brownfield corpus reproducer (Track 29 — INV-029-01)."""

import argparse
import io
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Final

from pydantic import BaseModel, Field

from jezz.codes import (
    CodeSeverity,
    OccupancyRuling,
    SupportedLanguage,
    build_workspace_ast_graph,
    build_workspace_inspector,
    discover_workspace_units,
)
from jezz.guards import (
    STRICT_FROZEN_CONFIG,
    WIRE_TUPLE_VALIDATOR,
    BoundedText,
    ContainedPath,
    FiniteFloat,
    IdentifierStr,
    InstanceGuard,
    LoopBudget,
    NonNegativeInt,
    PositiveInt,
    jezz_callable,
    read_bounded_text,
)

_BENCHMARKS_DIR: Final[Path] = Path(__file__).resolve().parent
_DEFAULT_LOCKFILE: Final[Path] = ContainedPath.from_parts(
    _BENCHMARKS_DIR, "brownfield_corpus.lock.json"
).resolved_path

_TIER_ORDER: Final[dict[str, int]] = {"smoke": 1, "core": 2, "full": 3}


class ReproTier(StrEnum):
    """Execution budget tiers for brownfield corpus reproduction."""

    SMOKE = "smoke"
    CORE = "core"
    FULL = "full"


class CorpusRepoEntry(BaseModel):
    """Pinned external repository specification in ``brownfield_corpus.lock.json``."""

    model_config = STRICT_FROZEN_CONFIG

    id: IdentifierStr
    repo_url: BoundedText
    commit_or_tag: IdentifierStr
    subpath: IdentifierStr = "."
    language: SupportedLanguage
    tier: ReproTier
    expected_ruling: OccupancyRuling
    recorded_units: PositiveInt
    recorded_loc: PositiveInt
    recorded_life_safety: NonNegativeInt


class BrownfieldCorpusLock(BaseModel):
    """Top-level validated lockfile schema for brownfield audits."""

    model_config = STRICT_FROZEN_CONFIG

    schema_version: IdentifierStr
    description: BoundedText
    repositories: Annotated[
        tuple[CorpusRepoEntry, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(min_length=1, max_length=64),
    ]


class RepoReproOutcome(BaseModel):
    """Observed vs. recorded JBC inspection metrics for a single corpus repository."""

    model_config = STRICT_FROZEN_CONFIG

    id: IdentifierStr
    commit_or_tag: IdentifierStr
    language: SupportedLanguage
    observed_ruling: OccupancyRuling
    ruling_matches: bool
    observed_units: NonNegativeInt
    recorded_units: PositiveInt
    observed_loc: NonNegativeInt
    recorded_loc: PositiveInt
    life_safety_violations: NonNegativeInt
    total_violations: NonNegativeInt
    ast_symbols: NonNegativeInt
    elapsed_ms: Annotated[FiniteFloat, Field(ge=0.0)]


class BrownfieldReproReport(BaseModel):
    """Aggregate report across all reproduced brownfield corpus repositories."""

    model_config = STRICT_FROZEN_CONFIG

    tier: ReproTier
    outcomes: Annotated[
        tuple[RepoReproOutcome, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=64),
    ]

    @property
    def all_rulings_matched(self) -> bool:
        """Return True iff every audited repository matched its expected occupancy ruling."""
        return all(item.ruling_matches for item in self.outcomes)

    @jezz_callable
    def to_markdown(self) -> BoundedText:
        """Render a deterministic Markdown comparison table."""
        status = "VERIFIED" if self.all_rulings_matched else "MISMATCH"
        lines = [
            f"# Jezz Brownfield Reproducibility Report (`{status}`, tier=`{self.tier}`)",
            "",
            "| Repo ID | Lang | Ruling (Match) | Units (Obs / Lock) | LOC (Obs / Lock) | "
            "`LIFE_SAFETY` | Total Viol. | AST Syms | Time (`ms`) |",
            "| :--- | :---: | :---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
        for row in self.outcomes:
            mark = "YES" if row.ruling_matches else "NO"
            lines.append(
                f"| `{row.id}` (`{row.commit_or_tag}`) | `{row.language}` | "
                f"`{row.observed_ruling.upper()}` (`{mark}`) | "
                f"`{row.observed_units}` / `{row.recorded_units}` | "
                f"`{row.observed_loc}` / `{row.recorded_loc}` | "
                f"`{row.life_safety_violations}` | `{row.total_violations}` | "
                f"`{row.ast_symbols}` | `{row.elapsed_ms:.1f}` |"
            )
        return "\n".join(lines)[:500_000]


@jezz_callable
def load_brownfield_lock(lock_path: Path = _DEFAULT_LOCKFILE) -> BrownfieldCorpusLock:
    """Load and schema-validate ``brownfield_corpus.lock.json``."""
    resolved = lock_path.resolve()
    contained = ContainedPath.from_parts(resolved.parent, resolved.name)
    return BrownfieldCorpusLock.model_validate_json(read_bounded_text(contained))


def _shallow_fetch_pinned_repo(entry: CorpusRepoEntry, work_dir: Path) -> Path:
    checkout = ContainedPath.from_parts(work_dir, entry.id).resolved_path
    checkout.mkdir(parents=True, exist_ok=True)
    commands: tuple[tuple[str, ...], ...] = (
        ("init", "-q"),
        ("remote", "add", "origin", entry.repo_url),
        ("fetch", "--depth", "1", "origin", entry.commit_or_tag),
        ("checkout", "-q", "FETCH_HEAD"),
    )
    for git_args in commands:
        subprocess.run(
            ("git", "-C", str(checkout), *git_args),
            check=True,
            capture_output=True,
            text=True,
            timeout=180,
        )
    if entry.subpath == ".":
        return checkout
    return ContainedPath.from_parts(checkout, entry.subpath).resolved_path


@jezz_callable
def audit_pinned_checkout(entry: CorpusRepoEntry, repo_dir: Path) -> RepoReproOutcome:
    """Run deterministic JBC inspection and AST/CST graph extraction on ``repo_dir``."""
    t0 = time.perf_counter()
    resolved = repo_dir.resolve()
    units = discover_workspace_units(resolved, languages=frozenset({entry.language}))
    inspector = build_workspace_inspector(resolved, units)
    report = inspector.inspect_rough_in(units)
    ast_graph = build_workspace_ast_graph(units)
    elapsed_ms = round(max(0.0, (time.perf_counter() - t0) * 1000.0), 2)

    total_loc = sum(u.source.count("\n") + (1 if u.source else 0) for u in units)
    unwaived = report.unwaived_violations()
    life_safety = sum(1 for f in unwaived if f.severity == CodeSeverity.LIFE_SAFETY)
    ast_syms = len(ast_graph.symbols)
    return RepoReproOutcome(
        id=entry.id,
        commit_or_tag=entry.commit_or_tag,
        language=entry.language,
        observed_ruling=report.ruling,
        ruling_matches=(report.ruling == entry.expected_ruling),
        observed_units=len(units),
        recorded_units=entry.recorded_units,
        observed_loc=total_loc,
        recorded_loc=entry.recorded_loc,
        life_safety_violations=life_safety,
        total_violations=len(unwaived),
        ast_symbols=ast_syms,
        elapsed_ms=elapsed_ms,
    )


@jezz_callable
def run_brownfield_reproduction(
    lock: BrownfieldCorpusLock,
    *,
    tier: ReproTier = ReproTier.SMOKE,
    repo_ids: Sequence[IdentifierStr] = (),
    local_overrides: Annotated[Mapping[IdentifierStr, Path], Field(max_length=64)] | None = None,
) -> BrownfieldReproReport:
    """Execute shallow-clone or local-override brownfield verification for ``tier``."""
    max_rank = _TIER_ORDER[tier.value]
    wanted = set(repo_ids)
    overrides = dict(local_overrides or {})
    selected = [
        e
        for e in lock.repositories
        if (e.id in wanted if wanted else _TIER_ORDER[e.tier.value] <= max_rank)
    ]
    outcomes: list[RepoReproOutcome] = []
    budget = LoopBudget(max_steps=128)
    with tempfile.TemporaryDirectory(prefix="jezz-repro-") as tmp_str:
        tmp_root = Path(tmp_str).resolve()
        for entry in selected:
            budget.tick()
            target_dir = (
                overrides[entry.id].resolve()
                if entry.id in overrides
                else _shallow_fetch_pinned_repo(entry, tmp_root)
            )
            outcomes.append(audit_pinned_checkout(entry, target_dir))
    return BrownfieldReproReport(tier=tier, outcomes=tuple(outcomes))


def _parse_override_pairs(raw_items: Sequence[str] | None) -> dict[str, Path]:
    parsed: dict[str, Path] = {}
    for item in raw_items or ():
        if "=" not in item:
            msg = f"Expected '--local-checkout <id>=<path>', got '{item}'."
            raise ValueError(msg)
        repo_id, raw_path = item.split("=", maxsplit=1)
        parsed[repo_id.strip()] = Path(raw_path.strip()).resolve()
    return parsed


@jezz_callable
def run_repro_cli(
    argv: Sequence[BoundedText] | None = None,
    *,
    stdout: Annotated[io.TextIOBase, InstanceGuard(io.TextIOBase)] | None = None,
) -> Annotated[int, Field(ge=0, le=2)]:
    """CLI entrypoint for ``python benchmarks/reproduce_brownfield.py``."""
    out = stdout if stdout is not None else sys.stdout
    parser = argparse.ArgumentParser(description="Reproduce $0 JBC brownfield OSS audits.")
    parser.add_argument("--lockfile", type=Path, default=_DEFAULT_LOCKFILE)
    parser.add_argument(
        "--tier", choices=tuple(t.value for t in ReproTier), default=ReproTier.SMOKE.value
    )
    parser.add_argument("--repo-id", dest="repo_ids", action="append", default=None)
    parser.add_argument("--local-checkout", dest="local_checkouts", action="append", default=None)
    parser.add_argument("--format", dest="fmt", choices=("markdown", "json"), default="markdown")
    ns = parser.parse_args(list(argv) if argv is not None else None)

    lock = load_brownfield_lock(Path(ns.lockfile))
    report = run_brownfield_reproduction(
        lock,
        tier=ReproTier(str(ns.tier)),
        repo_ids=tuple(ns.repo_ids or ()),
        local_overrides=_parse_override_pairs(ns.local_checkouts),
    )
    rendered = report.model_dump_json(indent=2) if ns.fmt == "json" else report.to_markdown()
    out.write(f"{rendered}\n")
    out.flush()
    return 0 if report.all_rulings_matched else 1


if __name__ == "__main__":
    raise SystemExit(run_repro_cli())
