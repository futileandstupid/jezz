"""Closed symbolic expression, statement, obligation, and proof schemas for SMT discharge."""

import hashlib
from collections.abc import Sequence
from enum import StrEnum
from typing import Annotated, Final, Literal, Self

import icontract
from pydantic import BaseModel, Field, StrictInt, model_validator

from jezz.codes.permits import SealDigestStr
from jezz.codes.polyglot_ir import SupportedLanguage
from jezz.guards import (
    STRICT_FROZEN_CONFIG,
    WIRE_TUPLE_VALIDATOR,
    BoundedText,
    FiniteFloat,
    IdentifierStr,
    LineNumber,
    NonNegativeInt,
    ShortText,
    SourceCodeText,
    jezz_callable,
)

SMT_SCHEMA_VERSION: Final[str] = "1.0"

type SymbolicIntLiteral = Annotated[StrictInt, Field(ge=-(10**18), le=10**18)]
type BoundedSolverTimeoutMs = Annotated[StrictInt, Field(ge=10, le=30_000)]


class SmtSort(StrEnum):
    """Supported quantifier-free SMT-LIB background theory sorts."""

    INT = "int"
    REAL = "real"
    BOOL = "bool"
    STRING = "string"


class SmtUnaryOp(StrEnum):
    """Unary operators lowered deterministically into Z3 terms."""

    NEG = "neg"
    NOT = "not"
    ABS = "abs"


class SmtBinaryOp(StrEnum):
    """Binary arithmetic, comparison, boolean, and string operators lowered into Z3."""

    ADD = "add"
    SUB = "sub"
    MUL = "mul"
    FLOORDIV = "floordiv"
    TRUEDIV = "truediv"
    MOD = "mod"
    EQ = "eq"
    NE = "ne"
    LT = "lt"
    LE = "le"
    GT = "gt"
    GE = "ge"
    AND = "and"
    OR = "or"
    IMPLIES = "implies"
    STR_CONCAT = "str_concat"
    STR_PREFIX = "str_prefix"


class SymbolicVar(BaseModel):
    """Typed symbolic input variable bound inside a ``ContractObligation``."""

    model_config = STRICT_FROZEN_CONFIG

    name: IdentifierStr
    sort: SmtSort


class SmtConstInt(BaseModel):
    """Exact bounded integer constant term."""

    model_config = STRICT_FROZEN_CONFIG

    kind: Literal["const_int"] = "const_int"
    value: SymbolicIntLiteral


class SmtConstReal(BaseModel):
    """Finite rational/floating-point constant term."""

    model_config = STRICT_FROZEN_CONFIG

    kind: Literal["const_real"] = "const_real"
    value: FiniteFloat


class SmtConstBool(BaseModel):
    """Boolean constant term."""

    model_config = STRICT_FROZEN_CONFIG

    kind: Literal["const_bool"] = "const_bool"
    value: bool


class SmtConstStr(BaseModel):
    """Bounded UTF-8 string constant term."""

    model_config = STRICT_FROZEN_CONFIG

    kind: Literal["const_str"] = "const_str"
    value: BoundedText


class SmtVar(BaseModel):
    """Symbolic variable reference carrying its static ``SmtSort``."""

    model_config = STRICT_FROZEN_CONFIG

    kind: Literal["var"] = "var"
    name: IdentifierStr
    sort: SmtSort


class SmtLen(BaseModel):
    """String length operator ``len(s)`` / ``s.length`` / ``s.len()`` returning ``INT``."""

    model_config = STRICT_FROZEN_CONFIG

    kind: Literal["str_len"] = "str_len"
    operand: SmtExpr


class SmtUnary(BaseModel):
    """Unary expression node over ``SmtExpr``."""

    model_config = STRICT_FROZEN_CONFIG

    kind: Literal["unary"] = "unary"
    op: SmtUnaryOp
    operand: SmtExpr


class SmtBinary(BaseModel):
    """Binary expression node over ``SmtExpr``."""

    model_config = STRICT_FROZEN_CONFIG

    kind: Literal["binary"] = "binary"
    op: SmtBinaryOp
    left: SmtExpr
    right: SmtExpr


class SmtIte(BaseModel):
    """Conditional ternary expression ``ite(cond, then_expr, else_expr)``."""

    model_config = STRICT_FROZEN_CONFIG

    kind: Literal["ite"] = "ite"
    cond: SmtExpr
    then_expr: SmtExpr
    else_expr: SmtExpr


type SmtExpr = Annotated[
    SmtConstInt
    | SmtConstReal
    | SmtConstBool
    | SmtConstStr
    | SmtVar
    | SmtLen
    | SmtUnary
    | SmtBinary
    | SmtIte,
    Field(discriminator="kind"),
]


class SmtAssign(BaseModel):
    """Pure local symbolic variable binding ``target := value``."""

    model_config = STRICT_FROZEN_CONFIG

    kind: Literal["assign"] = "assign"
    target: IdentifierStr
    value: SmtExpr


class SmtAssume(BaseModel):
    """Path guard narrowing normal execution to inputs satisfying ``cond``."""

    model_config = STRICT_FROZEN_CONFIG

    kind: Literal["assume"] = "assume"
    cond: SmtExpr


class SmtAssert(BaseModel):
    """Inline Floyd-Hoare assertion requiring ``cond`` to hold on every reaching path."""

    model_config = STRICT_FROZEN_CONFIG

    kind: Literal["assert_hold"] = "assert_hold"
    cond: SmtExpr


class SmtReturn(BaseModel):
    """Function return statement binding ``result := value`` along the active path."""

    model_config = STRICT_FROZEN_CONFIG

    kind: Literal["return"] = "return"
    value: SmtExpr


class SmtBranch(BaseModel):
    """Structured conditional statement ``if cond { then_body } else { else_body }``."""

    model_config = STRICT_FROZEN_CONFIG

    kind: Literal["branch"] = "branch"
    cond: SmtExpr
    then_body: Annotated[
        tuple[SmtStmt, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=128),
    ] = ()
    else_body: Annotated[
        tuple[SmtStmt, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=128),
    ] = ()


type SmtStmt = Annotated[
    SmtAssign | SmtAssume | SmtAssert | SmtReturn | SmtBranch,
    Field(discriminator="kind"),
]


class ContractObligation(BaseModel):
    """Single polyglot schema or callable verification condition lowered from AST/CST."""

    model_config = STRICT_FROZEN_CONFIG

    obligation_id: IdentifierStr
    file_path: IdentifierStr
    line: LineNumber = 1
    language: SupportedLanguage
    symbol: IdentifierStr
    variables: Annotated[
        tuple[SymbolicVar, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=64),
    ] = ()
    preconditions: Annotated[
        tuple[SmtExpr, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=64),
    ] = ()
    postconditions: Annotated[
        tuple[SmtExpr, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=64),
    ] = ()
    assertion_count: NonNegativeInt = 0
    body_stmts: (
        Annotated[
            tuple[SmtStmt, ...],
            WIRE_TUPLE_VALIDATOR,
            Field(max_length=256),
        ]
        | None
    ) = None
    has_opaque_postcondition: bool = False
    opaque_reason: BoundedText = ""


class ContractProofStatus(StrEnum):
    """Sound 4-state verdict produced by Z3 contract consistency and WP verification."""

    PROVED_DEDUCTIVE = "proved_deductive"
    BOUNDED_BY_PBT = "bounded_by_pbt"
    COUNTEREXAMPLE_FOUND = "counterexample_found"
    VACUOUS_CONTRACT = "vacuous_contract"


class ObligationProofResult(BaseModel):
    """Z3 verification outcome and optional concrete counterexample for one obligation."""

    model_config = STRICT_FROZEN_CONFIG

    obligation_id: IdentifierStr
    file_path: IdentifierStr
    line: LineNumber = 1
    language: SupportedLanguage
    symbol: IdentifierStr
    status: ContractProofStatus
    precondition_count: NonNegativeInt = 0
    postcondition_count: NonNegativeInt = 0
    counterexample: Annotated[dict[IdentifierStr, BoundedText], Field(max_length=64)] = Field(
        default_factory=dict
    )
    solver_duration_ms: Annotated[FiniteFloat, Field(ge=0.0, le=60_000.0)] = 0.0
    detail: ShortText


@icontract.ensure(lambda result: result.startswith("sha256:") and len(result) == 71)
@jezz_callable
def compute_smt_proof_digest(
    results: Annotated[Sequence[ObligationProofResult], Field(max_length=50_000)],
) -> SealDigestStr:
    """Compute canonical SHA-256 commitment over sorted ``(obligation_id, status)`` pairs."""
    ordered = sorted(results, key=lambda item: item.obligation_id)
    payload = "\x00".join(f"{item.obligation_id}:{item.status.value}" for item in ordered)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


class SmtProofReport(BaseModel):
    """Whole-workspace or scoped polyglot Z3 contract verification ledger."""

    model_config = STRICT_FROZEN_CONFIG

    schema_version: IdentifierStr = SMT_SCHEMA_VERSION
    total_obligations: NonNegativeInt
    proved_deductive_count: NonNegativeInt
    bounded_by_pbt_count: NonNegativeInt
    vacuous_count: NonNegativeInt
    counterexample_count: NonNegativeInt
    has_failures: bool
    proof_digest: SealDigestStr
    results: Annotated[
        tuple[ObligationProofResult, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=50_000),
    ] = ()

    @model_validator(mode="after")
    def _validate_counts_and_digest(self) -> Self:
        expected_total = (
            self.proved_deductive_count
            + self.bounded_by_pbt_count
            + self.vacuous_count
            + self.counterexample_count
        )
        if self.total_obligations != len(self.results) or self.total_obligations != expected_total:
            msg = "SmtProofReport summary counts do not match results tuple cardinality."
            raise ValueError(msg)
        expected_fail = (self.vacuous_count + self.counterexample_count) > 0
        if self.has_failures != expected_fail:
            msg = "SmtProofReport.has_failures contradicts vacuous/counterexample counts."
            raise ValueError(msg)
        if self.proof_digest != compute_smt_proof_digest(self.results):
            msg = "SmtProofReport.proof_digest mismatches canonical SHA-256 obligation digest."
            raise ValueError(msg)
        return self

    @jezz_callable
    def to_markdown(self) -> SourceCodeText:
        """Render a structured Markdown summary of polyglot SMT contract obligations."""
        verdict = "FAILED" if self.has_failures else "VERIFIED"
        lines = [
            f"# Polyglot SMT Contract Proof Report (`{verdict}`)",
            f"- **Total Obligations:** `{self.total_obligations}`",
            f"- **Proved Deductive (`UNSAT`):** `{self.proved_deductive_count}`",
            f"- **Bounded by PBT (`SAT` contract, opaque body):** `{self.bounded_by_pbt_count}`",
            f"- **Vacuous Contracts (`UNSAT(P)` / `UNSAT(P ∧ Q)`):** `{self.vacuous_count}`",
            f"- **Counterexamples Found (`SAT` witness):** `{self.counterexample_count}`",
            f"- **Proof Digest:** `{self.proof_digest}`",
        ]
        if self.results:
            lines.extend(
                [
                    "",
                    "## Obligation Ledger",
                    "| Symbol | Language | Status | Pre / Post | Detail |",
                    "| :--- | :---: | :---: | :---: | :--- |",
                ]
            )
            for item in self.results[:300]:
                witness = f" Counterexample: `{item.counterexample}`" if item.counterexample else ""
                lines.append(
                    f"| `{item.symbol}` (`{item.file_path}:{item.line}`) | `{item.language}` | "
                    f"**`{item.status.upper()}`** | {item.precondition_count}/"
                    f"{item.postcondition_count} | {item.detail}{witness} |"
                )
        return "\n".join(lines)


SmtLen.model_rebuild()
SmtUnary.model_rebuild()
SmtBinary.model_rebuild()
SmtIte.model_rebuild()
SmtAssign.model_rebuild()
SmtAssume.model_rebuild()
SmtAssert.model_rebuild()
SmtReturn.model_rebuild()
SmtBranch.model_rebuild()
ContractObligation.model_rebuild()
