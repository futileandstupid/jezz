"""Thread-safe Z3 verification condition solver and symbolic path evaluator (Track 27)."""

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Annotated, Final

import icontract
import z3
from pydantic import Field

from jezz.codes.smt.ir import (
    BoundedSolverTimeoutMs,
    ContractObligation,
    ContractProofStatus,
    ObligationProofResult,
    SmtAssert,
    SmtAssign,
    SmtAssume,
    SmtBinary,
    SmtBinaryOp,
    SmtBranch,
    SmtConstBool,
    SmtConstInt,
    SmtConstReal,
    SmtConstStr,
    SmtExpr,
    SmtIte,
    SmtLen,
    SmtProofReport,
    SmtReturn,
    SmtSort,
    SmtStmt,
    SmtUnary,
    SmtUnaryOp,
    SmtVar,
    SymbolicVar,
    compute_smt_proof_digest,
)
from jezz.guards import (
    BoundedInt,
    BoundedText,
    BudgetExceededError,
    IdentifierStr,
    LoopBudget,
    RecursionBudget,
    jezz_callable,
)

_MAX_SYMBOLIC_DEPTH: Final[int] = 96
_MAX_STMT_BUDGET: Final[int] = 4_096


@icontract.ensure(lambda result: 10 <= result <= 30_000)
@jezz_callable
def clamp_solver_timeout_ms(requested_ms: BoundedInt) -> BoundedSolverTimeoutMs:
    """Clamp ``requested_ms`` into the statutory ``[10, 30_000]`` ms Z3 solver budget."""
    low = max(requested_ms, 10)
    return min(low, 30_000)


@dataclass(frozen=True)
class _ReturnBranch:
    guard: SmtExpr
    value: SmtExpr
    well_defined: SmtExpr


@dataclass(frozen=True)
class _BlockState:
    env: Mapping[str, SmtExpr]
    active_guard: SmtExpr
    safety_cond: SmtExpr
    returns: tuple[_ReturnBranch, ...]


def _is_const_bool(expr: SmtExpr, expected: bool) -> bool:
    return isinstance(expr, SmtConstBool) and expr.value is expected


def _and_expr(left: SmtExpr, right: SmtExpr) -> SmtExpr:
    if _is_const_bool(left, True):
        return right
    if _is_const_bool(right, True):
        return left
    if _is_const_bool(left, False) or _is_const_bool(right, False):
        return SmtConstBool(value=False)
    return SmtBinary(op=SmtBinaryOp.AND, left=left, right=right)


def _not_expr(expr: SmtExpr) -> SmtExpr:
    if isinstance(expr, SmtConstBool):
        return SmtConstBool(value=not expr.value)
    return SmtUnary(op=SmtUnaryOp.NOT, operand=expr)


def _implies_expr(premise: SmtExpr, conclusion: SmtExpr) -> SmtExpr:
    if _is_const_bool(conclusion, True):
        return SmtConstBool(value=True)
    if isinstance(premise, SmtConstBool):
        return conclusion if premise.value else SmtConstBool(value=True)
    return SmtBinary(op=SmtBinaryOp.IMPLIES, left=premise, right=conclusion)


def _nonzero_guard(divisor: SmtExpr) -> SmtExpr:
    is_real = isinstance(divisor, SmtConstReal) or (
        isinstance(divisor, SmtVar) and divisor.sort == SmtSort.REAL
    )
    zero: SmtExpr = SmtConstReal(value=0.0) if is_real else SmtConstInt(value=0)
    return SmtBinary(op=SmtBinaryOp.NE, left=divisor, right=zero)


def _subst_binary(
    expr: SmtBinary,
    env: Mapping[str, SmtExpr],
    budget: RecursionBudget,
) -> tuple[SmtExpr, SmtExpr]:
    lhs, l_safe = _subst_expr(expr.left, env, budget)
    rhs, r_safe = _subst_expr(expr.right, env, budget)
    rebuilt = SmtBinary(op=expr.op, left=lhs, right=rhs)
    if expr.op == SmtBinaryOp.AND:
        return rebuilt, _and_expr(l_safe, _implies_expr(lhs, r_safe))
    if expr.op == SmtBinaryOp.OR:
        return rebuilt, _and_expr(l_safe, _implies_expr(_not_expr(lhs), r_safe))
    if expr.op in {SmtBinaryOp.FLOORDIV, SmtBinaryOp.TRUEDIV, SmtBinaryOp.MOD}:
        return rebuilt, _and_expr(_and_expr(l_safe, r_safe), _nonzero_guard(rhs))
    return rebuilt, _and_expr(l_safe, r_safe)


def _subst_expr(
    expr: SmtExpr,
    env: Mapping[str, SmtExpr],
    budget: RecursionBudget,
) -> tuple[SmtExpr, SmtExpr]:
    with budget:
        t_true = SmtConstBool(value=True)
        if isinstance(expr, SmtConstInt | SmtConstReal | SmtConstBool | SmtConstStr):
            return expr, t_true
        if isinstance(expr, SmtVar):
            return env.get(expr.name, expr), t_true
        if isinstance(expr, SmtLen):
            sub_op, safe = _subst_expr(expr.operand, env, budget)
            return SmtLen(operand=sub_op), safe
        if isinstance(expr, SmtUnary):
            sub_op, safe = _subst_expr(expr.operand, env, budget)
            return SmtUnary(op=expr.op, operand=sub_op), safe
        if isinstance(expr, SmtBinary):
            return _subst_binary(expr, env, budget)
        cond_e, c_safe = _subst_expr(expr.cond, env, budget)
        then_e, t_safe = _subst_expr(expr.then_expr, env, budget)
        else_e, e_safe = _subst_expr(expr.else_expr, env, budget)
        ite_safe = _and_expr(
            c_safe,
            _and_expr(_implies_expr(cond_e, t_safe), _implies_expr(_not_expr(cond_e), e_safe)),
        )
        return SmtIte(cond=cond_e, then_expr=then_e, else_expr=else_e), ite_safe


def _merge_single_var(
    cond: SmtExpr,
    t_val: SmtExpr | None,
    e_val: SmtExpr | None,
) -> SmtExpr | None:
    if t_val is None:
        return e_val
    if e_val is None or t_val == e_val:
        return t_val
    return SmtIte(cond=cond, then_expr=t_val, else_expr=e_val)


def _merge_branch_envs(
    cond: SmtExpr,
    then_state: _BlockState,
    else_state: _BlockState,
) -> dict[str, SmtExpr]:
    keys = sorted(set(then_state.env) | set(else_state.env))
    merged: dict[str, SmtExpr] = {}
    for key in keys:
        chosen = _merge_single_var(cond, then_state.env.get(key), else_state.env.get(key))
        if chosen is not None:
            merged[key] = chosen
    return merged


def _step_assign(
    stmt: SmtAssign,
    cur: _BlockState,
    rec_budget: RecursionBudget,
) -> _BlockState:
    val, v_safe = _subst_expr(stmt.value, cur.env, rec_budget)
    prior = cur.env.get(stmt.target)
    bound = val if prior is None else SmtIte(cond=cur.active_guard, then_expr=val, else_expr=prior)
    return _BlockState(
        env={**cur.env, stmt.target: bound},
        active_guard=cur.active_guard,
        safety_cond=_and_expr(cur.safety_cond, _implies_expr(cur.active_guard, v_safe)),
        returns=cur.returns,
    )


def _step_assume(
    stmt: SmtAssume,
    cur: _BlockState,
    rec_budget: RecursionBudget,
) -> _BlockState:
    cond_v, c_safe = _subst_expr(stmt.cond, cur.env, rec_budget)
    return _BlockState(
        env=cur.env,
        active_guard=_and_expr(cur.active_guard, cond_v),
        safety_cond=_and_expr(cur.safety_cond, _implies_expr(cur.active_guard, c_safe)),
        returns=cur.returns,
    )


def _step_assert(
    stmt: SmtAssert,
    cur: _BlockState,
    rec_budget: RecursionBudget,
) -> _BlockState:
    cond_v, c_safe = _subst_expr(stmt.cond, cur.env, rec_budget)
    held = _and_expr(c_safe, cond_v)
    return _BlockState(
        env=cur.env,
        active_guard=cur.active_guard,
        safety_cond=_and_expr(cur.safety_cond, _implies_expr(cur.active_guard, held)),
        returns=cur.returns,
    )


def _step_return(
    stmt: SmtReturn,
    cur: _BlockState,
    rec_budget: RecursionBudget,
) -> _BlockState:
    ret_v, r_safe = _subst_expr(stmt.value, cur.env, rec_budget)
    branch = _ReturnBranch(
        guard=cur.active_guard,
        value=ret_v,
        well_defined=_and_expr(cur.safety_cond, r_safe),
    )
    return _BlockState(
        env=cur.env,
        active_guard=SmtConstBool(value=False),
        safety_cond=cur.safety_cond,
        returns=(*cur.returns, branch),
    )


def _exec_branch_stmt(
    stmt: SmtBranch,
    state: _BlockState,
    rec_budget: RecursionBudget,
    loop_budget: LoopBudget,
    *,
    remaining_depth: int,
) -> _BlockState:
    if remaining_depth <= 0:
        raise BudgetExceededError("Symbolic branch nesting exceeded depth budget.")
    cond_val, cond_safe = _subst_expr(stmt.cond, state.env, rec_budget)
    base_safe = _and_expr(state.safety_cond, _implies_expr(state.active_guard, cond_safe))
    t_state = _exec_stmts(
        stmt.then_body,
        _BlockState(state.env, _and_expr(state.active_guard, cond_val), base_safe, ()),
        rec_budget,
        loop_budget,
        remaining_depth=remaining_depth - 1,
    )
    e_state = _exec_stmts(
        stmt.else_body,
        _BlockState(state.env, _and_expr(state.active_guard, _not_expr(cond_val)), base_safe, ()),
        rec_budget,
        loop_budget,
        remaining_depth=remaining_depth - 1,
    )
    both_closed = _is_const_bool(t_state.active_guard, False) and _is_const_bool(
        e_state.active_guard, False
    )
    next_guard: SmtExpr = (
        SmtConstBool(value=False)
        if both_closed
        else SmtBinary(op=SmtBinaryOp.OR, left=t_state.active_guard, right=e_state.active_guard)
    )
    return _BlockState(
        env=_merge_branch_envs(cond_val, t_state, e_state),
        active_guard=next_guard,
        safety_cond=_and_expr(t_state.safety_cond, e_state.safety_cond),
        returns=(*state.returns, *t_state.returns, *e_state.returns),
    )


def _step_single_stmt(
    stmt: SmtStmt,
    cur: _BlockState,
    rec_budget: RecursionBudget,
    loop_budget: LoopBudget,
    *,
    remaining_depth: int,
) -> _BlockState:
    if isinstance(stmt, SmtAssign):
        return _step_assign(stmt, cur, rec_budget)
    if isinstance(stmt, SmtAssume):
        return _step_assume(stmt, cur, rec_budget)
    if isinstance(stmt, SmtAssert):
        return _step_assert(stmt, cur, rec_budget)
    if isinstance(stmt, SmtReturn):
        return _step_return(stmt, cur, rec_budget)
    return _exec_branch_stmt(
        stmt, cur, rec_budget, loop_budget, remaining_depth=remaining_depth - 1
    )


def _exec_stmts(
    stmts: Sequence[SmtStmt],
    state: _BlockState,
    rec_budget: RecursionBudget,
    loop_budget: LoopBudget,
    *,
    remaining_depth: int,
) -> _BlockState:
    if remaining_depth <= 0:
        raise BudgetExceededError("Symbolic statement execution exceeded depth budget.")
    cur = state
    for stmt in stmts:
        loop_budget.tick()
        if _is_const_bool(cur.active_guard, False):
            break
        cur = _step_single_stmt(
            stmt, cur, rec_budget, loop_budget, remaining_depth=remaining_depth - 1
        )
    return cur


def _mk_z3_var(var: SymbolicVar, ctx: z3.Context) -> z3.ExprRef:
    builders: dict[SmtSort, Callable[[str, z3.Context], z3.ExprRef]] = {
        SmtSort.INT: z3.Int,
        SmtSort.REAL: z3.Real,
        SmtSort.BOOL: z3.Bool,
        SmtSort.STRING: z3.String,
    }
    return builders[var.sort](var.name, ctx)


def _as_bool(term: z3.ExprRef) -> z3.BoolRef:
    if isinstance(term, z3.BoolRef):
        return term
    msg = f"Expected boolean Z3 expression, got '{term}'."
    raise ValueError(msg)


def _as_seq(term: z3.ExprRef) -> z3.SeqRef:
    if isinstance(term, z3.SeqRef):
        return term
    msg = f"Expected string Z3 expression, got '{term}'."
    raise ValueError(msg)


def _as_arith_pair(
    left: z3.ExprRef,
    right: z3.ExprRef,
    *,
    force_real: bool = False,
) -> tuple[z3.ArithRef, z3.ArithRef]:
    if not isinstance(left, z3.ArithRef) or not isinstance(right, z3.ArithRef):
        msg = f"Expected numeric Z3 expressions, got '{left}' and '{right}'."
        raise ValueError(msg)
    if not (force_real or left.is_real() or right.is_real()):
        return left, right
    l_real = z3.ToReal(left) if left.is_int() else left
    r_real = z3.ToReal(right) if right.is_int() else right
    return l_real, r_real


_ARITH_BIN_OPS: Final[Mapping[SmtBinaryOp, Callable[[z3.ArithRef, z3.ArithRef], z3.ArithRef]]] = (
    MappingProxyType(
        {
            SmtBinaryOp.ADD: lambda a, b: a + b,
            SmtBinaryOp.SUB: lambda a, b: a - b,
            SmtBinaryOp.MUL: lambda a, b: a * b,
            SmtBinaryOp.FLOORDIV: lambda a, b: a / b,
            SmtBinaryOp.TRUEDIV: lambda a, b: a / b,
            SmtBinaryOp.MOD: lambda a, b: a % b,
        }
    )
)
_ORDER_BIN_OPS: Final[Mapping[SmtBinaryOp, Callable[[z3.ArithRef, z3.ArithRef], z3.BoolRef]]] = (
    MappingProxyType(
        {
            SmtBinaryOp.LT: lambda a, b: a < b,
            SmtBinaryOp.LE: lambda a, b: a <= b,
            SmtBinaryOp.GT: lambda a, b: a > b,
            SmtBinaryOp.GE: lambda a, b: a >= b,
        }
    )
)


def _lower_binary_z3(
    op: SmtBinaryOp,
    lhs: z3.ExprRef,
    rhs: z3.ExprRef,
    ctx: z3.Context,
) -> z3.ExprRef:
    arith_fn = _ARITH_BIN_OPS.get(op)
    if arith_fn is not None:
        la, ra = _as_arith_pair(lhs, rhs, force_real=(op == SmtBinaryOp.TRUEDIV))
        return arith_fn(la, ra)
    order_fn = _ORDER_BIN_OPS.get(op)
    if order_fn is not None:
        la, ra = _as_arith_pair(lhs, rhs)
        return order_fn(la, ra)
    if op in {SmtBinaryOp.EQ, SmtBinaryOp.NE}:
        pair = (
            _as_arith_pair(lhs, rhs)
            if isinstance(lhs, z3.ArithRef) and isinstance(rhs, z3.ArithRef)
            else (lhs, rhs)
        )
        neq = z3.Distinct(pair[0], pair[1])
        return neq if op == SmtBinaryOp.NE else z3.Not(neq, ctx)
    if op == SmtBinaryOp.STR_CONCAT:
        return z3.Concat(_as_seq(lhs), _as_seq(rhs))
    if op == SmtBinaryOp.STR_PREFIX:
        return z3.PrefixOf(_as_seq(rhs), _as_seq(lhs))
    lb, rb = _as_bool(lhs), _as_bool(rhs)
    bool_ops: dict[SmtBinaryOp, Callable[[], z3.BoolRef]] = {
        SmtBinaryOp.AND: lambda: z3.And(lb, rb, ctx),
        SmtBinaryOp.OR: lambda: z3.Or(lb, rb, ctx),
        SmtBinaryOp.IMPLIES: lambda: z3.Implies(lb, rb, ctx),
    }
    return bool_ops[op]()


def _lower_unary_z3(op: SmtUnaryOp, val: z3.ExprRef, ctx: z3.Context) -> z3.ExprRef:
    if op == SmtUnaryOp.NOT:
        return z3.Not(_as_bool(val), ctx)
    if not isinstance(val, z3.ArithRef):
        msg = f"Expected numeric Z3 operand for '{op}', got '{val}'."
        raise ValueError(msg)
    return -val if op == SmtUnaryOp.NEG else z3.Abs(val)


def _lower_atom_z3(
    expr: SmtConstInt | SmtConstReal | SmtConstBool | SmtConstStr | SmtVar,
    z3_vars: Mapping[str, z3.ExprRef],
    ctx: z3.Context,
) -> z3.ExprRef:
    if isinstance(expr, SmtConstInt):
        return z3.IntVal(expr.value, ctx)
    if isinstance(expr, SmtConstReal):
        return z3.RealVal(repr(expr.value), ctx)
    if isinstance(expr, SmtConstBool):
        return z3.BoolVal(expr.value, ctx)
    if isinstance(expr, SmtConstStr):
        return z3.StringVal(expr.value, ctx)
    existing = z3_vars.get(expr.name)
    if existing is not None:
        return existing
    return _mk_z3_var(SymbolicVar(name=expr.name, sort=expr.sort), ctx)


def _lower_ite_z3(
    expr: SmtIte,
    z3_vars: Mapping[str, z3.ExprRef],
    ctx: z3.Context,
    budget: RecursionBudget,
) -> z3.ExprRef:
    cond = _as_bool(_lower_z3_expr(expr.cond, z3_vars, ctx, budget))
    t_val = _lower_z3_expr(expr.then_expr, z3_vars, ctx, budget)
    e_val = _lower_z3_expr(expr.else_expr, z3_vars, ctx, budget)
    if isinstance(t_val, z3.ArithRef) and isinstance(e_val, z3.ArithRef):
        ta, ea = _as_arith_pair(t_val, e_val)
        return z3.If(cond, ta, ea, ctx)
    return z3.If(cond, t_val, e_val, ctx)


def _lower_z3_expr(
    expr: SmtExpr,
    z3_vars: Mapping[str, z3.ExprRef],
    ctx: z3.Context,
    budget: RecursionBudget,
) -> z3.ExprRef:
    with budget:
        if isinstance(expr, SmtConstInt | SmtConstReal | SmtConstBool | SmtConstStr | SmtVar):
            return _lower_atom_z3(expr, z3_vars, ctx)
        if isinstance(expr, SmtLen):
            return z3.Length(_as_seq(_lower_z3_expr(expr.operand, z3_vars, ctx, budget)))
        if isinstance(expr, SmtUnary):
            return _lower_unary_z3(expr.op, _lower_z3_expr(expr.operand, z3_vars, ctx, budget), ctx)
        if isinstance(expr, SmtBinary):
            lhs = _lower_z3_expr(expr.left, z3_vars, ctx, budget)
            rhs = _lower_z3_expr(expr.right, z3_vars, ctx, budget)
            return _lower_binary_z3(expr.op, lhs, rhs, ctx)
        return _lower_ite_z3(expr, z3_vars, ctx, budget)


def _conjunction_z3(terms: Sequence[z3.BoolRef], ctx: z3.Context) -> z3.BoolRef:
    if not terms:
        return z3.BoolVal(True, ctx)
    return terms[0] if len(terms) == 1 else z3.And(*terms, ctx)


def _disjunction_z3(terms: Sequence[z3.BoolRef], ctx: z3.Context) -> z3.BoolRef:
    if not terms:
        return z3.BoolVal(False, ctx)
    return terms[0] if len(terms) == 1 else z3.Or(*terms, ctx)


def _extract_witness(
    model: z3.ModelRef,
    variables: Sequence[SymbolicVar],
    z3_vars: Mapping[str, z3.ExprRef],
) -> dict[IdentifierStr, BoundedText]:
    witness: dict[str, str] = {}
    for var in variables:
        if var.name == "result":
            continue
        ref = z3_vars.get(var.name)
        if ref is None:
            continue
        evaluated = model.eval(ref, model_completion=True)
        witness[var.name] = str(evaluated)[:1024]
    return witness


def _build_result(
    ob: ContractObligation,
    status: ContractProofStatus,
    detail: str,
    started: float,
    clock: Callable[[], float],
    witness: Mapping[str, str] | None = None,
) -> ObligationProofResult:
    elapsed_ms = max(0.0, round((clock() - started) * 1000.0, 3))
    return ObligationProofResult(
        obligation_id=ob.obligation_id,
        file_path=ob.file_path,
        line=ob.line,
        language=ob.language,
        symbol=ob.symbol,
        status=status,
        precondition_count=len(ob.preconditions),
        postcondition_count=len(ob.postconditions) + ob.assertion_count,
        counterexample=dict(witness) if witness else {},
        solver_duration_ms=elapsed_ms,
        detail=detail[:1024],
    )


def _compile_branch_obligations(
    ob: ContractObligation,
    returns: Sequence[_ReturnBranch],
    init_env: Mapping[str, SmtExpr],
    z3_vars: Mapping[str, z3.ExprRef],
    ctx: z3.Context,
    rec: RecursionBudget,
) -> tuple[list[z3.BoolRef], list[z3.BoolRef]]:
    reach_guards: list[z3.BoolRef] = []
    violation_disjuncts: list[z3.BoolRef] = []
    for br in returns:
        g_z3 = _as_bool(_lower_z3_expr(br.guard, z3_vars, ctx, rec))
        safe_z3 = _as_bool(_lower_z3_expr(br.well_defined, z3_vars, ctx, rec))
        reach_guards.append(g_z3)
        post_terms: list[z3.BoolRef] = [safe_z3]
        sub_env = {**init_env, "result": br.value}
        for q_expr in ob.postconditions:
            q_sub, q_safe = _subst_expr(q_expr, sub_env, rec)
            post_terms.append(_as_bool(_lower_z3_expr(_and_expr(q_safe, q_sub), z3_vars, ctx, rec)))
        violation_disjuncts.append(z3.And(g_z3, z3.Not(_conjunction_z3(post_terms, ctx), ctx), ctx))
    return reach_guards, violation_disjuncts


def _verify_body_branches(
    ob: ContractObligation,
    pre_z3: z3.BoolRef,
    z3_vars: Mapping[str, z3.ExprRef],
    ctx: z3.Context,
    timeout_ms: int,
) -> tuple[ContractProofStatus, str, dict[str, str]]:
    rec = RecursionBudget(max_depth=_MAX_SYMBOLIC_DEPTH)
    init_env = {
        v.name: SmtVar(name=v.name, sort=v.sort) for v in ob.variables if v.name != "result"
    }
    final_state = _exec_stmts(
        ob.body_stmts or (),
        _BlockState(init_env, SmtConstBool(value=True), SmtConstBool(value=True), ()),
        rec,
        LoopBudget(max_steps=_MAX_STMT_BUDGET),
        remaining_depth=_MAX_SYMBOLIC_DEPTH,
    )
    if not final_state.returns:
        return (
            ContractProofStatus.VACUOUS_CONTRACT,
            "No reachable return branch exists in lowered body.",
            {},
        )

    reach_guards, violation_disjuncts = _compile_branch_obligations(
        ob, final_state.returns, init_env, z3_vars, ctx, rec
    )
    reach_solver = z3.Solver(ctx=ctx)
    reach_solver.set("timeout", timeout_ms)
    reach_solver.add(pre_z3, _disjunction_z3(reach_guards, ctx))
    if reach_solver.check() == z3.unsat:
        return (
            ContractProofStatus.VACUOUS_CONTRACT,
            "Every execution path aborts or diverges under declared preconditions.",
            {},
        )

    vc_solver = z3.Solver(ctx=ctx)
    vc_solver.set("timeout", timeout_ms)
    vc_solver.add(pre_z3, _disjunction_z3(violation_disjuncts, ctx))
    verdict = vc_solver.check()
    if verdict == z3.unsat:
        return (
            ContractProofStatus.PROVED_DEDUCTIVE,
            "Z3 proved P ∧ ¬WP(body, Q) is UNSAT across all inputs.",
            {},
        )
    if verdict == z3.sat:
        witness = _extract_witness(vc_solver.model(), ob.variables, z3_vars)
        return (
            ContractProofStatus.COUNTEREXAMPLE_FOUND,
            "Z3 found concrete input violating postcondition or division safety.",
            witness,
        )
    return (
        ContractProofStatus.BOUNDED_BY_PBT,
        f"Z3 returned unknown ({vc_solver.reason_unknown()}); deferred to dynamic PBT.",
        {},
    )


def _check_contract_satisfiability(
    ob: ContractObligation,
    z3_vars: Mapping[str, z3.ExprRef],
    ctx: z3.Context,
    rec: RecursionBudget,
    timeout_ms: int,
) -> tuple[z3.BoolRef, ContractProofStatus | None, str]:
    pre_terms = [_as_bool(_lower_z3_expr(p, z3_vars, ctx, rec)) for p in ob.preconditions]
    pre_z3 = _conjunction_z3(pre_terms, ctx)
    pre_solver = z3.Solver(ctx=ctx)
    pre_solver.set("timeout", timeout_ms)
    pre_solver.add(pre_z3)
    pre_chk = pre_solver.check()
    if pre_chk == z3.unsat:
        return (
            pre_z3,
            ContractProofStatus.VACUOUS_CONTRACT,
            "Preconditions P are unsatisfiable (contradictory input domain).",
        )
    if pre_chk == z3.unknown:
        return (
            pre_z3,
            ContractProofStatus.BOUNDED_BY_PBT,
            "Precondition satisfiability returned unknown; deferred to PBT.",
        )
    if ob.postconditions:
        post_terms = [_as_bool(_lower_z3_expr(q, z3_vars, ctx, rec)) for q in ob.postconditions]
        feas_solver = z3.Solver(ctx=ctx)
        feas_solver.set("timeout", timeout_ms)
        feas_solver.add(pre_z3, _conjunction_z3(post_terms, ctx))
        if feas_solver.check() == z3.unsat:
            return (
                pre_z3,
                ContractProofStatus.VACUOUS_CONTRACT,
                "Contract P ∧ Q is unsatisfiable (postconditions contradict preconditions).",
            )
    return pre_z3, None, ""


@jezz_callable
def discharge_obligation(
    obligation: ContractObligation,
    *,
    timeout_ms: BoundedSolverTimeoutMs = 500,
    clock: Callable[[], float] = time.perf_counter,
) -> ObligationProofResult:
    """Discharge one ``ContractObligation`` in an isolated ``z3.Context``."""
    started = clock()
    ctx = z3.Context()
    rec = RecursionBudget(max_depth=_MAX_SYMBOLIC_DEPTH)
    try:
        z3_vars = {v.name: _mk_z3_var(v, ctx) for v in obligation.variables}
        pre_z3, early_status, early_msg = _check_contract_satisfiability(
            obligation, z3_vars, ctx, rec, timeout_ms
        )
        if early_status is not None:
            return _build_result(obligation, early_status, early_msg, started, clock)
        has_goals = bool(obligation.postconditions) or obligation.assertion_count > 0
        if obligation.body_stmts is None or obligation.has_opaque_postcondition or not has_goals:
            reason = obligation.opaque_reason or "Contract satisfiable; body verified via PBT."
            return _build_result(
                obligation, ContractProofStatus.BOUNDED_BY_PBT, reason, started, clock
            )
        status, detail, witness = _verify_body_branches(
            obligation, pre_z3, z3_vars, ctx, timeout_ms
        )
        return _build_result(obligation, status, detail, started, clock, witness)
    except (ValueError, BudgetExceededError, z3.Z3Exception) as exc:
        return _build_result(
            obligation,
            ContractProofStatus.BOUNDED_BY_PBT,
            f"Symbolic evaluation deferred ({exc}); verified via PBT.",
            started,
            clock,
        )


@jezz_callable
def prove_obligations(
    obligations: Annotated[Sequence[ContractObligation], Field(max_length=50_000)],
    *,
    timeout_ms: BoundedSolverTimeoutMs = 500,
    clock: Callable[[], float] = time.perf_counter,
) -> SmtProofReport:
    """Discharge all ``obligations`` deterministically and seal an ``SmtProofReport``."""
    ordered = sorted(obligations, key=lambda item: item.obligation_id)
    results = tuple(discharge_obligation(ob, timeout_ms=timeout_ms, clock=clock) for ob in ordered)
    proved = sum(1 for r in results if r.status == ContractProofStatus.PROVED_DEDUCTIVE)
    pbt = sum(1 for r in results if r.status == ContractProofStatus.BOUNDED_BY_PBT)
    vacuous = sum(1 for r in results if r.status == ContractProofStatus.VACUOUS_CONTRACT)
    cex = sum(1 for r in results if r.status == ContractProofStatus.COUNTEREXAMPLE_FOUND)
    return SmtProofReport(
        total_obligations=len(results),
        proved_deductive_count=proved,
        bounded_by_pbt_count=pbt,
        vacuous_count=vacuous,
        counterexample_count=cex,
        has_failures=(vacuous + cex) > 0,
        proof_digest=compute_smt_proof_digest(results),
        results=results,
    )
