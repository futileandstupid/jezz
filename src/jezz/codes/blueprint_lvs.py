"""Blueprint redline algebra and coarse-grained Layout-Versus-Schematic (LVS) verifier."""

from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Annotated, Self

import icontract
from pydantic import BaseModel, Field, model_validator

from jezz.codes.blueprint import (
    BlueprintBus,
    BlueprintGraph,
    BlueprintRoom,
    ExternalRiser,
    InvariantPin,
    PerimeterPortPin,
    PortDirection,
    RedlineDelta,
    RedlineOp,
    RedlineStatus,
    RiserKind,
    build_blueprint_walls,
    compute_blueprint_netlist_digest,
    compute_room_ranks,
)
from jezz.codes.compartment import check_cog_006
from jezz.codes.dep_graph import find_tarjan_cycles
from jezz.codes.permits import SealDigestStr
from jezz.codes.polyglot_ir import InspectedUnit
from jezz.guards import (
    STRICT_FROZEN_CONFIG,
    WIRE_TUPLE_VALIDATOR,
    IdentifierStr,
    ShortText,
    SourceCodeText,
    jezz_callable,
)


class LvsDefectKind(StrEnum):
    """Canonical EDA Layout-Versus-Schematic boundary discrepancy classification."""

    OPEN_CIRCUIT = "open_circuit"
    SHORT_CIRCUIT = "short_circuit"


class LvsBoundaryLayer(StrEnum):
    """Architectural boundary surface where an LVS discrepancy occurred."""

    ROOM_WALL = "room_wall"
    PERIMETER_PORT = "perimeter_port"
    CROSS_ROOM_BUS = "cross_room_bus"
    EXTERNAL_RISER = "external_riser"
    INVARIANT_PIN = "invariant_pin"


class LvsDefect(BaseModel):
    """Single cited coarse-grained boundary discrepancy between physical layout and schematic."""

    model_config = STRICT_FROZEN_CONFIG
    kind: LvsDefectKind
    layer: LvsBoundaryLayer
    subject_id: IdentifierStr
    message: ShortText
    remediation: ShortText


class BlueprintLvsReport(BaseModel):
    """Deterministic coarse-grained LVS equivalence report between actual and target blueprints."""

    model_config = STRICT_FROZEN_CONFIG
    is_lvs_clean: bool
    digest_exact_match: bool
    actual_netlist_digest: SealDigestStr
    target_netlist_digest: SealDigestStr
    open_circuit_count: Annotated[int, Field(ge=0, le=100_000)] = 0
    short_circuit_count: Annotated[int, Field(ge=0, le=100_000)] = 0
    defects: Annotated[tuple[LvsDefect, ...], WIRE_TUPLE_VALIDATOR, Field(max_length=4096)] = ()

    @model_validator(mode="after")
    def _validate_lvs_consistency(self) -> Self:
        opens = sum(1 for d in self.defects if d.kind == LvsDefectKind.OPEN_CIRCUIT)
        shorts = sum(1 for d in self.defects if d.kind == LvsDefectKind.SHORT_CIRCUIT)
        if self.open_circuit_count != opens or self.short_circuit_count != shorts:
            msg = "BlueprintLvsReport open/short counts contradict defects tuple."
            raise ValueError(msg)
        if self.is_lvs_clean != (len(self.defects) == 0):
            msg = "BlueprintLvsReport.is_lvs_clean must equal (len(defects) == 0)."
            raise ValueError(msg)
        return self

    @jezz_callable
    def to_markdown(self) -> SourceCodeText:
        """Render a structured Markdown Layout-Versus-Schematic (LVS) verification report."""
        verdict = "LVS_CLEAN" if self.is_lvs_clean else "LVS_FAILED"
        lines = [
            f"# Blueprint LVS Verification — `{verdict}`",
            f"- **Target Netlist:** `{self.target_netlist_digest}`",
            f"- **Actual Netlist:** `{self.actual_netlist_digest}`",
            f"- **Digest Match:** `{self.digest_exact_match}`",
            f"- **Open Circuits:** `{self.open_circuit_count}` | "
            f"**Short Circuits:** `{self.short_circuit_count}`",
        ]
        if self.defects:
            lines.extend(["", "## Cited Boundary Defects"])
            for d in self.defects:
                lines.append(
                    f"- **[{d.kind.upper()}] `{d.layer}` (`{d.subject_id}`)**: {d.message} — "
                    f"*Remediation:* {d.remediation}"
                )
        return "\n".join(lines)


def _apply_bus_redline(
    delta: RedlineDelta,
    rooms: Mapping[str, BlueprintRoom],
    pins: dict[tuple[str, PortDirection, str], PerimeterPortPin],
    buses: dict[tuple[str, str], BlueprintBus],
) -> None:
    dst = delta.target_room[:256]
    if not dst or dst not in rooms or dst == delta.room_id:
        msg = f"Bus redline requires distinct existing 'target_room' (got '{dst}')."
        raise ValueError(msg)
    key = (delta.room_id, dst)
    if delta.op == RedlineOp.REMOVE_BUS:
        existing = buses.get(key)
        if existing is not None:
            buses[key] = existing.model_copy(
                update={"redline_status": RedlineStatus.PROPOSED_REMOVE}
            )
        return
    ports = (delta.symbol_name[:256],) if delta.symbol_name else ()
    buses[key] = BlueprintBus(
        bus_id=f"{delta.room_id}->{dst}"[:256],
        source_room=delta.room_id,
        target_room=dst,
        port_symbols=ports,
        has_breaker_guard=delta.has_circuit_breaker,
        redline_status=RedlineStatus.PROPOSED_ADD,
    )
    out_sym = f"out->{dst}"[:256]
    pins[(delta.room_id, PortDirection.OUTBOUND_PORT, out_sym)] = PerimeterPortPin(
        pin_id=f"{delta.room_id}::out:{dst}"[:256],
        room_id=delta.room_id,
        symbol_name=out_sym,
        direction=PortDirection.OUTBOUND_PORT,
        has_circuit_breaker=delta.has_circuit_breaker,
        redline_status=RedlineStatus.PROPOSED_ADD,
    )


def _apply_single_redline(
    delta: RedlineDelta,
    rooms: dict[str, BlueprintRoom],
    pins: dict[tuple[str, PortDirection, str], PerimeterPortPin],
    buses: dict[tuple[str, str], BlueprintBus],
    risers: dict[tuple[str, RiserKind, str], ExternalRiser],
    invariants: dict[tuple[str, str, str], InvariantPin],
) -> None:
    if delta.op == RedlineOp.ADD_ROOM:
        rooms[delta.room_id] = BlueprintRoom(
            room_id=delta.room_id,
            label=delta.room_id.split("/")[-1][:256] or delta.room_id,
            member_id=delta.member_id,
            language=delta.language,
            wall_rating=delta.wall_rating,
            module_count=0,
            max_modules=delta.max_modules,
            redline_status=RedlineStatus.PROPOSED_ADD,
        )
        return
    if delta.room_id not in rooms:
        msg = f"RedlineDelta '{delta.op}' references unknown room '{delta.room_id}'."
        raise ValueError(msg)
    if delta.op == RedlineOp.ADD_PORT_PIN:
        if not delta.symbol_name:
            msg = "ADD_PORT_PIN requires a non-empty 'symbol_name'."
            raise ValueError(msg)
        sym = delta.symbol_name[:256]
        pins[(delta.room_id, PortDirection.INBOUND_PORT, sym)] = PerimeterPortPin(
            pin_id=f"{delta.room_id}::in:{sym}"[:256],
            room_id=delta.room_id,
            symbol_name=sym,
            direction=PortDirection.INBOUND_PORT,
            has_circuit_breaker=delta.has_circuit_breaker,
            redline_status=RedlineStatus.PROPOSED_ADD,
        )
        return
    if delta.op in {RedlineOp.ADD_BUS, RedlineOp.REMOVE_BUS}:
        _apply_bus_redline(delta, rooms, pins, buses)
        return
    if delta.op == RedlineOp.ADD_EXTERNAL_RISER:
        if not delta.symbol_name:
            msg = "ADD_EXTERNAL_RISER requires target package/sink in 'symbol_name'."
            raise ValueError(msg)
        tgt = delta.symbol_name[:256]
        risers[(delta.room_id, delta.riser_kind, tgt)] = ExternalRiser(
            riser_id=f"{delta.room_id}=>riser:{delta.riser_kind.value}:{tgt}"[:256],
            room_id=delta.room_id,
            kind=delta.riser_kind,
            target_name=tgt,
            redline_status=RedlineStatus.PROPOSED_ADD,
        )
        return
    if delta.op == RedlineOp.BIND_INVARIANT_PIN:
        if not delta.invariant_id or not delta.symbol_name:
            msg = "BIND_INVARIANT_PIN requires both 'invariant_id' and 'symbol_name'."
            raise ValueError(msg)
        inv_id = delta.invariant_id[:64]
        sym = delta.symbol_name[:256]
        invariants[(inv_id, delta.room_id, sym)] = InvariantPin(
            invariant_id=inv_id,
            room_id=delta.room_id,
            verification_symbol=sym,
            redline_status=RedlineStatus.PROPOSED_ADD,
        )


@icontract.ensure(lambda baseline, deltas, result: len(result.rooms) >= len(baseline.rooms))
@jezz_callable
def apply_blueprint_redlines(
    baseline: BlueprintGraph,
    deltas: Annotated[Sequence[RedlineDelta], Field(max_length=2048)],
    *,
    title: ShortText | None = None,
) -> BlueprintGraph:
    """Compose target schematic B* = B_0 (+) Delta B from baseline BlueprintGraph and redlines."""
    rooms = {r.room_id: r for r in baseline.rooms}
    pins = {(p.room_id, p.direction, p.symbol_name): p for p in baseline.port_pins}
    buses = {(b.source_room, b.target_room): b for b in baseline.buses}
    risers = {(er.room_id, er.kind, er.target_name): er for er in baseline.external_risers}
    invariants = {
        (ip.invariant_id, ip.room_id, ip.verification_symbol): ip for ip in baseline.invariant_pins
    }
    for delta in deltas:
        _apply_single_redline(delta, rooms, pins, buses, risers, invariants)

    ordered_buses = tuple(buses[k] for k in sorted(buses))
    ranks = compute_room_ranks(tuple(sorted(rooms)), ordered_buses)
    ordered_rooms = tuple(
        rooms[r_id].model_copy(update={"topological_rank": ranks[r_id]}) for r_id in sorted(rooms)
    )
    ordered_pins = tuple(pins[k] for k in sorted(pins, key=lambda k: (k[0], k[1].value, k[2])))
    ordered_risers = tuple(
        risers[k] for k in sorted(risers, key=lambda k: (k[0], k[1].value, k[2]))
    )
    ordered_invs = tuple(invariants[k] for k in sorted(invariants))
    walls = build_blueprint_walls(ordered_rooms, ordered_buses)
    digest = compute_blueprint_netlist_digest(
        ordered_rooms, ordered_pins, ordered_buses, ordered_risers, ordered_invs
    )
    return BlueprintGraph(
        schema_version=baseline.schema_version,
        title=title if title is not None else baseline.title,
        profile=baseline.profile,
        rooms=ordered_rooms,
        walls=walls,
        port_pins=ordered_pins,
        circuit_breakers=baseline.circuit_breakers,
        buses=ordered_buses,
        external_risers=ordered_risers,
        invariant_pins=ordered_invs,
        redlines=tuple([*baseline.redlines, *deltas])[:2048],
        netlist_digest=digest,
    )


def _check_room_walls(
    actual_rooms: Mapping[str, BlueprintRoom],
    target_rooms: Mapping[str, BlueprintRoom],
) -> list[LvsDefect]:
    defects: list[LvsDefect] = []
    for r_id in sorted(target_rooms):
        if r_id not in actual_rooms:
            defects.append(
                LvsDefect(
                    kind=LvsDefectKind.OPEN_CIRCUIT,
                    layer=LvsBoundaryLayer.ROOM_WALL,
                    subject_id=r_id,
                    message=f"Required compartment room '{r_id}' is absent from physical layout.",
                    remediation=f"Create production module(s) inside room '{r_id}'.",
                )
            )
    for r_id in sorted(actual_rooms):
        room = actual_rooms[r_id]
        if r_id not in target_rooms:
            defects.append(
                LvsDefect(
                    kind=LvsDefectKind.SHORT_CIRCUIT,
                    layer=LvsBoundaryLayer.ROOM_WALL,
                    subject_id=r_id,
                    message=f"Undeclared compartment room '{r_id}' was introduced without redline.",
                    remediation=f"Approve an ADD_ROOM redline for '{r_id}' or relocate modules.",
                )
            )
        if room.module_count > room.max_modules:
            defects.append(
                LvsDefect(
                    kind=LvsDefectKind.SHORT_CIRCUIT,
                    layer=LvsBoundaryLayer.ROOM_WALL,
                    subject_id=r_id,
                    message=(
                        f"Room '{r_id}' breaches floorplate capacity "
                        f"({room.module_count}/{room.max_modules} modules)."
                    ),
                    remediation="Partition room into child compartments via redline permit.",
                )
            )
    return defects


def _check_inbound_ports(
    actual_ports: Mapping[tuple[str, str], PerimeterPortPin],
    target_ports: Mapping[tuple[str, str], PerimeterPortPin],
) -> list[LvsDefect]:
    defects: list[LvsDefect] = []
    for key in sorted(target_ports):
        t_pin = target_ports[key]
        a_pin = actual_ports.get(key)
        if a_pin is None:
            defects.append(
                LvsDefect(
                    kind=LvsDefectKind.OPEN_CIRCUIT,
                    layer=LvsBoundaryLayer.PERIMETER_PORT,
                    subject_id=t_pin.pin_id,
                    message=(
                        f"Required perimeter port pin '{t_pin.symbol_name}' on room "
                        f"'{t_pin.room_id}' is missing or unexported."
                    ),
                    remediation=(
                        f"Implement and export '{t_pin.symbol_name}' on '{t_pin.room_id}' portal."
                    ),
                )
            )
            continue
        if t_pin.has_circuit_breaker and not a_pin.has_circuit_breaker:
            defects.append(
                LvsDefect(
                    kind=LvsDefectKind.SHORT_CIRCUIT,
                    layer=LvsBoundaryLayer.PERIMETER_PORT,
                    subject_id=a_pin.pin_id,
                    message=(
                        f"Perimeter port '{a_pin.symbol_name}' on '{a_pin.room_id}' omits "
                        "required circuit-breaker guard."
                    ),
                    remediation=(
                        "Guard boundary callable/schema with @jezz_callable, @icontract, "
                        "or strict schema validation."
                    ),
                )
            )
    for key in sorted(actual_ports):
        if key not in target_ports:
            a_pin = actual_ports[key]
            defects.append(
                LvsDefect(
                    kind=LvsDefectKind.SHORT_CIRCUIT,
                    layer=LvsBoundaryLayer.PERIMETER_PORT,
                    subject_id=a_pin.pin_id,
                    message=(
                        f"Undeclared public perimeter port '{a_pin.symbol_name}' exposed on "
                        f"room '{a_pin.room_id}'."
                    ),
                    remediation=(
                        "Keep interior helper private/unexported or approve an ADD_PORT_PIN "
                        "redline."
                    ),
                )
            )
    return defects


def _check_single_matched_bus(
    actual_bus: BlueprintBus, target_bus: BlueprintBus
) -> list[LvsDefect]:
    defects: list[LvsDefect] = []
    if target_bus.port_symbols:
        missing = sorted(set(target_bus.port_symbols) - set(actual_bus.port_symbols))
        if missing:
            defects.append(
                LvsDefect(
                    kind=LvsDefectKind.OPEN_CIRCUIT,
                    layer=LvsBoundaryLayer.CROSS_ROOM_BUS,
                    subject_id=target_bus.bus_id,
                    message=(
                        f"Bus '{target_bus.bus_id}' omits required target port pin(s): {missing}."
                    ),
                    remediation=f"Wire '{target_bus.source_room}' through {missing}.",
                )
            )
    if target_bus.has_breaker_guard and not actual_bus.has_breaker_guard:
        defects.append(
            LvsDefect(
                kind=LvsDefectKind.SHORT_CIRCUIT,
                layer=LvsBoundaryLayer.CROSS_ROOM_BUS,
                subject_id=actual_bus.bus_id,
                message=f"Cross-room bus '{actual_bus.bus_id}' couples to unguarded target port.",
                remediation="Enforce runtime contract breaker on coupled target port symbols.",
            )
        )
    return defects


def _check_cross_room_buses(
    actual_buses: Mapping[tuple[str, str], BlueprintBus],
    target_active_buses: Mapping[tuple[str, str], BlueprintBus],
    demolished_buses: Mapping[tuple[str, str], BlueprintBus],
) -> list[LvsDefect]:
    defects: list[LvsDefect] = []
    for key in sorted(target_active_buses):
        t_bus = target_active_buses[key]
        a_bus = actual_buses.get(key)
        if a_bus is None:
            defects.append(
                LvsDefect(
                    kind=LvsDefectKind.OPEN_CIRCUIT,
                    layer=LvsBoundaryLayer.CROSS_ROOM_BUS,
                    subject_id=t_bus.bus_id,
                    message=(
                        f"Required cross-room bus '{t_bus.source_room} -> {t_bus.target_room}' "
                        "is disconnected."
                    ),
                    remediation=(
                        f"Connect '{t_bus.source_room}' to '{t_bus.target_room}' boundary ports."
                    ),
                )
            )
            continue
        defects.extend(_check_single_matched_bus(a_bus, t_bus))

    for key in sorted(actual_buses):
        if key in target_active_buses:
            continue
        a_bus = actual_buses[key]
        detail = (
            f"Demolished bus '{a_bus.bus_id}' (REMOVE_BUS) remains physically wired."
            if key in demolished_buses
            else (
                f"Unauthorized cross-wall bus '{a_bus.source_room} -> {a_bus.target_room}' "
                "violates schematic isolation."
            )
        )
        defects.append(
            LvsDefect(
                kind=LvsDefectKind.SHORT_CIRCUIT,
                layer=LvsBoundaryLayer.CROSS_ROOM_BUS,
                subject_id=a_bus.bus_id,
                message=detail,
                remediation="Sever unauthorized cross-room dependency or approve ADD_BUS redline.",
            )
        )
    return defects


def _check_bus_cycles(actual_buses: Mapping[tuple[str, str], BlueprintBus]) -> list[LvsDefect]:
    adj: dict[str, set[str]] = {}
    for src, dst in actual_buses:
        adj.setdefault(src, set()).add(dst)
        adj.setdefault(dst, set())
    frozen = {k: frozenset(v) for k, v in adj.items()}
    return [
        LvsDefect(
            kind=LvsDefectKind.SHORT_CIRCUIT,
            layer=LvsBoundaryLayer.ROOM_WALL,
            subject_id=(cycle_id := "<->".join(scc[:6])[:256]),
            message=f"Cyclic cross-room feedback short-circuit detected: {cycle_id}.",
            remediation="Break room cycle by extracting shared contracts into a sink room.",
        )
        for scc in find_tarjan_cycles(frozen)
    ]


def _check_external_risers(
    actual_risers: Mapping[tuple[str, RiserKind, str], ExternalRiser],
    target_risers: Mapping[tuple[str, RiserKind, str], ExternalRiser],
) -> list[LvsDefect]:
    defects: list[LvsDefect] = []
    for key in sorted(target_risers, key=lambda k: (k[0], k[1].value, k[2])):
        if key not in actual_risers:
            t_er = target_risers[key]
            defects.append(
                LvsDefect(
                    kind=LvsDefectKind.OPEN_CIRCUIT,
                    layer=LvsBoundaryLayer.EXTERNAL_RISER,
                    subject_id=t_er.riser_id,
                    message=(
                        f"Declared external riser '{t_er.target_name}' ({t_er.kind}) in room "
                        f"'{t_er.room_id}' is ungrounded."
                    ),
                    remediation=(
                        f"Wire '{t_er.room_id}' to '{t_er.target_name}' or update target redline."
                    ),
                )
            )
    for key in sorted(actual_risers, key=lambda k: (k[0], k[1].value, k[2])):
        if key not in target_risers:
            a_er = actual_risers[key]
            defects.append(
                LvsDefect(
                    kind=LvsDefectKind.SHORT_CIRCUIT,
                    layer=LvsBoundaryLayer.EXTERNAL_RISER,
                    subject_id=a_er.riser_id,
                    message=(
                        f"Undeclared external riser/sink '{a_er.target_name}' ({a_er.kind}) "
                        f"penetrates envelope from room '{a_er.room_id}'."
                    ),
                    remediation=(
                        f"Remove unauthorized sink/dependency '{a_er.target_name}' or approve "
                        "ADD_EXTERNAL_RISER redline."
                    ),
                )
            )
    return defects


def _check_invariant_pins(
    actual_invs: Mapping[tuple[str, str, str], InvariantPin],
    target_invs: Mapping[tuple[str, str, str], InvariantPin],
) -> list[LvsDefect]:
    return [
        LvsDefect(
            kind=LvsDefectKind.OPEN_CIRCUIT,
            layer=LvsBoundaryLayer.INVARIANT_PIN,
            subject_id=f"{t_ip.invariant_id}@{t_ip.room_id}"[:256],
            message=(
                f"Invariant pin '{t_ip.invariant_id}' ('{t_ip.verification_symbol}') "
                f"is missing on room '{t_ip.room_id}'."
            ),
            remediation=f"Ground '{t_ip.verification_symbol}' inside room '{t_ip.room_id}'.",
        )
        for key in sorted(target_invs)
        if key not in actual_invs and (t_ip := target_invs[key])
    ]


def _check_unit_firewalls(
    units: Sequence[InspectedUnit],
    actual: BlueprintGraph,
) -> list[LvsDefect]:
    if not units:
        return []
    return [
        LvsDefect(
            kind=LvsDefectKind.SHORT_CIRCUIT,
            layer=LvsBoundaryLayer.ROOM_WALL,
            subject_id=f"{d.location.file_path}:{d.location.line}"[:256],
            message=d.message,
            remediation=d.remediation,
        )
        for d in check_cog_006(units, profile=actual.profile)
    ]


@icontract.ensure(lambda result: result.is_lvs_clean == (len(result.defects) == 0))
@jezz_callable
def verify_blueprint_lvs(
    actual: BlueprintGraph,
    target: BlueprintGraph,
    *,
    units: Annotated[Sequence[InspectedUnit], Field(max_length=100_000)] = (),
) -> BlueprintLvsReport:
    """Verify coarse-grained boundary homomorphism while leaving room interiors unconstrained."""
    keep = RedlineStatus.PROPOSED_REMOVE
    actual_rooms = {r.room_id: r for r in actual.rooms if r.redline_status != keep}
    target_rooms = {r.room_id: r for r in target.rooms if r.redline_status != keep}
    actual_ports = {
        (p.room_id, p.symbol_name): p
        for p in actual.port_pins
        if p.direction == PortDirection.INBOUND_PORT and p.redline_status != keep
    }
    target_ports = {
        (p.room_id, p.symbol_name): p
        for p in target.port_pins
        if p.direction == PortDirection.INBOUND_PORT and p.redline_status != keep
    }
    actual_buses = {
        (b.source_room, b.target_room): b for b in actual.buses if b.redline_status != keep
    }
    target_active_buses = {
        (b.source_room, b.target_room): b for b in target.buses if b.redline_status != keep
    }
    demolished_buses = {
        (b.source_room, b.target_room): b for b in target.buses if b.redline_status == keep
    }
    actual_risers = {
        (er.room_id, er.kind, er.target_name): er
        for er in actual.external_risers
        if er.redline_status != keep
    }
    target_risers = {
        (er.room_id, er.kind, er.target_name): er
        for er in target.external_risers
        if er.redline_status != keep
    }
    actual_invs = {
        (ip.invariant_id, ip.room_id, ip.verification_symbol): ip
        for ip in actual.invariant_pins
        if ip.redline_status != keep
    }
    target_invs = {
        (ip.invariant_id, ip.room_id, ip.verification_symbol): ip
        for ip in target.invariant_pins
        if ip.redline_status != keep
    }

    defects = [
        *_check_room_walls(actual_rooms, target_rooms),
        *_check_inbound_ports(actual_ports, target_ports),
        *_check_cross_room_buses(actual_buses, target_active_buses, demolished_buses),
        *_check_bus_cycles(actual_buses),
        *_check_external_risers(actual_risers, target_risers),
        *_check_invariant_pins(actual_invs, target_invs),
        *_check_unit_firewalls(units, actual),
    ]
    opens = sum(1 for d in defects if d.kind == LvsDefectKind.OPEN_CIRCUIT)
    shorts = sum(1 for d in defects if d.kind == LvsDefectKind.SHORT_CIRCUIT)
    return BlueprintLvsReport(
        is_lvs_clean=(len(defects) == 0),
        digest_exact_match=(actual.netlist_digest == target.netlist_digest),
        actual_netlist_digest=actual.netlist_digest,
        target_netlist_digest=target.netlist_digest,
        open_circuit_count=opens,
        short_circuit_count=shorts,
        defects=tuple(defects[:4096]),
    )


__all__ = [
    "BlueprintLvsReport",
    "LvsBoundaryLayer",
    "LvsDefect",
    "LvsDefectKind",
    "apply_blueprint_redlines",
    "verify_blueprint_lvs",
]
