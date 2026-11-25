"""Switchable A-1/E-1 CAD SVG/HTML projections and ``jezz blueprint`` CLI handler (Track 26)."""

import argparse
import html
import io
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Final

from pydantic import TypeAdapter

from jezz.cli_interactive import CliExitCode, CliIoStreams, GuardedArgParser
from jezz.codes.blueprint import (
    WALL_THICKNESS_PX,
    BlueprintBus,
    BlueprintGraph,
    BlueprintRoom,
    ExternalRiser,
    PerimeterPortPin,
    PortDirection,
    RedlineDelta,
    RedlineStatus,
    extract_blueprint,
)
from jezz.codes.blueprint_lvs import (
    BlueprintLvsReport,
    apply_blueprint_redlines,
    verify_blueprint_lvs,
)
from jezz.codes.complexity import PermitProfile
from jezz.codes.discovery import discover_workspace_units, read_contained_json
from jezz.guards import ContainedPath, InstanceGuard, SourceCodeText, jezz_callable

_REDLINE_SEQ_ADAPTER: Final[TypeAdapter[tuple[RedlineDelta, ...]]] = TypeAdapter(
    tuple[RedlineDelta, ...]
)
_CANVAS_W: Final[int] = 1280
_CANVAS_H: Final[int] = 900


class BlueprintOutputFormat(StrEnum):
    """Supported ``jezz blueprint --format`` output projections."""

    MARKDOWN = "markdown"
    JSON = "json"
    SVG_A1 = "svg-a1"
    SVG_E1 = "svg-e1"
    HTML = "html"


@dataclass(frozen=True)
class _RectBox:
    x: float
    y: float
    w: float
    h: float


def _esc(text: str) -> str:
    return html.escape(text, quote=True)


def _write_out_line(stream: io.TextIOBase, text: str) -> None:
    payload = text if text.endswith("\n") else f"{text}\n"
    stream.write(payload)
    stream.flush()


def _persist_contained_output(anchor_dir: Path, raw_out: str, content: str) -> Path:
    out_path = Path(raw_out)
    if out_path.is_absolute():
        resolved = out_path.resolve()
        contained = ContainedPath.from_parts(resolved.parent, resolved.name)
    else:
        contained = ContainedPath.from_parts(anchor_dir.resolve(), raw_out)
    contained.resolved_path.parent.mkdir(parents=True, exist_ok=True)
    contained.resolved_path.write_text(content, encoding="utf-8")
    return contained.resolved_path


def _svg_defs_and_frame(sheet_code: str, width: int, height: int) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" data-cad-sheet="{_esc(sheet_code)}" '
        'style="background:#071324;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;">'
        "<defs>"
        '<pattern id="cad-grid" width="32" height="32" patternUnits="userSpaceOnUse">'
        '<path d="M 32 0 L 0 0 0 32" fill="none" stroke="#122b4a" stroke-width="0.8"/>'
        "</pattern>"
        '<marker id="bus-arrow" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
        '<path d="M 0 1 L 10 5 L 0 9 z" fill="#38bdf8"/>'
        "</marker>"
        '<marker id="redline-arrow" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
        '<path d="M 0 1 L 10 5 L 0 9 z" fill="#34d399"/>'
        "</marker>"
        "</defs>"
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="url(#cad-grid)"/>'
        f'<rect x="16" y="16" width="{width - 32}" height="{height - 32}" '
        'fill="none" stroke="#38bdf8" stroke-width="2"/>'
    )


def _render_title_block(
    graph: BlueprintGraph,
    sheet_code: str,
    sheet_subtitle: str,
    width: int,
    height: int,
) -> str:
    bx = width - 470
    by = height - 116
    short_dig = graph.netlist_digest[:27] + "..."
    return (
        f'<g data-cad-role="title-block" transform="translate({bx},{by})">'
        '<rect x="0" y="0" width="446" height="92" fill="#0b1d36" '
        'stroke="#38bdf8" stroke-width="1.8"/>'
        '<line x1="0" y1="30" x2="446" y2="30" stroke="#1e3a5f" stroke-width="1"/>'
        '<line x1="0" y1="60" x2="446" y2="60" stroke="#1e3a5f" stroke-width="1"/>'
        '<line x1="310" y1="0" x2="310" y2="92" stroke="#1e3a5f" stroke-width="1"/>'
        f'<text x="10" y="20" fill="#e2e8f0" font-size="12" font-weight="bold">'
        f"{_esc(graph.title[:38])}</text>"
        f'<text x="320" y="20" fill="#38bdf8" font-size="13" font-weight="bold">'
        f"SHEET {_esc(sheet_code)}</text>"
        f'<text x="10" y="49" fill="#94a3b8" font-size="11">{_esc(sheet_subtitle)}</text>'
        f'<text x="320" y="49" fill="#cbd5e1" font-size="11">'
        f"JBC {_esc(graph.schema_version)} ({_esc(str(graph.profile))})</text>"
        f'<text x="10" y="79" fill="#7dd3fc" font-size="10">{_esc(short_dig)}</text>'
        f'<text x="320" y="79" fill="#94a3b8" font-size="10">'
        f"R:{len(graph.rooms)} B:{len(graph.buses)} E:{len(graph.external_risers)}</text>"
        "</g>"
    )


def _compute_a1_room_boxes(rooms: Sequence[BlueprintRoom]) -> dict[str, _RectBox]:
    by_rank: dict[int, list[BlueprintRoom]] = {}
    for room in rooms:
        by_rank.setdefault(room.topological_rank, []).append(room)
    rank_keys = sorted(by_rank)
    if not rank_keys:
        return {}
    usable_x, usable_y = 48.0, 52.0
    usable_w = float(_CANVAS_W - 96)
    usable_h = float(_CANVAS_H - 200)
    row_gap = 18.0
    row_h = max(92.0, (usable_h - row_gap * max(0, len(rank_keys) - 1)) / len(rank_keys))
    boxes: dict[str, _RectBox] = {}

    for r_idx, rk in enumerate(rank_keys):
        row_rooms = sorted(by_rank[rk], key=lambda item: item.room_id)
        weights = [float(max(1, rm.module_count)) for rm in row_rooms]
        total_w = sum(weights)
        col_gap = 16.0
        avail_w = usable_w - col_gap * max(0, len(row_rooms) - 1)
        cursor_x = usable_x
        ry = usable_y + r_idx * (row_h + row_gap)
        for rm, wt in zip(row_rooms, weights, strict=True):
            rw = max(140.0, avail_w * (wt / total_w)) if len(row_rooms) > 1 else avail_w
            boxes[rm.room_id] = _RectBox(
                x=round(cursor_x, 1), y=round(ry, 1), w=round(rw, 1), h=round(row_h, 1)
            )
            cursor_x += rw + col_gap
    return boxes


def _gauge_color(count: int, ceiling: int) -> str:
    ratio = count / max(1, ceiling)
    if ratio > 1.0:
        return "#ef4444"
    return "#f59e0b" if ratio >= 0.85 else "#22c55e"


def _render_a1_single_room(
    room: BlueprintRoom,
    box: _RectBox,
    inbound_pins: Sequence[PerimeterPortPin],
    risers: Sequence[ExternalRiser],
) -> tuple[str, str, str, str]:
    stroke_w = WALL_THICKNESS_PX[room.wall_rating] + 0.5
    wall_col = "#34d399" if room.redline_status == RedlineStatus.PROPOSED_ADD else "#60a5fa"
    dash = ' stroke-dasharray="7,4"' if room.redline_status != RedlineStatus.EXISTING else ""
    ratio = min(1.0, room.module_count / max(1, room.max_modules))
    bar_w = round((box.w - 24.0) * ratio, 1)
    g_col = _gauge_color(room.module_count, room.max_modules)

    struct_svg = (
        f'<g class="cad-room" data-room-id="{_esc(room.room_id)}" cursor="pointer">'
        f'<rect x="{box.x}" y="{box.y}" width="{box.w}" height="{box.h}" rx="4" '
        f'fill="#0c213d" fill-opacity="0.88" stroke="{wall_col}" '
        f'stroke-width="{stroke_w}"{dash}/>'
        f'<text x="{box.x + 12}" y="{box.y + 22}" fill="#f8fafc" font-size="13" font-weight="bold">'
        f"{_esc(room.room_id)}</text>"
        f'<text x="{box.x + 12}" y="{box.y + 39}" fill="#94a3b8" font-size="10">'
        f"{_esc(room.language.upper())} | {_esc(room.wall_rating)} | "
        f"L{room.topological_rank}</text>"
        f'<rect x="{box.x + 12}" y="{box.y + 50}" width="{round(box.w - 24.0, 1)}" height="10" '
        'rx="2" fill="#1e293b"/>'
        f'<rect x="{box.x + 12}" y="{box.y + 50}" width="{bar_w}" height="10" '
        f'rx="2" fill="{g_col}"/>'
        f'<text x="{box.x + 12}" y="{box.y + 76}" fill="#e2e8f0" font-size="11">'
        f"OCCUPANCY {room.module_count}/{room.max_modules} MODULES "
        f"| PORTS {len(inbound_pins)}</text>"
        "</g>"
    )
    breaker_svg = (
        f'<g data-room-id="{_esc(room.room_id)}">'
        f'<rect x="{box.x + box.w - 78}" y="{box.y + 8}" width="66" height="18" rx="3" '
        'fill="#172554" stroke="#38bdf8" stroke-width="1"/>'
        f'<text x="{box.x + box.w - 72}" y="{box.y + 20}" fill="#7dd3fc" font-size="10">'
        f"[CB:{room.breaker_count}]</text></g>"
    )
    riser_svg = ""
    if risers:
        labels = ", ".join(f"{r.kind}:{r.target_name}" for r in risers[:3])
        riser_svg = (
            f'<text x="{box.x + 12}" y="{box.y + box.h - 10}" fill="#fbbf24" font-size="10">'
            f"RISERS: {_esc(labels[:52])}</text>"
        )
    redline_svg = ""
    if room.redline_status != RedlineStatus.EXISTING:
        redline_svg = (
            f'<text x="{box.x + box.w - 130}" y="{box.y + 38}" fill="#34d399" font-size="10" '
            f'font-weight="bold">{_esc(room.redline_status.upper())}</text>'
        )
    return struct_svg, breaker_svg, riser_svg, redline_svg


def _render_a1_conduits(
    buses: Sequence[BlueprintBus],
    boxes: Mapping[str, _RectBox],
) -> tuple[list[str], list[str]]:
    struct_lines: list[str] = []
    red_lines: list[str] = []
    for idx, bus in enumerate(buses):
        src = boxes.get(bus.source_room)
        dst = boxes.get(bus.target_room)
        if src is None or dst is None:
            continue
        sx = round(src.x + src.w * 0.5 + (idx % 5 - 2) * 8.0, 1)
        sy = round(src.y + src.h, 1)
        dx = round(dst.x + dst.w * 0.5 + (idx % 5 - 2) * 8.0, 1)
        dy = round(dst.y, 1)
        mid_y = round((sy + dy) * 0.5, 1)
        path_d = f"M {sx} {sy} L {sx} {mid_y} L {dx} {mid_y} L {dx} {dy}"
        if bus.redline_status != RedlineStatus.EXISTING:
            col = "#ef4444" if bus.redline_status == RedlineStatus.PROPOSED_REMOVE else "#34d399"
            red_lines.append(
                f'<path class="cad-bus" data-bus-id="{_esc(bus.bus_id)}" d="{path_d}" '
                f'fill="none" stroke="{col}" stroke-width="2.2" stroke-dasharray="6,4"/>'
            )
        else:
            struct_lines.append(
                f'<path class="cad-bus" data-bus-id="{_esc(bus.bus_id)}" d="{path_d}" '
                'fill="none" stroke="#38bdf8" stroke-opacity="0.45" stroke-width="1.5" '
                'marker-end="url(#bus-arrow)"/>'
            )
    return struct_lines, red_lines


@jezz_callable
def render_sheet_a1_svg(graph: BlueprintGraph) -> SourceCodeText:
    """Render Sheet A-1 (Architectural Floor Plan) SVG with occupancy gauges and wall ratings."""
    boxes = _compute_a1_room_boxes(graph.rooms)
    pins_by_room: dict[str, list[PerimeterPortPin]] = {}
    for pin in graph.port_pins:
        if pin.direction == PortDirection.INBOUND_PORT:
            pins_by_room.setdefault(pin.room_id, []).append(pin)
    risers_by_room: dict[str, list[ExternalRiser]] = {}
    for er in graph.external_risers:
        risers_by_room.setdefault(er.room_id, []).append(er)

    conduit_struct, conduit_red = _render_a1_conduits(graph.buses, boxes)
    struct_parts: list[str] = list(conduit_struct)
    breaker_parts: list[str] = []
    riser_parts: list[str] = []
    red_parts: list[str] = list(conduit_red)

    for room in graph.rooms:
        box = boxes.get(room.room_id)
        if box is None:
            continue
        s_svg, b_svg, r_svg, d_svg = _render_a1_single_room(
            room, box, pins_by_room.get(room.room_id, ()), risers_by_room.get(room.room_id, ())
        )
        struct_parts.append(s_svg)
        breaker_parts.append(b_svg)
        if r_svg:
            riser_parts.append(r_svg)
        if d_svg:
            red_parts.append(d_svg)

    return "".join(
        [
            _svg_defs_and_frame("A-1", _CANVAS_W, _CANVAS_H),
            '<text x="34" y="40" fill="#38bdf8" font-size="14" font-weight="bold">'
            "SHEET A-1 — ARCHITECTURAL COMPARTMENT FLOOR PLAN</text>",
            f'<g data-cad-layer="structure">{"".join(struct_parts)}</g>',
            f'<g data-cad-layer="breakers">{"".join(breaker_parts)}</g>',
            f'<g data-cad-layer="risers">{"".join(riser_parts)}</g>',
            f'<g data-cad-layer="redline_diff">{"".join(red_parts)}</g>',
            _render_title_block(
                graph, "A-1", "ARCHITECTURAL FLOOR PLAN & OCCUPANCY", _CANVAS_W, _CANVAS_H
            ),
            "</svg>",
        ]
    )


def _compute_e1_ic_boxes(rooms: Sequence[BlueprintRoom]) -> dict[str, _RectBox]:
    by_rank: dict[int, list[BlueprintRoom]] = {}
    for room in rooms:
        by_rank.setdefault(room.topological_rank, []).append(room)
    rank_keys = sorted(by_rank)
    if not rank_keys:
        return {}
    col_step = max(230.0, float(_CANVAS_W - 180) / max(1, len(rank_keys)))
    boxes: dict[str, _RectBox] = {}
    for c_idx, rk in enumerate(rank_keys):
        col_rooms = sorted(by_rank[rk], key=lambda item: item.room_id)
        avail_h = float(_CANVAS_H - 210)
        row_step = avail_h / max(1, len(col_rooms))
        cx = 64.0 + c_idx * col_step
        for r_idx, rm in enumerate(col_rooms):
            cy = 70.0 + r_idx * row_step
            boxes[rm.room_id] = _RectBox(
                x=round(cx, 1), y=round(cy, 1), w=184.0, h=min(132.0, row_step - 18.0)
            )
    return boxes


def _render_e1_ic_block(
    room: BlueprintRoom,
    box: _RectBox,
    in_pins: Sequence[PerimeterPortPin],
    out_pins: Sequence[PerimeterPortPin],
    inv_ids: Sequence[str],
) -> tuple[str, str]:
    pin_svg: list[str] = []
    for idx, pin in enumerate(in_pins[:5]):
        py = round(box.y + 28.0 + idx * 16.0, 1)
        pin_svg.append(
            f'<circle cx="{box.x}" cy="{py}" r="3.5" fill="#38bdf8"/>'
            f'<text x="{box.x + 7}" y="{py + 3}" fill="#cbd5e1" font-size="9">'
            f"{_esc(pin.symbol_name[:18])}</text>"
        )
    for idx, _pin in enumerate(out_pins[:5]):
        py = round(box.y + 28.0 + idx * 16.0, 1)
        pin_svg.append(
            f'<rect x="{box.x + box.w - 3.5}" y="{py - 3.5}" width="7" height="7" fill="#34d399"/>'
        )
    inv_txt = ""
    if inv_ids:
        inv_txt = (
            f'<text x="{box.x + 8}" y="{box.y + box.h - 8}" fill="#c084fc" font-size="9">'
            f"PINS: {_esc(', '.join(inv_ids[:2]))}</text>"
        )
    ic_struct = (
        f'<g class="cad-room" data-room-id="{_esc(room.room_id)}" cursor="pointer">'
        f'<rect x="{box.x}" y="{box.y}" width="{box.w}" height="{box.h}" rx="2" '
        'fill="#09192f" stroke="#7dd3fc" stroke-width="2"/>'
        f'<rect x="{box.x}" y="{box.y}" width="{box.w}" height="20" fill="#112a4d"/>'
        f'<text x="{box.x + 8}" y="{box.y + 14}" fill="#f8fafc" font-size="11" font-weight="bold">'
        f"IC {_esc(room.room_id[:20])}</text>"
        f"{''.join(pin_svg)}{inv_txt}</g>"
    )
    cb_badge = (
        f'<text x="{box.x + box.w - 50}" y="{box.y + 14}" fill="#38bdf8" font-size="9">'
        f"CB:{room.breaker_count}</text>"
    )
    return ic_struct, cb_badge


def _render_e1_manhattan_buses(
    buses: Sequence[BlueprintBus],
    boxes: Mapping[str, _RectBox],
) -> tuple[list[str], list[str], list[str]]:
    struct_paths: list[str] = []
    breaker_glyphs: list[str] = []
    red_paths: list[str] = []

    for idx, bus in enumerate(buses):
        src = boxes.get(bus.source_room)
        dst = boxes.get(bus.target_room)
        if src is None or dst is None:
            continue
        y1 = round(src.y + 32.0 + (idx % 4) * 14.0, 1)
        y2 = round(dst.y + 32.0 + (idx % 4) * 14.0, 1)
        x1 = round(src.x + src.w, 1)
        x2 = round(dst.x, 1)
        channel_x = round((x1 + x2) * 0.5 + (idx % 5 - 2) * 9.0, 1)
        d_cmd = f"M {x1} {y1} L {channel_x} {y1} L {channel_x} {y2} L {x2} {y2}"
        if bus.redline_status != RedlineStatus.EXISTING:
            col = "#ef4444" if bus.redline_status == RedlineStatus.PROPOSED_REMOVE else "#34d399"
            red_paths.append(
                f'<path class="cad-bus" data-bus-id="{_esc(bus.bus_id)}" d="{d_cmd}" '
                f'fill="none" stroke="{col}" stroke-width="2.2" stroke-dasharray="6,4" '
                'marker-end="url(#redline-arrow)"/>'
            )
        else:
            struct_paths.append(
                f'<path class="cad-bus" data-bus-id="{_esc(bus.bus_id)}" d="{d_cmd}" '
                'fill="none" stroke="#38bdf8" stroke-width="1.8" marker-end="url(#bus-arrow)"/>'
            )
        if bus.has_breaker_guard:
            my = round((y1 + y2) * 0.5, 1)
            breaker_glyphs.append(
                f'<rect x="{channel_x - 8}" y="{my - 6}" width="16" height="12" rx="2" '
                'fill="#071324" stroke="#34d399" stroke-width="1.2"/>'
                f'<text x="{channel_x - 6}" y="{my + 3}" fill="#34d399" font-size="7">CB</text>'
            )
    return struct_paths, breaker_glyphs, red_paths


@jezz_callable
def render_sheet_e1_svg(graph: BlueprintGraph) -> SourceCodeText:
    """Render Sheet E-1 (Electrical Wiring Schematic) SVG with Manhattan bus routing."""
    boxes = _compute_e1_ic_boxes(graph.rooms)
    in_by_room: dict[str, list[PerimeterPortPin]] = {}
    out_by_room: dict[str, list[PerimeterPortPin]] = {}
    for p in graph.port_pins:
        target_map = in_by_room if p.direction == PortDirection.INBOUND_PORT else out_by_room
        target_map.setdefault(p.room_id, []).append(p)
    inv_by_room: dict[str, list[str]] = {}
    for ip in graph.invariant_pins:
        inv_by_room.setdefault(ip.room_id, []).append(ip.invariant_id)

    bus_struct, bus_cbs, bus_red = _render_e1_manhattan_buses(graph.buses, boxes)
    struct_parts: list[str] = list(bus_struct)
    cb_parts: list[str] = list(bus_cbs)
    riser_parts: list[str] = []

    for room in graph.rooms:
        box = boxes.get(room.room_id)
        if box is None:
            continue
        ic_s, ic_cb = _render_e1_ic_block(
            room,
            box,
            in_by_room.get(room.room_id, ()),
            out_by_room.get(room.room_id, ()),
            inv_by_room.get(room.room_id, ()),
        )
        struct_parts.append(ic_s)
        cb_parts.append(ic_cb)

    for idx, er in enumerate(graph.external_risers[:14]):
        ry = 68 + idx * 46
        riser_parts.append(
            f'<g data-riser-id="{_esc(er.riser_id)}">'
            f'<rect x="{_CANVAS_W - 196}" y="{ry}" width="170" height="26" rx="3" '
            'fill="#271a07" stroke="#f59e0b" stroke-width="1.2"/>'
            f'<text x="{_CANVAS_W - 188}" y="{ry + 17}" fill="#fcd34d" font-size="9">'
            f"{_esc(er.room_id[:10])}=>{_esc(er.target_name[:12])}</text></g>"
        )

    return "".join(
        [
            _svg_defs_and_frame("E-1", _CANVAS_W, _CANVAS_H),
            '<text x="34" y="40" fill="#38bdf8" font-size="14" font-weight="bold">'
            "SHEET E-1 — ORTHOGONAL MANHATTAN WIRING SCHEMATIC</text>",
            f'<g data-cad-layer="structure">{"".join(struct_parts)}</g>',
            f'<g data-cad-layer="breakers">{"".join(cb_parts)}</g>',
            f'<g data-cad-layer="risers">{"".join(riser_parts)}</g>',
            f'<g data-cad-layer="redline_diff">{"".join(bus_red)}</g>',
            _render_title_block(
                graph, "E-1", "MANHATTAN BUS & PERIMETER PORT SCHEMATIC", _CANVAS_W, _CANVAS_H
            ),
            "</svg>",
        ]
    )


_CAD_VIEWER_SCRIPT: Final[str] = "\n".join(
    [
        "(function() {",
        "  const state = {",
        "    activeSheet: 'A-1',",
        "    selectedRoomId: null,",
        "    selectedBusId: null,",
        "    zoom: 1.0,",
        "    layers: { structure: true, breakers: true, risers: true, redline_diff: true }",
        "  };",
        "  function byId(id) { return document.getElementById(id); }",
        "  function syncView() {",
        "    const isA1 = state.activeSheet === 'A-1';",
        "    byId('pane-a1').style.display = isA1 ? 'block' : 'none';",
        "    byId('pane-e1').style.display = isA1 ? 'none' : 'block';",
        "    byId('btn-a1').dataset.active = String(isA1);",
        "    byId('btn-e1').dataset.active = String(!isA1);",
        "    byId('cad-stage').style.transform = 'scale(' + state.zoom.toFixed(2) + ')';",
        "    Object.keys(state.layers).forEach(function(layer) {",
        "      const q = '[data-cad-layer=\"' + layer + '\"]';",
        "      document.querySelectorAll(q).forEach(function(el) {",
        "        el.style.display = state.layers[layer] ? '' : 'none';",
        "      });",
        "    });",
        "    document.querySelectorAll('.cad-room').forEach(function(el) {",
        "      const hit = el.getAttribute('data-room-id') === state.selectedRoomId;",
        "      el.style.filter = hit ? 'drop-shadow(0 0 8px #38bdf8)' : '';",
        "    });",
        "    byId('cad-selection-status').textContent =",
        "      state.selectedRoomId ? ('Selected Room: ' + state.selectedRoomId) :",
        "      (state.selectedBusId",
        "        ? ('Selected Bus: ' + state.selectedBusId) : 'Selection: <none>');",
        "  }",
        "  byId('btn-a1').addEventListener('click', function() {",
        "    state.activeSheet = 'A-1'; syncView();",
        "  });",
        "  byId('btn-e1').addEventListener('click', function() {",
        "    state.activeSheet = 'E-1'; syncView();",
        "  });",
        "  byId('btn-zoom-in').addEventListener('click', function() {",
        "    state.zoom = Math.min(2.5, state.zoom + 0.15); syncView();",
        "  });",
        "  byId('btn-zoom-out').addEventListener('click', function() {",
        "    state.zoom = Math.max(0.5, state.zoom - 0.15); syncView();",
        "  });",
        "  document.querySelectorAll('[data-toggle-layer]').forEach(function(cb) {",
        "    cb.addEventListener('change', function(ev) {",
        "      state.layers[ev.target.getAttribute('data-toggle-layer')] = ev.target.checked;",
        "      syncView();",
        "    });",
        "  });",
        "  document.querySelectorAll('.cad-room').forEach(function(el) {",
        "    el.addEventListener('click', function() {",
        "      state.selectedRoomId = el.getAttribute('data-room-id');",
        "      state.selectedBusId = null;",
        "      syncView();",
        "    });",
        "  });",
        "  document.querySelectorAll('.cad-bus').forEach(function(el) {",
        "    el.addEventListener('click', function() {",
        "      state.selectedBusId = el.getAttribute('data-bus-id');",
        "      state.selectedRoomId = null;",
        "      syncView();",
        "    });",
        "  });",
        "  byId('btn-export-svg').addEventListener('click', function() {",
        "    const host = byId(state.activeSheet === 'A-1' ? 'pane-a1' : 'pane-e1');",
        "    const svg = host.querySelector('svg');",
        "    if (!svg) return;",
        "    const blob = new Blob([svg.outerHTML], { type: 'image/svg+xml;charset=utf-8' });",
        "    const link = document.createElement('a');",
        "    link.href = URL.createObjectURL(blob);",
        "    link.download = 'blueprint-' + state.activeSheet + '.svg';",
        "    link.click();",
        "  });",
        "  syncView();",
        "})();",
    ]
)


@jezz_callable
def render_switchable_cad_html(graph: BlueprintGraph) -> SourceCodeText:
    """Render synchronized Switchable CAD Viewer HTML uniting Sheet A-1 and Sheet E-1."""
    svg_a1 = render_sheet_a1_svg(graph)
    svg_e1 = render_sheet_e1_svg(graph)
    safe_json = graph.model_dump_json().replace("</", "<\\/")
    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"/>'
        f"<title>{_esc(graph.title)} — Jezz CAD Blueprint</title>"
        "<style>"
        "body{margin:0;background:#040b16;color:#e2e8f0;font-family:ui-monospace,monospace;}"
        ".bar{display:flex;gap:12px;align-items:center;padding:10px 18px;background:#0b1d36;"
        "border-bottom:1px solid #1e3a5f;flex-wrap:wrap;}"
        "button{background:#112a4d;color:#e2e8f0;border:1px solid #38bdf8;padding:6px 12px;"
        "border-radius:4px;cursor:pointer;font-family:inherit;}"
        'button[data-active="true"]{background:#0284c7;color:#fff;font-weight:bold;}'
        "#cad-viewport{overflow:auto;padding:16px;display:flex;justify-content:center;}"
        "#cad-stage{transform-origin:top center;transition:transform 0.12s ease;}"
        "</style></head><body>"
        '<div class="bar">'
        '<button id="btn-a1" data-action="switch-sheet" data-active="true">'
        "Sheet A-1 (Floor Plan)</button>"
        '<button id="btn-e1" data-action="switch-sheet" data-active="false">'
        "Sheet E-1 (Wiring Schematic)</button>"
        '<label><input type="checkbox" data-toggle-layer="structure" checked/> Structure</label>'
        '<label><input type="checkbox" data-toggle-layer="breakers" checked/> Breakers</label>'
        '<label><input type="checkbox" data-toggle-layer="risers" checked/> '
        "Taint &amp; I/O Risers</label>"
        '<label><input type="checkbox" data-toggle-layer="redline_diff" checked/> '
        "Redline Diff</label>"
        '<button id="btn-zoom-in">Zoom +</button>'
        '<button id="btn-zoom-out">Zoom -</button>'
        '<button id="btn-export-svg">Export .svg</button>'
        '<span id="cad-selection-status">Selection: &lt;none&gt;</span>'
        "</div>"
        f'<div id="cad-viewport"><div id="cad-stage">'
        f'<div id="pane-a1">{svg_a1}</div>'
        f'<div id="pane-e1" style="display:none;">{svg_e1}</div>'
        "</div></div>"
        f'<script id="jezz-blueprint-data" type="application/json">{safe_json}</script>'
        f"<script>{_CAD_VIEWER_SCRIPT}</script>"
        "</body></html>"
    )


@jezz_callable
def register_blueprint_parser(parser: GuardedArgParser) -> None:
    """Configure CLI flags on the ``jezz blueprint`` subcommand parser."""
    parser.add_argument("repo_path", type=Path, help="Target repository root directory.")
    parser.add_argument("--title", default=None, help="Blueprint title block heading.")
    parser.add_argument("--target-dir", dest="target_dirs", action="append", default=None)
    parser.add_argument(
        "--profile",
        choices=tuple(p.value for p in PermitProfile),
        default=PermitProfile.STANDARD.value,
        help="JBC floorplate profile ('standard' or 'strict_internal').",
    )
    parser.add_argument(
        "--redlines",
        type=Path,
        default=None,
        help="Optional JSON file containing RedlineDelta array to compose B* = B_0 (+) Delta B.",
    )
    parser.add_argument(
        "--verify-target",
        dest="verify_target",
        type=Path,
        default=None,
        help="Target BlueprintGraph JSON file to verify via coarse-grained LVS.",
    )
    parser.add_argument("--write-json", dest="write_json", default=None)
    parser.add_argument("--write-html", dest="write_html", default=None)
    parser.add_argument("--write-svg-a1", dest="write_svg_a1", default=None)
    parser.add_argument("--write-svg-e1", dest="write_svg_e1", default=None)
    parser.add_argument(
        "--format",
        dest="output_format",
        choices=tuple(f.value for f in BlueprintOutputFormat),
        default=BlueprintOutputFormat.MARKDOWN.value,
    )


def _read_external_or_repo_json(repo_root: Path, candidate: Path) -> str:
    if candidate.is_absolute():
        resolved = candidate.resolve()
        return read_contained_json(ContainedPath.from_parts(resolved.parent, resolved.name))
    cwd_contained = ContainedPath.from_parts(Path.cwd().resolve(), candidate.as_posix())
    if cwd_contained.resolved_path.is_file():
        return read_contained_json(cwd_contained)
    return read_contained_json(ContainedPath.from_parts(repo_root, candidate.as_posix()))


def _write_requested_artifacts(
    ns: argparse.Namespace,
    resolved_repo: Path,
    graph: BlueprintGraph,
) -> None:
    if ns.write_json is not None:
        _persist_contained_output(
            resolved_repo, str(ns.write_json), graph.model_dump_json(indent=2)
        )
    if ns.write_html is not None:
        _persist_contained_output(
            resolved_repo, str(ns.write_html), render_switchable_cad_html(graph)
        )
    if ns.write_svg_a1 is not None:
        _persist_contained_output(resolved_repo, str(ns.write_svg_a1), render_sheet_a1_svg(graph))
    if ns.write_svg_e1 is not None:
        _persist_contained_output(resolved_repo, str(ns.write_svg_e1), render_sheet_e1_svg(graph))


def _format_blueprint_stdout(graph: BlueprintGraph, fmt: str) -> str:
    if fmt == BlueprintOutputFormat.JSON:
        return graph.model_dump_json(indent=2)
    if fmt == BlueprintOutputFormat.SVG_A1:
        return render_sheet_a1_svg(graph)
    if fmt == BlueprintOutputFormat.SVG_E1:
        return render_sheet_e1_svg(graph)
    if fmt == BlueprintOutputFormat.HTML:
        return render_switchable_cad_html(graph)
    return graph.to_markdown()


def _run_cli_lvs_mode(
    ns: argparse.Namespace,
    resolved_repo: Path,
    target_dirs: Sequence[str],
    profile: PermitProfile,
    streams: CliIoStreams,
) -> CliExitCode:
    raw_target = _read_external_or_repo_json(resolved_repo, Path(ns.verify_target))
    target_bp = BlueprintGraph.model_validate_json(raw_target)
    bindings = tuple((ip.invariant_id, ip.verification_symbol) for ip in target_bp.invariant_pins)
    units = discover_workspace_units(resolved_repo, target_dirs=target_dirs)
    actual_bp = extract_blueprint(
        units,
        title=target_bp.title,
        profile=profile,
        invariant_bindings=bindings,
    )
    _write_requested_artifacts(ns, resolved_repo, actual_bp)
    report: BlueprintLvsReport = verify_blueprint_lvs(actual_bp, target_bp, units=units)
    rendered = (
        report.model_dump_json(indent=2)
        if str(ns.output_format) == BlueprintOutputFormat.JSON
        else report.to_markdown()
    )
    _write_out_line(streams.stdout, rendered)
    return 0 if report.is_lvs_clean else 1


@jezz_callable
def handle_blueprint_command(
    ns: Annotated[argparse.Namespace, InstanceGuard(argparse.Namespace)],
    streams: Annotated[CliIoStreams, InstanceGuard(CliIoStreams)],
) -> CliExitCode:
    """Execute ``jezz blueprint`` extraction, CAD rendering, or target LVS verification."""
    resolved_repo = Path(ns.repo_path).resolve()
    if not resolved_repo.is_dir():
        msg = f"Workspace directory does not exist: '{ns.repo_path}'."
        raise FileNotFoundError(msg)
    target_dirs = tuple(ns.target_dirs) if ns.target_dirs else ()
    profile = PermitProfile(str(ns.profile))
    if ns.verify_target is not None:
        return _run_cli_lvs_mode(ns, resolved_repo, target_dirs, profile, streams)

    title = str(ns.title) if ns.title else f"{resolved_repo.name or 'Workspace'} Blueprint"
    units = discover_workspace_units(resolved_repo, target_dirs=target_dirs)
    graph = extract_blueprint(units, title=title[:512], profile=profile)
    if ns.redlines is not None:
        raw_deltas = _read_external_or_repo_json(resolved_repo, Path(ns.redlines))
        deltas = _REDLINE_SEQ_ADAPTER.validate_json(raw_deltas)
        graph = apply_blueprint_redlines(graph, deltas, title=title[:512])

    _write_requested_artifacts(ns, resolved_repo, graph)
    _write_out_line(streams.stdout, _format_blueprint_stdout(graph, str(ns.output_format)))
    return 0


__all__ = [
    "BlueprintOutputFormat",
    "handle_blueprint_command",
    "register_blueprint_parser",
    "render_sheet_a1_svg",
    "render_sheet_e1_svg",
    "render_switchable_cad_html",
]
