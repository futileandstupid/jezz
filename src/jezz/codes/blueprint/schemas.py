"""Canonical BlueprintGraph netlist IR schemas, digest seal, and wall/rank geometry."""

import hashlib
import json
from collections import deque
from collections.abc import Mapping, Sequence
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, Final, Self

from pydantic import BaseModel, Field, model_validator

from jezz.codes.complexity import PermitProfile
from jezz.codes.permits import SealDigestStr
from jezz.codes.polyglot_ir import SupportedLanguage
from jezz.guards import (
    STRICT_FROZEN_CONFIG,
    WIRE_TUPLE_VALIDATOR,
    BoundedText,
    IdentifierStr,
    LoopBudget,
    NonNegativeInt,
    PositiveInt,
    ShortText,
    SourceCodeText,
    jezz_callable,
)

BLUEPRINT_SCHEMA_VERSION: Final[str] = "1.3"
type InvariantIdStr = Annotated[
    str, Field(min_length=6, max_length=64, pattern=r"^INV-[A-Za-z0-9_-]{2,56}$")
]


class WallFireRating(StrEnum):
    """Structural compartmentation fire-rating governing wall stroke and breach severity."""

    EXTERIOR_ENVELOPE = "exterior_envelope"
    WORKSPACE_FIREWALL = "workspace_firewall"
    COMPARTMENT_WALL = "compartment_wall"
    PACKAGE_LOBBY = "package_lobby"


WALL_THICKNESS_PX: Final[Mapping[WallFireRating, int]] = MappingProxyType(
    {
        WallFireRating.EXTERIOR_ENVELOPE: 4,
        WallFireRating.WORKSPACE_FIREWALL: 3,
        WallFireRating.COMPARTMENT_WALL: 2,
        WallFireRating.PACKAGE_LOBBY: 1,
    }
)
WALL_RATING_RANK: Final[Mapping[WallFireRating, int]] = MappingProxyType(
    {
        WallFireRating.PACKAGE_LOBBY: 1,
        WallFireRating.COMPARTMENT_WALL: 2,
        WallFireRating.WORKSPACE_FIREWALL: 3,
        WallFireRating.EXTERIOR_ENVELOPE: 4,
    }
)


class PortDirection(StrEnum):
    """Signal orientation of a perimeter port pin mounted on a room wall."""

    INBOUND_PORT = "inbound_port"
    OUTBOUND_PORT = "outbound_port"


class RiserKind(StrEnum):
    """External envelope penetration category (runtime dependency or I/O hazard sink)."""

    EXTERNAL_PACKAGE = "external_package"
    SUBPROCESS_IO = "subprocess_io"
    NETWORK_IO = "network_io"
    FILESYSTEM_IO = "filesystem_io"
    DATABASE_IO = "database_io"


class RedlineStatus(StrEnum):
    """CAD revision state distinguishing baseline structure from proposed redline deltas."""

    EXISTING = "existing"
    PROPOSED_ADD = "proposed_add"
    PROPOSED_REMOVE = "proposed_remove"


class RedlineOp(StrEnum):
    """Algebraic edit operations composing target schematic B* = B_0 (+) Delta B."""

    ADD_ROOM = "add_room"
    ADD_PORT_PIN = "add_port_pin"
    ADD_BUS = "add_bus"
    REMOVE_BUS = "remove_bus"
    ADD_EXTERNAL_RISER = "add_external_riser"
    BIND_INVARIANT_PIN = "bind_invariant_pin"


class BlueprintRoom(BaseModel):
    """Compartment/package room with floorplate occupancy gauge and wall fire-rating."""

    model_config = STRICT_FROZEN_CONFIG
    room_id: IdentifierStr
    label: IdentifierStr
    member_id: IdentifierStr = "."
    language: SupportedLanguage = SupportedLanguage.PYTHON
    wall_rating: WallFireRating = WallFireRating.COMPARTMENT_WALL
    module_count: NonNegativeInt = 0
    max_modules: PositiveInt = 35
    public_symbol_count: NonNegativeInt = 0
    breaker_count: NonNegativeInt = 0
    topological_rank: NonNegativeInt = 0
    redline_status: RedlineStatus = RedlineStatus.EXISTING


class BlueprintWall(BaseModel):
    """Load-bearing envelope or partition wall with statutory fire-rating thickness."""

    model_config = STRICT_FROZEN_CONFIG
    wall_id: IdentifierStr
    room_id: IdentifierStr
    adjacent_room_id: BoundedText = ""
    rating: WallFireRating
    thickness_px: PositiveInt


class PerimeterPortPin(BaseModel):
    """Public boundary port pin mounted on a room wall for cross-room bus termination."""

    model_config = STRICT_FROZEN_CONFIG
    pin_id: IdentifierStr
    room_id: IdentifierStr
    symbol_name: IdentifierStr
    direction: PortDirection = PortDirection.INBOUND_PORT
    has_circuit_breaker: bool = True
    redline_status: RedlineStatus = RedlineStatus.EXISTING


class CircuitBreakerNode(BaseModel):
    """Runtime boundary guard (@jezz_callable / @icontract / strict schema) inside a room."""

    model_config = STRICT_FROZEN_CONFIG
    breaker_id: IdentifierStr
    room_id: IdentifierStr
    symbol_name: IdentifierStr
    guard_kind: IdentifierStr


class BlueprintBus(BaseModel):
    """Directed cross-room dependency/call conduit routed across compartment walls."""

    model_config = STRICT_FROZEN_CONFIG
    bus_id: IdentifierStr
    source_room: IdentifierStr
    target_room: IdentifierStr
    port_symbols: Annotated[
        tuple[IdentifierStr, ...], WIRE_TUPLE_VALIDATOR, Field(max_length=256)
    ] = ()
    call_count: NonNegativeInt = 0
    has_breaker_guard: bool = True
    redline_status: RedlineStatus = RedlineStatus.EXISTING


class ExternalRiser(BaseModel):
    """Exterior envelope penetration to a runtime third-party package or I/O sink."""

    model_config = STRICT_FROZEN_CONFIG
    riser_id: IdentifierStr
    room_id: IdentifierStr
    kind: RiserKind
    target_name: IdentifierStr
    redline_status: RedlineStatus = RedlineStatus.EXISTING


class InvariantPin(BaseModel):
    """Falsifiable domain invariant probe bound to a verification symbol on a room."""

    model_config = STRICT_FROZEN_CONFIG
    invariant_id: InvariantIdStr
    room_id: IdentifierStr
    verification_symbol: IdentifierStr
    redline_status: RedlineStatus = RedlineStatus.EXISTING


class RedlineDelta(BaseModel):
    """Single architectural redline edit operation in Delta B."""

    model_config = STRICT_FROZEN_CONFIG
    op: RedlineOp
    room_id: IdentifierStr
    target_room: BoundedText = ""
    symbol_name: BoundedText = ""
    member_id: IdentifierStr = "."
    language: SupportedLanguage = SupportedLanguage.PYTHON
    wall_rating: WallFireRating = WallFireRating.COMPARTMENT_WALL
    max_modules: PositiveInt = 35
    has_circuit_breaker: bool = True
    riser_kind: RiserKind = RiserKind.EXTERNAL_PACKAGE
    invariant_id: BoundedText = ""


@jezz_callable
def compute_blueprint_netlist_digest(
    rooms: Annotated[Sequence[BlueprintRoom], Field(max_length=4096)],
    port_pins: Annotated[Sequence[PerimeterPortPin], Field(max_length=32_768)],
    buses: Annotated[Sequence[BlueprintBus], Field(max_length=16_384)],
    external_risers: Annotated[Sequence[ExternalRiser], Field(max_length=16_384)],
    invariant_pins: Annotated[Sequence[InvariantPin], Field(max_length=4096)],
) -> SealDigestStr:
    """Compute canonical coarse-grained boundary SHA-256 digest invariant to interior refactors."""
    keep = RedlineStatus.PROPOSED_REMOVE
    r_rows = [
        (r.room_id, r.member_id, r.language.value, r.wall_rating.value, r.max_modules)
        for r in sorted((x for x in rooms if x.redline_status != keep), key=lambda x: x.room_id)
    ]
    p_rows = [
        (p.room_id, p.symbol_name, p.has_circuit_breaker)
        for p in sorted(
            (
                x
                for x in port_pins
                if x.direction == PortDirection.INBOUND_PORT and x.redline_status != keep
            ),
            key=lambda x: (x.room_id, x.symbol_name),
        )
    ]
    b_rows = [
        (b.source_room, b.target_room)
        for b in sorted(
            (x for x in buses if x.redline_status != keep),
            key=lambda x: (x.source_room, x.target_room),
        )
    ]
    e_rows = [
        (e.room_id, e.kind.value, e.target_name)
        for e in sorted(
            (x for x in external_risers if x.redline_status != keep),
            key=lambda x: (x.room_id, x.kind.value, x.target_name),
        )
    ]
    i_rows = [
        (ip.invariant_id, ip.room_id, ip.verification_symbol)
        for ip in sorted(
            (x for x in invariant_pins if x.redline_status != keep),
            key=lambda x: (x.invariant_id, x.room_id, x.verification_symbol),
        )
    ]
    canonical = {
        "schema_version": BLUEPRINT_SCHEMA_VERSION,
        "rooms": r_rows,
        "inbound_ports": p_rows,
        "buses": b_rows,
        "external_risers": e_rows,
        "invariant_pins": i_rows,
    }
    raw = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


class BlueprintGraph(BaseModel):
    """Canonical architectural floorplan & wiring schematic IR sealed by SHA-256 netlist digest."""

    model_config = STRICT_FROZEN_CONFIG
    schema_version: IdentifierStr = BLUEPRINT_SCHEMA_VERSION
    title: ShortText = "Workspace Blueprint"
    profile: PermitProfile = PermitProfile.STANDARD
    rooms: Annotated[tuple[BlueprintRoom, ...], WIRE_TUPLE_VALIDATOR, Field(max_length=4096)] = ()
    walls: Annotated[tuple[BlueprintWall, ...], WIRE_TUPLE_VALIDATOR, Field(max_length=16_384)] = ()
    port_pins: Annotated[
        tuple[PerimeterPortPin, ...], WIRE_TUPLE_VALIDATOR, Field(max_length=32_768)
    ] = ()
    circuit_breakers: Annotated[
        tuple[CircuitBreakerNode, ...], WIRE_TUPLE_VALIDATOR, Field(max_length=32_768)
    ] = ()
    buses: Annotated[tuple[BlueprintBus, ...], WIRE_TUPLE_VALIDATOR, Field(max_length=16_384)] = ()
    external_risers: Annotated[
        tuple[ExternalRiser, ...], WIRE_TUPLE_VALIDATOR, Field(max_length=16_384)
    ] = ()
    invariant_pins: Annotated[
        tuple[InvariantPin, ...], WIRE_TUPLE_VALIDATOR, Field(max_length=4096)
    ] = ()
    redlines: Annotated[tuple[RedlineDelta, ...], WIRE_TUPLE_VALIDATOR, Field(max_length=2048)] = ()
    netlist_digest: SealDigestStr

    @model_validator(mode="after")
    def _verify_netlist_digest(self) -> Self:
        expected = compute_blueprint_netlist_digest(
            self.rooms, self.port_pins, self.buses, self.external_risers, self.invariant_pins
        )
        if self.netlist_digest != expected:
            msg = f"BlueprintGraph.netlist_digest '{self.netlist_digest}' != '{expected}'."
            raise ValueError(msg)
        return self

    @jezz_callable
    def to_markdown(self) -> SourceCodeText:
        """Render an engineering schedule of rooms, cross-wall buses, risers, and invariants."""
        in_cnt = sum(1 for p in self.port_pins if p.direction == PortDirection.INBOUND_PORT)
        lines = [
            f"# Engineering Blueprint — `{self.title}`",
            f"- **Edition / Profile:** `JBC {self.schema_version}` (`{self.profile}`)",
            f"- **Netlist Digest:** `{self.netlist_digest}`",
            f"- **Rooms:** `{len(self.rooms)}` | **Inbound Port Pins:** `{in_cnt}` | "
            f"**Cross-Wall Buses:** `{len(self.buses)}` | "
            f"**External Risers:** `{len(self.external_risers)}`",
            "",
            "## Sheet A-1 — Room Occupancy & Fire-Rating Schedule",
            "| Room | Language | Wall Rating | Occupancy | Breakers | Rank | Status |",
            "| :--- | :--- | :--- | :---: | :---: | :---: | :--- |",
            *[
                f"| `{r.room_id}` | `{r.language}` | `{r.wall_rating}` | "
                f"`{r.module_count}/{r.max_modules}` | `{r.breaker_count}` | "
                f"`L{r.topological_rank}` | `{r.redline_status}` |"
                for r in self.rooms
            ],
        ]
        if self.buses:
            lines.extend(
                [
                    "",
                    "## Sheet E-1 — Cross-Room Wiring Schedule",
                    "| Bus | Source Room | Target Room | Coupled Ports | Guarded | Status |",
                    "| :--- | :--- | :--- | :--- | :---: | :--- |",
                    *[
                        f"| `{b.bus_id}` | `{b.source_room}` | `{b.target_room}` | "
                        f"`{', '.join(b.port_symbols[:6]) or '(module portal)'}` | "
                        f"**{'YES' if b.has_breaker_guard else 'NO'}** | `{b.redline_status}` |"
                        for b in self.buses
                    ],
                ]
            )
        return "\n".join(lines)


@jezz_callable
def compute_room_ranks(
    room_ids: Annotated[Sequence[IdentifierStr], Field(max_length=4096)],
    buses: Annotated[Sequence[BlueprintBus], Field(max_length=16_384)],
) -> dict[IdentifierStr, NonNegativeInt]:
    """Compute deterministic topological room layers over active BlueprintBus edges."""
    adj: dict[str, set[str]] = {r: set() for r in room_ids}
    indeg: dict[str, int] = {r: 0 for r in room_ids}
    for b in buses:
        if b.redline_status != RedlineStatus.PROPOSED_REMOVE and (
            b.source_room in adj
            and b.target_room in adj
            and b.target_room not in adj[b.source_room]
        ):
            adj[b.source_room].add(b.target_room)
            indeg[b.target_room] += 1

    ranks: dict[str, int] = {r: 0 for r in room_ids}
    queue: deque[str] = deque(sorted(r for r, deg in indeg.items() if deg == 0))
    visited: set[str] = set()
    budget = LoopBudget(max_steps=max(32, len(room_ids) * max(1, len(room_ids)) + 16))
    while queue:
        budget.tick()
        curr = queue.popleft()
        visited.add(curr)
        for nxt in sorted(adj[curr]):
            ranks[nxt] = max(ranks[nxt], ranks[curr] + 1)
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                queue.append(nxt)
    max_rank = max(ranks.values(), default=0)
    for r in room_ids:
        if r not in visited:
            ranks[r] = max_rank + 1
    return ranks


@jezz_callable
def build_blueprint_walls(
    rooms: Annotated[Sequence[BlueprintRoom], Field(max_length=4096)],
    buses: Annotated[Sequence[BlueprintBus], Field(max_length=16_384)],
) -> tuple[BlueprintWall, ...]:
    """Construct envelope and shared interior partition walls with statutory fire-ratings."""
    by_id = {r.room_id: r for r in rooms}
    walls: list[BlueprintWall] = [
        BlueprintWall(
            wall_id=f"wall:{r.room_id}:envelope"[:256],
            room_id=r.room_id,
            adjacent_room_id="",
            rating=r.wall_rating,
            thickness_px=WALL_THICKNESS_PX[r.wall_rating],
        )
        for r in rooms
    ]
    seen_pairs: set[tuple[str, str]] = set()
    for b in buses:
        a, c = sorted((b.source_room, b.target_room))
        if a == c or (a, c) in seen_pairs or a not in by_id or c not in by_id:
            continue
        seen_pairs.add((a, c))
        ra, rc = by_id[a].wall_rating, by_id[c].wall_rating
        stronger = ra if WALL_RATING_RANK[ra] >= WALL_RATING_RANK[rc] else rc
        walls.append(
            BlueprintWall(
                wall_id=f"wall:{a}|{c}"[:256],
                room_id=a,
                adjacent_room_id=c,
                rating=stronger,
                thickness_px=WALL_THICKNESS_PX[stronger],
            )
        )
    return tuple(walls)
