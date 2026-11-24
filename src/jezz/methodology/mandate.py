"""Pillar 3 (PCE): Anti-Agile, anti-theatre engineering mandates and disjoint write scopes."""

import re
from collections.abc import Sequence
from enum import StrEnum
from typing import Annotated, Final, Self

import icontract
from pydantic import AfterValidator, BaseModel, Field, model_validator

from jezz.codes.polyglot_ir import SupportedLanguage
from jezz.guards import (
    STRICT_FROZEN_CONFIG,
    WIRE_FROZENSET_VALIDATOR,
    WIRE_TUPLE_VALIDATOR,
    IdentifierStr,
    LoopBudget,
    ShortText,
    jezz_callable,
)

METHODOLOGY_NAME: Final[str] = "Permit-and-Contract Engineering"
METHODOLOGY_ACRONYM: Final[str] = "PCE"

_USER_STORY_THEATRE_RE: Final[re.Pattern[str]] = re.compile(
    r"\bas\s+an?\s+[^,\n]{1,60},\s*i\s+want\b",
    re.IGNORECASE,
)
_CHECKBOX_THEATRE_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:^|\n)\s*[-*]\s*\[[ xX]]",
)
_AGILE_CEREMONY_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:story\s+points?|sprint\s+velocity|scrum\s+ceremony|backlog\s+grooming|"
    r"spec-driven\s+development)\b",
    re.IGNORECASE,
)
_WEASEL_MODAL_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:should|ideally|maybe|might|nice\s+to\s+have|try\s+to)\b",
    re.IGNORECASE,
)
_PYTHON_SYMBOL_RE: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:(?:\.|::)[A-Za-z_][A-Za-z0-9_]*)*$"
)
_SAFE_PACKAGE_NAME_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?:"
    r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,126}[A-Za-z0-9])?|"
    r"@[A-Za-z0-9][A-Za-z0-9._-]{0,60}/[A-Za-z0-9][A-Za-z0-9._-]{0,60}|"
    r"[A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z]{2,24}(?:/[A-Za-z0-9._-]+)+"
    r")$"
)
_SAFE_VERSION_CONSTRAINT_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?:\s*(?:===|==|~=|!=|<=|>=|<|>|\^|~)?\s*[A-Za-z0-9][A-Za-z0-9._*+-]{0,63}"
    r"(?:\s*,\s*(?:===|==|~=|!=|<=|>=|<|>|\^|~)?\s*[A-Za-z0-9][A-Za-z0-9._*+-]{0,63})*\s*)?$"
)
_WINDOWS_DRIVE_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z]:")
_PEP440_OPERATOR_PREFIXES: Final[tuple[str, ...]] = ("===", "==", "~=", "!=", "<=", ">=", "<", ">")
_AUTHORIZED_MANIFEST_FILES: Final[frozenset[str]] = frozenset(
    {
        "pyproject.toml",
        "uv.lock",
        "package.json",
        "package-lock.json",
        "pnpm-lock.yaml",
        "Cargo.toml",
        "Cargo.lock",
        "go.mod",
        "go.sum",
    }
)


def _validate_consult_target(raw: str) -> str:
    cleaned = raw.strip()
    if not cleaned or any(ord(ch) < 32 or ord(ch) == 127 for ch in cleaned):
        msg = "Consultation target must be a non-empty printable string."
        raise ValueError(msg)
    low = cleaned.lower()
    if "://" in low or low.startswith(("http:", "https:", "file:", "git+")):
        msg = (
            f"Remote or URL consultation target '{cleaned}' is prohibited; "
            "BHV-06 CONSULT resolves offline local specs, docs, and package APIs."
        )
        raise ValueError(msg)
    if cleaned.startswith(("/", "\\", "~")) or _WINDOWS_DRIVE_RE.match(cleaned) is not None:
        msg = f"Consultation target '{cleaned}' must not be an absolute or host-anchored path."
        raise ValueError(msg)
    segments = cleaned.replace("\\", "/").split("/")
    if ".." in segments:
        msg = f"Path traversal blocked in consultation target '{cleaned}'."
        raise ValueError(msg)
    return cleaned


type ConsultTargetStr = Annotated[
    str,
    Field(min_length=1, max_length=256),
    AfterValidator(_validate_consult_target),
]


def _is_safe_package_token(token: str) -> bool:
    if ".." in token or token.lower().startswith(("git+", "http:", "https:", "file:", "path:")):
        return False
    return _SAFE_PACKAGE_NAME_RE.fullmatch(token) is not None


def _is_valid_import_root(import_root: str) -> bool:
    if import_root.isidentifier():
        return True
    if import_root.startswith("@") or "/" in import_root:
        return _is_safe_package_token(import_root)
    return False


def _validate_python_symbol_ref(symbol: str, *, field_label: str) -> None:
    if _PYTHON_SYMBOL_RE.fullmatch(symbol) is None:
        msg = (
            f"{field_label} '{symbol}' must be a valid Python identifier or polyglot qualified "
            "symbol (e.g. 'test_quota', 'pkg.mod.ClassName', or 'ledger::Escrow::settle'), "
            "never a file path or 'file.py:symbol'."
        )
        raise ValueError(msg)


class PcePhase(StrEnum):
    """Five statutory phases of Permit-and-Contract Engineering (PCE)."""

    MANDATE = "mandate"
    PERMIT = "permit"
    CONTRACT = "contract"
    CONSTRUCTION = "construction"
    OCCUPANCY = "occupancy"


class DependencyScope(StrEnum):
    """Manifest dependency group targeted by ``BHV-13 PROVISION``."""

    RUNTIME = "runtime"
    DEV = "dev"


class DependencyRequirement(BaseModel):
    """Permit-checked polyglot dependency declaration authorized for ``BHV-13 PROVISION``."""

    model_config = STRICT_FROZEN_CONFIG

    package: Annotated[str, Field(min_length=1, max_length=128)]
    version_constraint: Annotated[str, Field(max_length=128)] = ""
    scope: DependencyScope = DependencyScope.RUNTIME
    import_root: Annotated[str, Field(max_length=128)] = ""
    ecosystem: SupportedLanguage | None = None

    @model_validator(mode="after")
    def _validate_requirement_spec(self) -> Self:
        if not _is_safe_package_token(self.package):
            msg = (
                f"Invalid package name '{self.package}'; must be a pure PEP 508, npm, Cargo, or "
                "Go module identifier without flags, traversal, URLs, or shell metacharacters."
            )
            raise ValueError(msg)
        if _SAFE_VERSION_CONSTRAINT_RE.fullmatch(self.version_constraint) is None:
            msg = (
                f"Invalid version_constraint '{self.version_constraint}' for '{self.package}'; "
                "use safe PEP 440 or semver constraints (e.g. '>=2.0,<3.0', '^3.23.0', 'v5.1.0')."
            )
            raise ValueError(msg)
        if self.import_root and not _is_valid_import_root(self.import_root):
            msg = f"import_root '{self.import_root}' must be a valid module or package root."
            raise ValueError(msg)
        return self

    @jezz_callable
    def infer_ecosystem(
        self,
        fallback: SupportedLanguage = SupportedLanguage.PYTHON,
    ) -> SupportedLanguage:
        """Return explicit ``ecosystem`` or infer Go/npm from unambiguous package syntax."""
        if self.ecosystem is not None:
            return self.ecosystem
        if self.package.startswith("@"):
            return SupportedLanguage.TYPESCRIPT
        if "/" in self.package and "." in self.package.split("/", maxsplit=1)[0]:
            return SupportedLanguage.GO
        return fallback

    @jezz_callable
    def pep508_spec(self) -> IdentifierStr:
        """Render the normalized dependency specifier string."""
        compact_ver = "".join(self.version_constraint.split())
        if not compact_ver:
            return self.package
        if compact_ver.startswith(_PEP440_OPERATOR_PREFIXES):
            return f"{self.package}{compact_ver}"
        return f"{self.package}@{compact_ver}"

    @jezz_callable
    def ecosystem_spec(self, ecosystem: IdentifierStr = "python") -> IdentifierStr:
        """Render ecosystem-native CLI install specifier for Python, TypeScript, Rust, or Go."""
        compact_ver = "".join(self.version_constraint.split())
        eco = (str(self.ecosystem) if self.ecosystem is not None else ecosystem).strip().lower()
        if eco == "python":
            if not compact_ver or compact_ver.startswith(_PEP440_OPERATOR_PREFIXES):
                return f"{self.package}{compact_ver}"
            cleaned = compact_ver.lstrip("^~v")
            return f"{self.package}>={cleaned}"
        if eco == "go":
            ver_tag = compact_ver.lstrip("=^~") if compact_ver else "latest"
            if ver_tag != "latest" and not ver_tag.startswith("v"):
                ver_tag = f"v{ver_tag}"
            return f"{self.package}@{ver_tag}"
        if not compact_ver:
            return self.package
        return f"{self.package}@{compact_ver.lstrip('=')}"

    @jezz_callable
    def effective_import_root(self) -> IdentifierStr:
        """Return explicit ``import_root`` or normalized ecosystem import root."""
        if self.import_root:
            return self.import_root
        if self.package.startswith("@types/"):
            return self.package.removeprefix("@types/")
        if self.package.startswith("@") or "/" in self.package:
            return self.package
        return re.sub(r"[-.]+", "_", self.package).lower()


@jezz_callable
def validate_engineering_prose(text: ShortText, *, field_label: IdentifierStr) -> None:
    """Reject Agile persona user stories, ceremony jargon, and Markdown checkbox theatre."""
    if _USER_STORY_THEATRE_RE.search(text) is not None:
        msg = (
            f"{field_label} contains Agile persona user-story theatre ('As a ..., I want ...'); "
            "state an objective system constraint instead."
        )
        raise ValueError(msg)
    if _CHECKBOX_THEATRE_RE.search(text) is not None:
        msg = (
            f"{field_label} contains Markdown checkbox theatre ('- [ ]'); "
            "task topology belongs in a verified BehaviorGraph."
        )
        raise ValueError(msg)
    if _AGILE_CEREMONY_RE.search(text) is not None:
        msg = f"{field_label} references prohibited Agile/SDD ceremony terminology."
        raise ValueError(msg)


class DomainInvariant(BaseModel):
    """Falsifiable domain obligation bound directly to a compiled AST verification symbol."""

    model_config = STRICT_FROZEN_CONFIG

    invariant_id: Annotated[
        str,
        Field(min_length=7, max_length=16, pattern=r"^INV-[0-9]{3,6}$"),
    ]
    statement: ShortText
    verification_symbol: IdentifierStr

    @model_validator(mode="after")
    def _enforce_falsifiable_wording(self) -> Self:
        validate_engineering_prose(self.statement, field_label=f"Invariant {self.invariant_id}")
        _validate_python_symbol_ref(
            self.verification_symbol,
            field_label=f"Invariant {self.invariant_id} verification_symbol",
        )
        if _WEASEL_MODAL_RE.search(self.statement) is not None:
            msg = (
                f"Invariant '{self.invariant_id}' uses non-normative weasel wording "
                "(should/might/ideally); state a strict falsifiable condition."
            )
            raise ValueError(msg)
        return self


class ModuleWriteScope(BaseModel):
    """Disjoint file-ownership envelope, contract surface, and upstream DAG dependencies."""

    model_config = STRICT_FROZEN_CONFIG

    scope_id: IdentifierStr
    owned_files: Annotated[
        frozenset[IdentifierStr],
        WIRE_FROZENSET_VALIDATOR,
        Field(min_length=1, max_length=256),
    ]
    contract_symbols: Annotated[
        tuple[IdentifierStr, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(min_length=1, max_length=128),
    ]
    depends_on: Annotated[
        frozenset[IdentifierStr],
        WIRE_FROZENSET_VALIDATOR,
        Field(max_length=64),
    ] = frozenset()

    @model_validator(mode="after")
    def _enforce_valid_contract_symbols(self) -> Self:
        if self.scope_id in self.depends_on:
            msg = f"ModuleWriteScope '{self.scope_id}' cannot declare self-dependency."
            raise ValueError(msg)
        for sym in self.contract_symbols:
            _validate_python_symbol_ref(
                sym,
                field_label=f"ModuleWriteScope '{self.scope_id}' contract_symbol",
            )
        return self


def _verify_disjoint_scopes(scopes: tuple[ModuleWriteScope, ...]) -> None:
    seen_ids: set[str] = set()
    claimed_files: dict[str, str] = {}
    for scope in scopes:
        if scope.scope_id in seen_ids:
            msg = f"Duplicate ModuleWriteScope id '{scope.scope_id}'."
            raise ValueError(msg)
        seen_ids.add(scope.scope_id)
        for file_path in sorted(scope.owned_files):
            prior_owner = claimed_files.get(file_path)
            if prior_owner is not None:
                msg = (
                    f"Write-scope collision on '{file_path}' between '{prior_owner}' "
                    f"and '{scope.scope_id}'."
                )
                raise ValueError(msg)
            claimed_files[file_path] = scope.scope_id


def _verify_scope_dependency_targets(scopes: tuple[ModuleWriteScope, ...]) -> None:
    known_ids = {s.scope_id for s in scopes}
    for scope in scopes:
        missing = sorted(scope.depends_on - known_ids)
        if missing:
            msg = f"ModuleWriteScope '{scope.scope_id}' depends_on unknown scope_id(s): {missing}."
            raise ValueError(msg)


@icontract.ensure(lambda scopes, result: sum(len(wave) for wave in result) == len(scopes))
@jezz_callable
def compute_scope_execution_waves(
    scopes: Annotated[Sequence[ModuleWriteScope], Field(min_length=1, max_length=64)],
) -> Annotated[tuple[tuple[ModuleWriteScope, ...], ...], Field(min_length=1, max_length=64)]:
    """Partition ``scopes`` into acyclic topological execution waves via Kahn peeling."""
    scope_tuple = tuple(scopes)
    _verify_disjoint_scopes(scope_tuple)
    _verify_scope_dependency_targets(scope_tuple)

    completed: set[str] = set()
    remaining: list[ModuleWriteScope] = list(scope_tuple)
    waves: list[tuple[ModuleWriteScope, ...]] = []
    budget = LoopBudget(max_steps=len(scope_tuple) + 2)

    while remaining:
        budget.tick()
        ready = tuple(s for s in remaining if s.depends_on <= completed)
        if not ready:
            stalled = [s.scope_id for s in remaining]
            msg = f"Cyclic ModuleWriteScope dependency graph detected across scopes: {stalled}."
            raise ValueError(msg)
        waves.append(ready)
        completed |= {s.scope_id for s in ready}
        remaining = [s for s in remaining if s.scope_id not in completed]

    return tuple(waves)


def _verify_unique_dependencies(deps: Sequence[DependencyRequirement], mandate_id: str) -> None:
    seen: set[tuple[SupportedLanguage | None, str, DependencyScope]] = set()
    for dep in deps:
        key = (dep.ecosystem, dep.package.lower(), dep.scope)
        if key in seen:
            msg = (
                f"Duplicate DependencyRequirement '{dep.package}' ({dep.scope}) in '{mandate_id}'."
            )
            raise ValueError(msg)
        seen.add(key)


class EngineeringMandate(BaseModel):
    """Phase I PCE artifact replacing prose PRDs, user stories, and tasks.md checklists."""

    model_config = STRICT_FROZEN_CONFIG

    mandate_id: Annotated[
        str,
        Field(min_length=5, max_length=64, pattern=r"^MND-[A-Za-z0-9_-]+$"),
    ]
    title: ShortText
    objective: ShortText
    invariants: Annotated[
        tuple[DomainInvariant, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(min_length=1, max_length=128),
    ]
    write_scopes: Annotated[
        tuple[ModuleWriteScope, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(min_length=1, max_length=64),
    ]
    required_dependencies: Annotated[
        tuple[DependencyRequirement, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=32),
    ] = ()
    consultation_targets: Annotated[
        tuple[ConsultTargetStr, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=32),
    ] = ()
    out_of_scope: Annotated[
        tuple[ShortText, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=64),
    ] = ()
    open_ambiguities: Annotated[
        tuple[ShortText, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=64),
    ] = ()

    @model_validator(mode="after")
    def _enforce_mandate_integrity(self) -> Self:
        validate_engineering_prose(self.title, field_label="Mandate.title")
        validate_engineering_prose(self.objective, field_label="Mandate.objective")

        seen_invariants: set[str] = set()
        for inv in self.invariants:
            if inv.invariant_id in seen_invariants:
                msg = f"Duplicate invariant_id '{inv.invariant_id}' in '{self.mandate_id}'."
                raise ValueError(msg)
            seen_invariants.add(inv.invariant_id)

        _verify_unique_dependencies(self.required_dependencies, self.mandate_id)
        compute_scope_execution_waves(self.write_scopes)
        return self

    @property
    def requires_elicitation(self) -> bool:
        """Return True iff unresolved ambiguities require halting at BHV-02 ELICIT."""
        return len(self.open_ambiguities) > 0

    @jezz_callable
    def authorized_files(self) -> frozenset[IdentifierStr]:
        """Return the union of all file paths authorized for mutation across write scopes."""
        combined: set[str] = set()
        for scope in self.write_scopes:
            combined |= set(scope.owned_files)
        return frozenset(combined)

    @jezz_callable
    def authorized_manifest_files(self) -> frozenset[IdentifierStr]:
        """Return manifest/lockfile paths authorized when ``required_dependencies`` is non-empty."""
        if not self.required_dependencies:
            return frozenset()
        return _AUTHORIZED_MANIFEST_FILES

    @jezz_callable
    def execution_waves(
        self,
    ) -> Annotated[tuple[tuple[ModuleWriteScope, ...], ...], Field(min_length=1, max_length=64)]:
        """Return the validated topological execution waves across ``self.write_scopes``."""
        return compute_scope_execution_waves(self.write_scopes)
