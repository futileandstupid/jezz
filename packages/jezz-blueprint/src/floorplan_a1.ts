import {
  WALL_THICKNESS_BY_RATING,
  type BlueprintBus,
  type BlueprintGraph,
  type BlueprintRoom,
  type CadLayerState,
} from "./schemas.js";
import {
  defaultCadLayers,
  escapeXml,
  layerDisplayAttr,
  renderCadGridAndBorder,
  renderEngineeringTitleBlock,
  resolveRedlineStroke,
} from "./svg_primitives.js";

export interface RoomBoxA1 {
  readonly room: BlueprintRoom;
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
  readonly wallStrokePx: number;
  readonly occupancyRatio: number;
}

export interface SheetA1Layout {
  readonly sheetCode: "A-1";
  readonly canvasWidth: number;
  readonly canvasHeight: number;
  readonly rooms: readonly RoomBoxA1[];
}

export interface SheetA1RenderOptions {
  readonly layers?: CadLayerState;
  readonly selectedRoomId?: string | null;
  readonly selectedBusId?: string | null;
}

const MIN_ROOM_WIDTH = 220;
const MAX_ROOM_WIDTH = 420;
const ROOM_HEIGHT = 148;
const H_GAP = 28;
const V_GAP = 48;
const MARGIN_X = 44;
const TOP_OFFSET = 44;

function groupRoomsByRank(rooms: readonly BlueprintRoom[]): Map<number, BlueprintRoom[]> {
  const byRank = new Map<number, BlueprintRoom[]>();
  const ordered = [...rooms].sort((a: BlueprintRoom, b: BlueprintRoom): number => {
    if (a.topological_rank !== b.topological_rank) {
      return a.topological_rank - b.topological_rank;
    }
    return a.room_id.localeCompare(b.room_id);
  });
  for (const room of ordered) {
    const existing = byRank.get(room.topological_rank);
    if (existing !== undefined) {
      existing.push(room);
    } else {
      byRank.set(room.topological_rank, [room]);
    }
  }
  return byRank;
}

function computeProportionalWidth(room: BlueprintRoom, rankTotalModules: number, count: number): number {
  const safeDenom = Math.max(1, rankTotalModules);
  const targetRowSpan = Math.min(1360, Math.max(360, count * 290));
  const massWeight = Math.max(1, room.module_count);
  const rawWidth = Math.round((massWeight / safeDenom) * targetRowSpan);
  return Math.min(MAX_ROOM_WIDTH, Math.max(MIN_ROOM_WIDTH, rawWidth));
}

export function layoutSheetA1(graph: BlueprintGraph): SheetA1Layout {
  const byRank = groupRoomsByRank(graph.rooms);
  const rankKeys = [...byRank.keys()].sort((a: number, b: number): number => a - b);
  const boxes: RoomBoxA1[] = [];
  let maxRowRight = 980;
  let rowIndex = 0;

  for (const rank of rankKeys) {
    const rowRooms = byRank.get(rank) ?? [];
    const totalMass = rowRooms.reduce(
      (acc: number, r: BlueprintRoom): number => acc + Math.max(1, r.module_count),
      0,
    );
    let cursorX = MARGIN_X;
    const y = TOP_OFFSET + rowIndex * (ROOM_HEIGHT + V_GAP);
    for (const room of rowRooms) {
      const width = computeProportionalWidth(room, totalMass, rowRooms.length);
      const safeMax = Math.max(1, room.max_modules);
      boxes.push({
        room,
        x: cursorX,
        y,
        width,
        height: ROOM_HEIGHT,
        wallStrokePx: WALL_THICKNESS_BY_RATING[room.wall_rating],
        occupancyRatio: Math.min(1, room.module_count / safeMax),
      });
      cursorX += width + H_GAP;
    }
    maxRowRight = Math.max(maxRowRight, cursorX + MARGIN_X);
    rowIndex += 1;
  }

  const canvasHeight = Math.max(340, TOP_OFFSET + Math.max(1, rowIndex) * (ROOM_HEIGHT + V_GAP) + 104);
  return {
    sheetCode: "A-1",
    canvasWidth: maxRowRight,
    canvasHeight,
    rooms: boxes,
  };
}

function renderSingleRoomBoxA1(
  box: RoomBoxA1,
  graph: BlueprintGraph,
  selectedRoomId: string | null,
): string {
  const { room, x, y, width, height, wallStrokePx, occupancyRatio } = box;
  const isSelected = selectedRoomId === room.room_id;
  const style = resolveRedlineStroke(room.redline_status, isSelected ? "#fbbf24" : "#60a5fa");
  const dashAttr = style.dashArray ? ` stroke-dasharray="${style.dashArray}"` : "";
  const barFullW = width - 28;
  const barFillW = Math.max(4, Math.round(barFullW * occupancyRatio));
  const overLimit = room.module_count > room.max_modules;
  const barColor = overLimit ? "#f87171" : "#38bdf8";
  const roomPins = graph.invariant_pins
    .filter((p) => p.room_id === room.room_id)
    .map((p) => p.invariant_id)
    .slice(0, 3);
  const invLine = roomPins.length > 0 ? `INV: ${roomPins.join(", ")}` : `RANK L${room.topological_rank}`;
  return [
    `<g class="cad-room" data-room-id="${escapeXml(room.room_id)}">`,
    `  <rect x="${x}" y="${y}" width="${width}" height="${height}" rx="4" fill="#0d223d" stroke="${style.stroke}" stroke-width="${isSelected ? wallStrokePx + 1.5 : wallStrokePx}"${dashAttr}/>`,
    `  <text x="${x + 14}" y="${y + 24}" fill="#f8fafc" font-family="monospace" font-size="13" font-weight="700">${escapeXml(room.room_id)}</text>`,
    `  <text x="${x + 14}" y="${y + 44}" fill="#93c5fd" font-family="monospace" font-size="10.5">${escapeXml(room.language.toUpperCase())} | ${escapeXml(room.wall_rating)}</text>`,
    `  <rect x="${x + 14}" y="${y + 58}" width="${barFullW}" height="10" rx="2" fill="#071324" stroke="#1e3a5f" stroke-width="1"/>`,
    `  <rect x="${x + 14}" y="${y + 58}" width="${barFillW}" height="10" rx="2" fill="${barColor}"/>`,
    `  <text x="${x + 14}" y="${y + 86}" fill="${overLimit ? "#fca5a5" : "#bae6fd"}" font-family="monospace" font-size="11" font-weight="700">OCCUPANCY: ${room.module_count}/${room.max_modules} MODULES</text>`,
    `  <text x="${x + 14}" y="${y + 106}" fill="#cbd5e1" font-family="monospace" font-size="10">PUBLIC PORTS: ${room.public_symbol_count}</text>`,
    `  <text x="${x + 14}" y="${y + 126}" fill="#a7f3d0" font-family="monospace" font-size="10">${escapeXml(invLine)}</text>`,
    "</g>",
  ].join("\n");
}

function renderFloorplanConduits(
  layout: SheetA1Layout,
  buses: readonly BlueprintBus[],
  selectedBusId: string | null,
): { readonly baselineSvg: string; readonly redlineSvg: string } {
  const byId = new Map<string, RoomBoxA1>(layout.rooms.map((b: RoomBoxA1) => [b.room.room_id, b]));
  const baseline: string[] = [];
  const redline: string[] = [];
  for (const bus of buses) {
    const src = byId.get(bus.source_room);
    const dst = byId.get(bus.target_room);
    if (src === undefined || dst === undefined) {
      continue;
    }
    const x1 = src.x + src.width / 2;
    const y1 = src.y + src.height;
    const x2 = dst.x + dst.width / 2;
    const y2 = dst.y;
    const midY = Math.round((y1 + y2) / 2);
    const isSel = selectedBusId === bus.bus_id;
    const style = resolveRedlineStroke(bus.redline_status, isSel ? "#fbbf24" : "#3b82f6");
    const dash = style.dashArray ? ` stroke-dasharray="${style.dashArray}"` : "";
    const pathEl = `<path class="cad-bus" data-bus-id="${escapeXml(bus.bus_id)}" d="M ${x1.toFixed(1)} ${y1.toFixed(1)} L ${x1.toFixed(1)} ${midY} L ${x2.toFixed(1)} ${midY} L ${x2.toFixed(1)} ${y2.toFixed(1)}" fill="none" stroke="${style.stroke}" stroke-width="${isSel ? 2.6 : 1.5}"${dash} marker-end="url(#arrow-a-1)"/>`;
    if (bus.redline_status === "existing") {
      baseline.push(pathEl);
    } else {
      redline.push(pathEl);
    }
  }
  return { baselineSvg: baseline.join("\n"), redlineSvg: redline.join("\n") };
}

function renderFloorplanOverlays(layout: SheetA1Layout, graph: BlueprintGraph): {
  readonly breakersSvg: string;
  readonly risersSvg: string;
} {
  const breakers: string[] = [];
  const risers: string[] = [];
  for (const box of layout.rooms) {
    if (box.room.breaker_count > 0) {
      const bx = box.x + box.width - 68;
      const by = box.y + 12;
      breakers.push(
        `<g><rect x="${bx}" y="${by}" width="54" height="18" rx="3" fill="#082820" stroke="#34d399" stroke-width="1.2"/><text x="${bx + 27}" y="${by + 13}" text-anchor="middle" fill="#6ee7b7" font-family="monospace" font-size="9.5" font-weight="700">CB x${box.room.breaker_count}</text></g>`,
      );
    }
    const roomRisers = graph.external_risers.filter((r) => r.room_id === box.room.room_id);
    if (roomRisers.length > 0) {
      const label = roomRisers
        .slice(0, 2)
        .map((r) => r.target_name)
        .join(",");
      risers.push(
        `<text x="${box.x + box.width - 14}" y="${box.y + 126}" text-anchor="end" fill="#fbbf24" font-family="monospace" font-size="9.5">RISERS(${roomRisers.length}): ${escapeXml(label)}</text>`,
      );
    }
  }
  return { breakersSvg: breakers.join("\n"), risersSvg: risers.join("\n") };
}

export function renderSheetA1Svg(
  graph: BlueprintGraph,
  options?: SheetA1RenderOptions,
): string {
  const layers = options?.layers ?? defaultCadLayers();
  const selectedRoom = options?.selectedRoomId ?? null;
  const selectedBus = options?.selectedBusId ?? null;
  const layout = layoutSheetA1(graph);
  const conduits = renderFloorplanConduits(layout, graph.buses, selectedBus);
  const overlays = renderFloorplanOverlays(layout, graph);
  const baselineRooms = layout.rooms
    .filter((b) => b.room.redline_status === "existing")
    .map((b) => renderSingleRoomBoxA1(b, graph, selectedRoom))
    .join("\n");
  const redlineRooms = layout.rooms
    .filter((b) => b.room.redline_status !== "existing")
    .map((b) => renderSingleRoomBoxA1(b, graph, selectedRoom))
    .join("\n");

  return [
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${layout.canvasWidth} ${layout.canvasHeight}" width="${layout.canvasWidth}" height="${layout.canvasHeight}" data-sheet="A-1">`,
    renderCadGridAndBorder({
      width: layout.canvasWidth,
      height: layout.canvasHeight,
      sheetCode: "A-1",
    }),
    `<g data-cad-layer="structure"${layerDisplayAttr(layers.structure)}>`,
    conduits.baselineSvg,
    baselineRooms,
    "</g>",
    `<g data-cad-layer="redline_diff"${layerDisplayAttr(layers.redline_diff)}>`,
    conduits.redlineSvg,
    redlineRooms,
    "</g>",
    `<g data-cad-layer="breakers"${layerDisplayAttr(layers.breakers)}>`,
    overlays.breakersSvg,
    "</g>",
    `<g data-cad-layer="risers"${layerDisplayAttr(layers.risers)}>`,
    overlays.risersSvg,
    "</g>",
    renderEngineeringTitleBlock({
      sheetCode: "A-1",
      sheetSubtitle: "ARCHITECTURAL FLOOR PLAN",
      graph,
      width: layout.canvasWidth,
      yTop: layout.canvasHeight - 92,
    }),
    "</svg>",
  ].join("\n");
}
