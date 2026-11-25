import ELK, {
  type ElkEdgeSection,
  type ElkExtendedEdge,
  type ElkNode,
  type ElkPort,
} from "elkjs/lib/elk.bundled.js";
import type {
  BlueprintBus,
  BlueprintGraph,
  BlueprintRoom,
  CadLayerState,
  ExternalRiser,
  PerimeterPortPin,
} from "./schemas.js";
import {
  defaultCadLayers,
  escapeXml,
  layerDisplayAttr,
  renderBreakerGlyph,
  renderCadGridAndBorder,
  renderEngineeringTitleBlock,
  resolveRedlineStroke,
} from "./svg_primitives.js";

export interface Point2D {
  readonly x: number;
  readonly y: number;
}

export interface PositionedPortE1 {
  readonly portId: string;
  readonly label: string;
  readonly side: "WEST" | "EAST" | "SOUTH";
  readonly x: number;
  readonly y: number;
  readonly guarded: boolean;
}

export interface PositionedIcBlockE1 {
  readonly room: BlueprintRoom;
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
  readonly ports: readonly PositionedPortE1[];
}

export interface RoutedBusE1 {
  readonly bus: BlueprintBus;
  readonly points: readonly Point2D[];
  readonly svgPath: string;
  readonly midpoint: Point2D;
  readonly isOrthogonal: boolean;
}

export interface SheetE1Layout {
  readonly sheetCode: "E-1";
  readonly canvasWidth: number;
  readonly canvasHeight: number;
  readonly blocks: readonly PositionedIcBlockE1[];
  readonly routedBuses: readonly RoutedBusE1[];
}

export interface SheetE1RenderOptions {
  readonly layers?: CadLayerState;
  readonly selectedRoomId?: string | null;
  readonly selectedBusId?: string | null;
}

export interface PolylineCheckInput {
  readonly points: readonly Point2D[];
}

const PAD_X = 56;
const PAD_Y = 52;
const IC_WIDTH = 248;

export function isPolylineOrthogonal(input: PolylineCheckInput): boolean {
  const pts = input.points;
  for (let idx = 1; idx < pts.length; idx += 1) {
    const prev = pts[idx - 1];
    const curr = pts[idx];
    if (prev === undefined || curr === undefined) {
      continue;
    }
    const dx = Math.abs(curr.x - prev.x);
    const dy = Math.abs(curr.y - prev.y);
    if (dx > 0.5 && dy > 0.5) {
      return false;
    }
  }
  return pts.length >= 2;
}

function buildElkPortsForRoom(
  room: BlueprintRoom,
  inboundPins: readonly PerimeterPortPin[],
  outboundBuses: readonly BlueprintBus[],
  risers: readonly ExternalRiser[],
): { readonly elkPorts: ElkPort[]; readonly metaById: Map<string, PositionedPortE1> } {
  const elkPorts: ElkPort[] = [];
  const metaById = new Map<string, PositionedPortE1>();
  for (const pin of inboundPins.slice(0, 8)) {
    elkPorts.push({
      id: pin.pin_id,
      width: 8,
      height: 8,
      layoutOptions: { "elk.port.side": "WEST" },
    });
    metaById.set(pin.pin_id, {
      portId: pin.pin_id,
      label: pin.symbol_name,
      side: "WEST",
      x: 0,
      y: 0,
      guarded: pin.has_circuit_breaker,
    });
  }
  for (const bus of outboundBuses) {
    const outId = `${room.room_id}::elk-out:${bus.target_room}`;
    elkPorts.push({
      id: outId,
      width: 8,
      height: 8,
      layoutOptions: { "elk.port.side": "EAST" },
    });
    metaById.set(outId, {
      portId: outId,
      label: bus.target_room.split("/").at(-1) ?? bus.target_room,
      side: "EAST",
      x: 0,
      y: 0,
      guarded: bus.has_breaker_guard,
    });
  }
  for (const riser of risers.slice(0, 4)) {
    elkPorts.push({
      id: riser.riser_id,
      width: 8,
      height: 8,
      layoutOptions: { "elk.port.side": "SOUTH" },
    });
    metaById.set(riser.riser_id, {
      portId: riser.riser_id,
      label: `${riser.kind}:${riser.target_name}`,
      side: "SOUTH",
      x: 0,
      y: 0,
      guarded: true,
    });
  }
  return { elkPorts, metaById };
}

function buildElkRootGraph(
  graph: BlueprintGraph,
): { readonly root: ElkNode; readonly portMeta: Map<string, PositionedPortE1> } {
  const allMeta = new Map<string, PositionedPortE1>();
  const validRoomIds = new Set<string>(graph.rooms.map((r: BlueprintRoom) => r.room_id));
  const children: ElkNode[] = graph.rooms.map((room: BlueprintRoom): ElkNode => {
    const inPins = graph.port_pins.filter(
      (p: PerimeterPortPin) => p.room_id === room.room_id && p.direction === "inbound_port",
    );
    const outBuses = graph.buses.filter(
      (b: BlueprintBus) => b.source_room === room.room_id && validRoomIds.has(b.target_room),
    );
    const risers = graph.external_risers.filter((r: ExternalRiser) => r.room_id === room.room_id);
    const built = buildElkPortsForRoom(room, inPins, outBuses, risers);
    for (const [k, v] of built.metaById.entries()) {
      allMeta.set(k, v);
    }
    const pinRows = Math.max(inPins.slice(0, 8).length, outBuses.length, 2);
    return {
      id: room.room_id,
      width: IC_WIDTH,
      height: Math.max(124, 56 + pinRows * 22),
      ports: built.elkPorts,
      layoutOptions: { "elk.portConstraints": "FIXED_SIDE" },
    };
  });

  const edges: ElkExtendedEdge[] = graph.buses
    .filter((b: BlueprintBus) => validRoomIds.has(b.source_room) && validRoomIds.has(b.target_room))
    .map((bus: BlueprintBus): ElkExtendedEdge => {
      const srcPort = `${bus.source_room}::elk-out:${bus.target_room}`;
      const firstSym = bus.port_symbols[0];
      const candidateTargetPin =
        firstSym !== undefined ? `${bus.target_room}::in:${firstSym}` : "";
      const dstTarget = allMeta.has(candidateTargetPin) ? candidateTargetPin : bus.target_room;
      return {
        id: bus.bus_id,
        sources: [allMeta.has(srcPort) ? srcPort : bus.source_room],
        targets: [dstTarget],
      };
    });

  return {
    root: {
      id: "blueprint-e1-root",
      layoutOptions: {
        "elk.algorithm": "layered",
        "elk.direction": "RIGHT",
        "elk.edgeRouting": "ORTHOGONAL",
        "elk.layered.spacing.nodeNodeBetweenLayers": "116",
        "elk.spacing.nodeNode": "64",
        "elk.spacing.edgeNode": "28",
        "elk.spacing.edgeEdge": "18",
      },
      children,
      edges,
    },
    portMeta: allMeta,
  };
}

function extractSectionPoints(section: ElkEdgeSection | undefined): Point2D[] {
  if (section === undefined) {
    return [];
  }
  const raw: Point2D[] = [{ x: section.startPoint.x + PAD_X, y: section.startPoint.y + PAD_Y }];
  for (const bp of section.bendPoints ?? []) {
    raw.push({ x: bp.x + PAD_X, y: bp.y + PAD_Y });
  }
  raw.push({ x: section.endPoint.x + PAD_X, y: section.endPoint.y + PAD_Y });
  return raw;
}

function mapChildPorts(
  child: ElkNode,
  bx: number,
  by: number,
  portMeta: Map<string, PositionedPortE1>,
): PositionedPortE1[] {
  const ports: PositionedPortE1[] = [];
  for (const ep of child.ports ?? []) {
    const meta = portMeta.get(ep.id);
    if (meta !== undefined) {
      ports.push({
        ...meta,
        x: bx + (ep.x ?? 0),
        y: by + (ep.y ?? 0),
      });
    }
  }
  return ports;
}

function extractPositionedBlocks(
  laidOut: ElkNode,
  graph: BlueprintGraph,
  portMeta: Map<string, PositionedPortE1>,
): PositionedIcBlockE1[] {
  const roomById = new Map<string, BlueprintRoom>(
    graph.rooms.map((r: BlueprintRoom) => [r.room_id, r]),
  );
  const blocks: PositionedIcBlockE1[] = [];
  for (const child of laidOut.children ?? []) {
    const room = roomById.get(child.id);
    if (room === undefined) {
      continue;
    }
    const bx = (child.x ?? 0) + PAD_X;
    const by = (child.y ?? 0) + PAD_Y;
    blocks.push({
      room,
      x: bx,
      y: by,
      width: child.width ?? IC_WIDTH,
      height: child.height ?? 132,
      ports: mapChildPorts(child, bx, by, portMeta),
    });
  }
  return blocks;
}

function extractRoutedBuses(laidOut: ElkNode, graph: BlueprintGraph): RoutedBusE1[] {
  const busById = new Map<string, BlueprintBus>(
    graph.buses.map((b: BlueprintBus) => [b.bus_id, b]),
  );
  const routed: RoutedBusE1[] = [];
  for (const edge of laidOut.edges ?? []) {
    const bus = busById.get(edge.id);
    const pts = extractSectionPoints(edge.sections?.[0]);
    if (bus === undefined || pts.length < 2) {
      continue;
    }
    const svgPath = pts
      .map((pt: Point2D, idx: number): string =>
        `${idx === 0 ? "M" : "L"} ${pt.x.toFixed(1)} ${pt.y.toFixed(1)}`,
      )
      .join(" ");
    const midIdx = Math.floor(pts.length / 2);
    const a = pts[midIdx - 1] ?? pts[0] ?? { x: 0, y: 0 };
    const b = pts[midIdx] ?? a;
    routed.push({
      bus,
      points: pts,
      svgPath,
      midpoint: { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 },
      isOrthogonal: isPolylineOrthogonal({ points: pts }),
    });
  }
  return routed;
}

export async function layoutSheetE1(graph: BlueprintGraph): Promise<SheetE1Layout> {
  const elk = new ELK();
  const { root, portMeta } = buildElkRootGraph(graph);
  const laidOut = await elk.layout(root);
  const blocks = extractPositionedBlocks(laidOut, graph, portMeta);
  const routedBuses = extractRoutedBuses(laidOut, graph);
  const canvasWidth = Math.max(980, Math.ceil((laidOut.width ?? 820) + PAD_X * 2 + 48));
  const canvasHeight = Math.max(380, Math.ceil((laidOut.height ?? 220) + PAD_Y * 2 + 128));
  return {
    sheetCode: "E-1",
    canvasWidth,
    canvasHeight,
    blocks,
    routedBuses,
  };
}

function renderIcBlockSvg(block: PositionedIcBlockE1, selectedRoomId: string | null): string {
  const { room, x, y, width, height, ports } = block;
  const isSel = selectedRoomId === room.room_id;
  const style = resolveRedlineStroke(room.redline_status, isSel ? "#fbbf24" : "#60a5fa");
  const dash = style.dashArray ? ` stroke-dasharray="${style.dashArray}"` : "";
  const pinMarkup = ports
    .filter((p) => p.side !== "SOUTH")
    .map((p: PositionedPortE1): string => {
      const pinColor = p.guarded ? "#34d399" : "#f87171";
      const labelX = p.side === "WEST" ? p.x + 12 : p.x - 6;
      const anchor = p.side === "WEST" ? "start" : "end";
      return `<g><rect x="${(p.x - 4).toFixed(1)}" y="${(p.y - 4).toFixed(1)}" width="8" height="8" fill="${pinColor}"/><text x="${labelX.toFixed(1)}" y="${(p.y + 3).toFixed(1)}" text-anchor="${anchor}" fill="#cbd5e1" font-family="monospace" font-size="9">${escapeXml(p.label.slice(0, 18))}</text></g>`;
    })
    .join("\n");
  return [
    `<g class="cad-ic-block" data-room-id="${escapeXml(room.room_id)}">`,
    `  <rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${width}" height="${height}" rx="3" fill="#0b1f38" stroke="${style.stroke}" stroke-width="${isSel ? 3 : 2}"${dash}/>`,
    `  <rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${width}" height="28" fill="#123055"/>`,
    `  <text x="${(x + 10).toFixed(1)}" y="${(y + 18).toFixed(1)}" fill="#f8fafc" font-family="monospace" font-size="12" font-weight="700">IC: ${escapeXml(room.room_id)}</text>`,
    pinMarkup,
    "</g>",
  ].join("\n");
}

function renderRiserStubsSvg(blocks: readonly PositionedIcBlockE1[]): string {
  const lines: string[] = [];
  for (const blk of blocks) {
    for (const sp of blk.ports.filter((p) => p.side === "SOUTH")) {
      lines.push(
        `<g><line x1="${sp.x.toFixed(1)}" y1="${sp.y.toFixed(1)}" x2="${sp.x.toFixed(1)}" y2="${(sp.y + 22).toFixed(1)}" stroke="#f59e0b" stroke-width="1.6" stroke-dasharray="3,2"/><circle cx="${sp.x.toFixed(1)}" cy="${(sp.y + 25).toFixed(1)}" r="3.5" fill="#fbbf24"/><text x="${sp.x.toFixed(1)}" y="${(sp.y + 38).toFixed(1)}" text-anchor="middle" fill="#fde68a" font-family="monospace" font-size="8.5">${escapeXml(sp.label.slice(0, 20))}</text></g>`,
      );
    }
  }
  return lines.join("\n");
}

export async function renderSheetE1Svg(
  graph: BlueprintGraph,
  options?: SheetE1RenderOptions,
): Promise<string> {
  const layers = options?.layers ?? defaultCadLayers();
  const selectedRoom = options?.selectedRoomId ?? null;
  const selectedBus = options?.selectedBusId ?? null;
  const layout = await layoutSheetE1(graph);

  const baseBlocks = layout.blocks
    .filter((b) => b.room.redline_status === "existing")
    .map((b) => renderIcBlockSvg(b, selectedRoom))
    .join("\n");
  const redBlocks = layout.blocks
    .filter((b) => b.room.redline_status !== "existing")
    .map((b) => renderIcBlockSvg(b, selectedRoom))
    .join("\n");
  const baseBuses = layout.routedBuses
    .filter((rb) => rb.bus.redline_status === "existing")
    .map((rb: RoutedBusE1): string => {
      const isSel = selectedBus === rb.bus.bus_id;
      return `<path class="cad-bus" data-bus-id="${escapeXml(rb.bus.bus_id)}" d="${rb.svgPath}" fill="none" stroke="${isSel ? "#fbbf24" : "#38bdf8"}" stroke-width="${isSel ? 2.8 : 1.8}" marker-end="url(#arrow-e-1)"/>`;
    })
    .join("\n");
  const redBuses = layout.routedBuses
    .filter((rb) => rb.bus.redline_status !== "existing")
    .map((rb: RoutedBusE1): string => {
      const st = resolveRedlineStroke(rb.bus.redline_status, "#38bdf8");
      return `<path class="cad-bus" data-bus-id="${escapeXml(rb.bus.bus_id)}" d="${rb.svgPath}" fill="none" stroke="${st.stroke}" stroke-width="2" stroke-dasharray="${st.dashArray}" marker-end="url(#arrow-e-1)"/>`;
    })
    .join("\n");
  const breakerGlyphs = layout.routedBuses
    .map((rb: RoutedBusE1): string =>
      renderBreakerGlyph({
        x: rb.midpoint.x,
        y: rb.midpoint.y,
        guarded: rb.bus.has_breaker_guard,
        label: rb.bus.bus_id,
      }),
    )
    .join("\n");

  return [
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${layout.canvasWidth} ${layout.canvasHeight}" width="${layout.canvasWidth}" height="${layout.canvasHeight}" data-sheet="E-1">`,
    renderCadGridAndBorder({
      width: layout.canvasWidth,
      height: layout.canvasHeight,
      sheetCode: "E-1",
    }),
    `<g data-cad-layer="structure"${layerDisplayAttr(layers.structure)}>`,
    baseBuses,
    baseBlocks,
    "</g>",
    `<g data-cad-layer="redline_diff"${layerDisplayAttr(layers.redline_diff)}>`,
    redBuses,
    redBlocks,
    "</g>",
    `<g data-cad-layer="breakers"${layerDisplayAttr(layers.breakers)}>`,
    breakerGlyphs,
    "</g>",
    `<g data-cad-layer="risers"${layerDisplayAttr(layers.risers)}>`,
    renderRiserStubsSvg(layout.blocks),
    "</g>",
    renderEngineeringTitleBlock({
      sheetCode: "E-1",
      sheetSubtitle: "IC WIRING SCHEMATIC (ELK MANHATTAN)",
      graph,
      width: layout.canvasWidth,
      yTop: layout.canvasHeight - 92,
    }),
    "</svg>",
  ].join("\n");
}
