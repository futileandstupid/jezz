"""Deterministic auditor recomputing published whitepaper metrics from telemetry (INV-029-02)."""

import argparse
import io
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Annotated, Final

from pydantic import BaseModel, Field

from jezz.guards import (
    STRICT_FROZEN_CONFIG,
    WIRE_TUPLE_VALIDATOR,
    BoundedText,
    ContainedPath,
    FiniteFloat,
    IdentifierStr,
    InstanceGuard,
    NonNegativeInt,
    PositiveInt,
    jezz_callable,
    read_bounded_text,
)

_BENCHMARKS_DIR: Final[Path] = Path(__file__).resolve().parent
_DEFAULT_METRICS_FILE: Final[Path] = ContainedPath.from_parts(
    _BENCHMARKS_DIR, "published_metrics.json"
).resolved_path
_TOLERANCE: Final[float] = 0.15


class PolyglotWaveCell(BaseModel):
    """Single Ecosystem x Suite A/B cell from Track 21 (Wave 21A)."""

    model_config = STRICT_FROZEN_CONFIG

    ecosystem: IdentifierStr
    suite: IdentifierStr
    cases: PositiveInt
    sdd_occupiable: NonNegativeInt
    pce_occupiable: NonNegativeInt
    sdd_violations: NonNegativeInt
    pce_violations: NonNegativeInt
    sdd_oracle_passed: NonNegativeInt
    pce_oracle_passed: NonNegativeInt
    sdd_spec_lines_total: NonNegativeInt
    pce_spec_lines_total: PositiveInt
    sdd_gross_input_tokens: PositiveInt
    sdd_cache_read_tokens: NonNegativeInt
    sdd_billed_input_tokens: PositiveInt
    sdd_output_tokens: PositiveInt
    sdd_mean_latency_sec: Annotated[FiniteFloat, Field(gt=0.0)]
    pce_gross_input_tokens: PositiveInt
    pce_cache_read_tokens: NonNegativeInt
    pce_billed_input_tokens: PositiveInt
    pce_output_tokens: PositiveInt
    pce_mean_latency_sec: Annotated[FiniteFloat, Field(gt=0.0)]


class PythonWave4Summary(BaseModel):
    """Summary telemetry from Track 4 (Waves 4B/4D) and Track 7 prompt caching."""

    model_config = STRICT_FROZEN_CONFIG

    wave_4b_sdd_violations: PositiveInt
    wave_4d_sdd_violations: PositiveInt
    wave_4b_sdd_life_safety: PositiveInt
    wave_4d_sdd_life_safety: PositiveInt
    wave_4b_pce_latency_sec: Annotated[FiniteFloat, Field(gt=0.0)]
    wave_4d_pce_latency_sec: Annotated[FiniteFloat, Field(gt=0.0)]
    wave_4b_pce_tokens: PositiveInt
    wave_4d_pce_tokens: PositiveInt
    wave_7_gross_input_tokens: PositiveInt
    wave_7_billed_input_tokens: PositiveInt


class OssRatchetProbeRecord(BaseModel):
    """Track 11 surgical brownfield renovation probe record."""

    model_config = STRICT_FROZEN_CONFIG

    probe_id: IdentifierStr
    baseline_scope_violations: PositiveInt
    post_unwaived_violations: NonNegativeInt
    is_occupancy_sealed: bool
    gross_input_tokens: PositiveInt
    cache_read_tokens: NonNegativeInt


class PublishedMetricsBundle(BaseModel):
    """Top-level schema for ``benchmarks/published_metrics.json``."""

    model_config = STRICT_FROZEN_CONFIG

    schema_version: IdentifierStr
    description: BoundedText
    wave_21a_polyglot_ab: Annotated[
        tuple[PolyglotWaveCell, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(min_length=1, max_length=32),
    ]
    python_wave_4_summary: PythonWave4Summary
    track_11_oss_ratchet_probes: Annotated[
        tuple[OssRatchetProbeRecord, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(min_length=1, max_length=16),
    ]
    headline_claims: Mapping[IdentifierStr, FiniteFloat]


class ClaimCheckVerdict(BaseModel):
    """Comparison between a published headline number and its recomputed value."""

    model_config = STRICT_FROZEN_CONFIG

    claim_key: IdentifierStr
    published: FiniteFloat
    recomputed: FiniteFloat
    abs_diff: Annotated[FiniteFloat, Field(ge=0.0)]
    passed: bool


class ClaimVerificationReport(BaseModel):
    """Complete deterministic audit report over all published whitepaper claims."""

    model_config = STRICT_FROZEN_CONFIG

    verdicts: Annotated[
        tuple[ClaimCheckVerdict, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=64),
    ]
    billed_accounting_consistent: bool

    @property
    def all_passed(self) -> bool:
        """Return True iff every headline claim and cache billing invariant passed."""
        return self.billed_accounting_consistent and all(v.passed for v in self.verdicts)

    @jezz_callable
    def to_markdown(self) -> BoundedText:
        """Render Markdown ledger of verified whitepaper statistics."""
        banner = "VERIFIED" if self.all_passed else "FAILED"
        lines = [
            f"# Jezz Whitepaper Telemetry Audit (`{banner}`)",
            f"- **0.1x Cache Billing Consistent:** `{self.billed_accounting_consistent}`",
            "",
            "| Claim Key | Published | Recomputed | `|Δ|` | Status |",
            "| :--- | ---: | ---: | ---: | :---: |",
        ]
        for v in self.verdicts:
            tag = "PASS" if v.passed else "FAIL"
            lines.append(
                f"| `{v.claim_key}` | `{v.published:.2f}` | `{v.recomputed:.2f}` | "
                f"`{v.abs_diff:.3f}` | **{tag}** |"
            )
        return "\n".join(lines)[:500_000]


def _pct_delta(new_val: float, base_val: float) -> float:
    return ((new_val - base_val) / base_val) * 100.0


def _compute_polyglot_claims(cells: Sequence[PolyglotWaveCell]) -> dict[str, float]:
    total_cases = sum(c.cases for c in cells)
    sdd_occ = sum(c.sdd_occupiable for c in cells)
    pce_occ = sum(c.pce_occupiable for c in cells)
    sdd_viol = sum(c.sdd_violations for c in cells)
    pce_viol = sum(c.pce_violations for c in cells)
    sdd_oracle_fail = total_cases - sum(c.sdd_oracle_passed for c in cells)
    sdd_spec = sum(c.sdd_spec_lines_total for c in cells)
    pce_spec = sum(c.pce_spec_lines_total for c in cells)
    pce_gross = sum(c.pce_gross_input_tokens for c in cells)
    pce_cache = sum(c.pce_cache_read_tokens for c in cells)
    sdd_billed = sum(c.sdd_billed_input_tokens for c in cells)
    pce_billed = sum(c.pce_billed_input_tokens for c in cells)
    sdd_out = sum(c.sdd_output_tokens for c in cells)
    pce_out = sum(c.pce_output_tokens for c in cells)
    sdd_lat = sum(c.sdd_mean_latency_sec * c.cases for c in cells) / total_cases
    pce_lat = sum(c.pce_mean_latency_sec * c.cases for c in cells) / total_cases
    return {
        "polyglot_pce_occupancy_pct": (pce_occ / total_cases) * 100.0,
        "polyglot_sdd_occupancy_pct": (sdd_occ / total_cases) * 100.0,
        "polyglot_sdd_total_violations": float(sdd_viol),
        "polyglot_pce_total_violations": float(pce_viol),
        "polyglot_sdd_oracle_escape_pct": (sdd_oracle_fail / total_cases) * 100.0,
        "polyglot_spec_compression_x": sdd_spec / pce_spec,
        "polyglot_pce_cache_hit_pct": (pce_cache / pce_gross) * 100.0,
        "polyglot_billed_input_delta_pct": _pct_delta(pce_billed, sdd_billed),
        "polyglot_output_token_delta_pct": _pct_delta(pce_out, sdd_out),
        "polyglot_latency_delta_pct": _pct_delta(pce_lat, sdd_lat),
    }


def _verify_billed_input_formula(cells: Sequence[PolyglotWaveCell]) -> bool:
    for c in cells:
        exp_sdd = round(c.sdd_gross_input_tokens - 0.9 * c.sdd_cache_read_tokens)
        exp_pce = round(c.pce_gross_input_tokens - 0.9 * c.pce_cache_read_tokens)
        if (
            abs(exp_sdd - c.sdd_billed_input_tokens) > 2
            or abs(exp_pce - c.pce_billed_input_tokens) > 2
        ):
            return False
    return True


@jezz_callable
def verify_published_bundle(bundle: PublishedMetricsBundle) -> ClaimVerificationReport:
    """Recompute all headline whitepaper ratios and verify exact parity within ``0.15``."""
    w4 = bundle.python_wave_4_summary
    probes = bundle.track_11_oss_ratchet_probes
    recomputed = {
        **_compute_polyglot_claims(bundle.wave_21a_polyglot_ab),
        "wave_4d_sdd_violation_delta_pct": _pct_delta(
            w4.wave_4d_sdd_violations, w4.wave_4b_sdd_violations
        ),
        "wave_4d_pce_latency_delta_pct": _pct_delta(
            w4.wave_4d_pce_latency_sec, w4.wave_4b_pce_latency_sec
        ),
        "wave_7_cache_compression_pct": _pct_delta(
            w4.wave_7_billed_input_tokens, w4.wave_7_gross_input_tokens
        ),
        "track_11_seal_rate_pct": (
            sum(1 for p in probes if p.is_occupancy_sealed and p.post_unwaived_violations == 0)
            / len(probes)
        )
        * 100.0,
    }
    verdicts: list[ClaimCheckVerdict] = []
    for key, pub_val in sorted(bundle.headline_claims.items()):
        rec_val = recomputed[key]
        diff = abs(pub_val - rec_val)
        verdicts.append(
            ClaimCheckVerdict(
                claim_key=key,
                published=round(pub_val, 4),
                recomputed=round(rec_val, 4),
                abs_diff=round(diff, 4),
                passed=(diff <= _TOLERANCE),
            )
        )
    return ClaimVerificationReport(
        verdicts=tuple(verdicts),
        billed_accounting_consistent=_verify_billed_input_formula(bundle.wave_21a_polyglot_ab),
    )


@jezz_callable
def run_claims_cli(
    argv: Sequence[BoundedText] | None = None,
    *,
    stdout: Annotated[io.TextIOBase, InstanceGuard(io.TextIOBase)] | None = None,
) -> Annotated[int, Field(ge=0, le=2)]:
    """CLI entrypoint for ``python benchmarks/verify_published_claims.py``."""
    out = stdout if stdout is not None else sys.stdout
    parser = argparse.ArgumentParser(description="Verify published whitepaper telemetry claims.")
    parser.add_argument("--metrics-file", type=Path, default=_DEFAULT_METRICS_FILE)
    parser.add_argument("--format", dest="fmt", choices=("markdown", "json"), default="markdown")
    ns = parser.parse_args(list(argv) if argv is not None else None)

    resolved = Path(ns.metrics_file).resolve()
    contained = ContainedPath.from_parts(resolved.parent, resolved.name)
    bundle = PublishedMetricsBundle.model_validate_json(read_bounded_text(contained))
    report = verify_published_bundle(bundle)
    rendered = report.model_dump_json(indent=2) if ns.fmt == "json" else report.to_markdown()
    out.write(f"{rendered}\n")
    out.flush()
    return 0 if report.all_passed else 1


if __name__ == "__main__":
    raise SystemExit(run_claims_cli())
