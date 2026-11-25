"""Deterministic generator for ``docs/jbc-codebook-reference.md`` from ``CODE_BOOK`` (Track 30)."""

import argparse
from pathlib import Path
from typing import Final

from jezz.codes.codebook import CODE_BOOK, CODEBOOK_EDITION
from jezz.codes.taxonomy import BuildingCode, CodeArticle
from jezz.guards import BoundedText, ContainedPath, jezz_callable

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
_DEFAULT_OUT: Final[Path] = ContainedPath.from_parts(
    _REPO_ROOT, "docs/jbc-codebook-reference.md"
).resolved_path

_ARTICLE_TITLES: Final[dict[CodeArticle, str]] = {
    CodeArticle.DBC: "Article I — Design by Contract & Signature Breakers (`DBC`)",
    CodeArticle.FLT: "Article II — Fault Containment, Resource Ceilings, & Termination (`FLT`)",
    CodeArticle.SEC: "Article III — Security, Schema Closure, & Path Containment (`SEC`)",
    CodeArticle.COG: "Article IV — Cognitive Complexity, Cohesion, & Compartmentation (`COG`)",
    CodeArticle.OBS: "Article V — Observability, Determinism, & State Hygiene (`OBS`)",
    CodeArticle.VRF: "Article VI — Verification Non-Vacuity & Final Gate Attestations (`VRF`)",
}


def _render_provision_card(code: BuildingCode) -> list[str]:
    waivable = (
        "Yes (`CodeVariance` with justification/sunset)"
        if code.variance_permitted
        else "**No** (Non-Waivable)"
    )
    triggers = ", ".join(f"`{t.value}`" for t in sorted(code.triggers, key=lambda x: x.value))
    enforced = ", ".join(f"`{b.value}`" for b in sorted(code.enforced_at, key=lambda x: x.value))
    lines = [
        f"### `{code.code_id}` — {code.title} {{#{code.code_id.lower()}}}",
        "",
        f"- **Severity:** `{code.severity.value.upper()}` | **Waivable:** {waivable}",
        (
            f"- **Inspection Method:** `{code.inspection_method.value}` | "
            f"**Origin:** `{code.origin.value}`"
        ),
        f"- **Triggers:** {triggers} | **Enforced At:** {enforced}",
        f"- **Normative Rule:** {code.normative_rule}",
        f"- **Prevented Hazard:** {code.hazard}",
        f"- **Objective Pass Criteria:** {code.pass_criteria}",
    ]
    if code.standard_refs:
        lines.append("- **Authoritative Standards Lineage:**")
        for ref in code.standard_refs:
            clause = f" ({ref.clause})" if ref.clause else ""
            lines.append(f"  - `{ref.body.value}`{clause}: {ref.summary}")
    lines.append("")
    return lines


@jezz_callable
def generate_codebook_markdown() -> BoundedText:
    """Render the complete JBC Edition 1.3 reference manual deterministically from ``CODE_BOOK``."""
    codes = tuple(CODE_BOOK[cid] for cid in sorted(CODE_BOOK))
    lines: list[str] = [
        f"# Jezz Software Building Code (`JBC` Edition {CODEBOOK_EDITION}) — Reference Manual",
        "",
        "> **Auto-generated from [`src/jezz/codes/codebook.py`]"
        "(https://github.com/futileandstupid/jezz/blob/main/src/jezz/codes/codebook.py)** "
        "via `uv run python benchmarks/generate_codebook_docs.py`. Verified in CI against "
        "documentation drift (`INV-030-01`).",
        "",
        f"Edition `{CODEBOOK_EDITION}` codifies **{len(codes)} machine-checkable provisions** "
        "(`30` static AST/CST/graph Rough-In rules + `2` Final Gate compiler/suite attestations) "
        "enforced across **Python (`.py`), TypeScript (`.ts`/`.tsx`), "
        "Rust (`.rs`), and Go (`.go`)**.",
        "",
        "## Summary Schedule",
        "",
        "| Code ID | Article | Severity | Waivable | Method | Title |",
        "| :--- | :---: | :---: | :---: | :---: | :--- |",
    ]
    for c in codes:
        waiv = "Yes" if c.variance_permitted else "**No**"
        lines.append(
            f"| [`{c.code_id}`](#{c.code_id.lower()}) | `{c.article.value.upper()}` | "
            f"`{c.severity.value.upper()}` | {waiv} | `{c.inspection_method.value}` | {c.title} |"
        )
    lines.append("")

    for article in CodeArticle:
        art_codes = [c for c in codes if c.article == article]
        if not art_codes:
            continue
        lines.extend(["---", "", f"## {_ARTICLE_TITLES[article]}", ""])
        for c in art_codes:
            lines.extend(_render_provision_card(c))
    return ("\n".join(lines).rstrip() + "\n")[:500_000]


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate docs/jbc-codebook-reference.md.")
    parser.add_argument("--output", type=Path, default=_DEFAULT_OUT)
    ns = parser.parse_args()
    resolved = Path(ns.output).resolve()
    target = ContainedPath.from_parts(resolved.parent, resolved.name)
    target.resolved_path.parent.mkdir(parents=True, exist_ok=True)
    target.resolved_path.write_text(generate_codebook_markdown(), encoding="utf-8")


if __name__ == "__main__":
    main()
