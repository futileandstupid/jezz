"""Track 30: Documentation overhaul, codebook reference lock, citations, & MkDocs CAD site."""

import os
import re
import subprocess
import sys
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from jezz.codes.codebook import CODE_BOOK
from jezz.guards import ContainedPath, read_bounded_text

REPO_ROOT = Path(__file__).resolve().parents[1]
_HOST_ROOT_TOKENS = ("/" + "Users" + "/", "/" + "home" + "/", "C:" + "\\\\")
_NUMBERED_REF_RE = re.compile(r"^\d+\.\s+")


def test_codebook_reference_manual_matches_code_book_byte_for_byte(tmp_path: Path) -> None:
    """INV-030-01: ``docs/jbc-codebook-reference.md`` stays in 100% parity with ``CODE_BOOK``."""
    gen_script = ContainedPath.from_parts(
        REPO_ROOT, "benchmarks/generate_codebook_docs.py"
    ).resolved_path
    out_md = ContainedPath.from_parts(tmp_path, "generated_codebook.md").resolved_path
    src_dir = ContainedPath.from_parts(REPO_ROOT, "src").resolved_path
    proc = subprocess.run(
        (sys.executable, str(gen_script), "--output", str(out_md)),
        cwd=REPO_ROOT,
        env={**os.environ, "PYTHONPATH": str(src_dir)},
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"

    committed = read_bounded_text(
        ContainedPath.from_parts(REPO_ROOT, "docs/jbc-codebook-reference.md")
    )
    generated = read_bounded_text(ContainedPath.from_parts(tmp_path, "generated_codebook.md"))
    assert committed == generated
    assert len(CODE_BOOK) == 32


def test_landscape_comparison_and_bibliography_carry_hyperlinked_citations() -> None:
    """INV-030-02: Every cited paper, standard, and external tool carries an https:// URL."""
    landscape = read_bounded_text(
        ContainedPath.from_parts(REPO_ROOT, "docs/landscape-comparison.md")
    )
    refs = read_bounded_text(ContainedPath.from_parts(REPO_ROOT, "docs/references.md"))
    whitepaper = read_bounded_text(ContainedPath.from_parts(REPO_ROOT, "docs/whitepaper.md"))

    for required_target in (
        "https://github.com/github/spec-kit",
        "https://github.com/Fission-AI/OpenSpec",
        "https://github.com/bmad-code-org/BMAD-METHOD",
        "https://kiro.dev/",
        "https://tessl.io/",
        "https://github.com/gauge-sh/tach",
        "https://www.archunit.org/",
        "https://semgrep.dev/",
        "https://likec4.dev/",
        "https://structurizr.com/",
        "https://dafny.org/",
        "https://github.com/verus-lang/verus",
        "https://lean-lang.org/",
        "https://doi.org/10.1007/978-3-540-78800-3_24",
    ):
        assert required_target in landscape

    for required_doi in (
        "https://doi.org/10.1145/363235.363259",
        "https://doi.org/10.1145/512927.512945",
        "https://doi.org/10.1109/TSE.2016.2616877",
        "https://doi.org/10.1109/TSE.2012.28",
        "https://doi.org/10.1137/0201010",
        "https://doi.org/10.1109/MC.2006.212",
        "https://cwe.mitre.org/",
    ):
        assert required_doi in refs

    for doc_name, text in (("references.md", refs), ("whitepaper.md", whitepaper)):
        ref_sec = text.split("## References", maxsplit=1)[-1] if "## References" in text else text
        numbered_lines = [ln for ln in ref_sec.splitlines() if _NUMBERED_REF_RE.match(ln)]
        assert len(numbered_lines) >= 20, f"Too few numbered references in {doc_name}"
        for ln in numbered_lines:
            assert "https://" in ln, f"Unlinked bibliography item in {doc_name}: {ln}"


def test_mkdocs_strict_build_and_interactive_blueprint_cad_demo(tmp_path: Path) -> None:
    """INV-030-03: MkDocs strict build succeeds and embeds switchable A-1/E-1 CAD HTML."""
    for doc_rel in (
        "README.md",
        "mkdocs.yml",
        "docs/getting-started.md",
        "docs/cli-and-mcp-reference.md",
        "docs/interactive-blueprint.md",
        "docs/demo/jezz-blueprint.html",
        ".github/workflows/docs-pages.yml",
    ):
        txt = read_bounded_text(ContainedPath.from_parts(REPO_ROOT, doc_rel))
        assert not any(tok in txt for tok in _HOST_ROOT_TOKENS), f"Host path leaked in {doc_rel}"

    cad_html = read_bounded_text(
        ContainedPath.from_parts(REPO_ROOT, "docs/demo/jezz-blueprint.html")
    )
    assert "SHEET A-1" in cad_html
    assert "SHEET E-1" in cad_html

    site_out = ContainedPath.from_parts(tmp_path, "mkdocs_site").resolved_path
    env = {**os.environ, "NO_MKDOCS_2_WARNING": "1"}
    proc = subprocess.run(
        (sys.executable, "-m", "mkdocs", "build", "--strict", "--site-dir", str(site_out)),
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    assert ContainedPath.from_parts(
        site_out, "interactive-blueprint/index.html"
    ).resolved_path.is_file()
    assert ContainedPath.from_parts(site_out, "demo/jezz-blueprint.html").resolved_path.is_file()


@settings(max_examples=32)
@given(code_id=st.sampled_from(tuple(sorted(CODE_BOOK))))
def test_property_every_codebook_provision_has_anchor_in_reference_manual(code_id: str) -> None:
    """Property: Every provision in ``CODE_BOOK`` is documented with its anchor & metadata."""
    manual = read_bounded_text(
        ContainedPath.from_parts(REPO_ROOT, "docs/jbc-codebook-reference.md")
    )
    provision = CODE_BOOK[code_id]
    assert f"{{#{code_id.lower()}}}" in manual
    assert provision.title in manual
    assert provision.normative_rule in manual
