import type {
  BlueprintGraph,
  CadLayerState,
  CadSheetId,
  RedlineStatus,
} from "./schemas.js";

export interface CadCanvasFrameProps {
  readonly width: number;
  readonly height: number;
  readonly sheetCode: CadSheetId;
}

export interface RedlineStrokeStyle {
  readonly stroke: string;
  readonly dashArray: string;
}

export interface BreakerGlyphProps {
  readonly x: number;
  readonly y: number;
  readonly guarded: boolean;
  readonly label: string;
}

export interface TitleBlockProps {
  readonly sheetCode: CadSheetId;
  readonly sheetSubtitle: string;
  readonly graph: BlueprintGraph;
  readonly width: number;
  readonly yTop: number;
}

export function escapeXml(raw: string): string {
  if (typeof raw !== "string" || raw.length > 262_144) {
    throw new RangeError("XML text input exceeds maximum bounded length (262,144).");
  }
  return raw
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&apos;");
}

export function resolveRedlineStroke(
  status: RedlineStatus,
  baseStroke: string,
): RedlineStrokeStyle {
  if (typeof baseStroke !== "string" || baseStroke.length === 0 || baseStroke.length > 64) {
    throw new RangeError("Base SVG stroke color must be a non-empty bounded string.");
  }
  if (status === "proposed_add") {
    return { stroke: "#34d399", dashArray: "6,3" };
  }
  if (status === "proposed_remove") {
    return { stroke: "#f87171", dashArray: "4,4" };
  }
  return { stroke: baseStroke, dashArray: "" };
}

export function layerDisplayAttr(visible: boolean): string {
  return visible ? "" : ' display="none"';
}

export function defaultCadLayers(): CadLayerState {
  return {
    structure: true,
    breakers: true,
    risers: true,
    redline_diff: true,
  };
}

export function renderCadGridAndBorder(props: CadCanvasFrameProps): string {
  const { width, height, sheetCode } = props;
  const patternId = `cad-grid-${sheetCode.toLowerCase()}`;
  const innerW = Math.max(40, width - 24);
  const innerH = Math.max(40, height - 24);
  const outerW = Math.max(48, width - 16);
  const outerH = Math.max(48, height - 16);
  return [
    "<defs>",
    `  <pattern id="${patternId}" width="24" height="24" patternUnits="userSpaceOnUse">`,
    `    <path d="M 24 0 L 0 0 0 24" fill="none" stroke="#132946" stroke-width="0.8"/>`,
    "  </pattern>",
    `  <marker id="arrow-${sheetCode.toLowerCase()}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">`,
    '    <path d="M 0 1 L 10 5 L 0 9 z" fill="#60a5fa"/>',
    "  </marker>",
    "</defs>",
    `<rect width="${width}" height="${height}" fill="#071324"/>`,
    `<rect x="12" y="12" width="${innerW}" height="${innerH}" fill="url(#${patternId})" stroke="#2d5a88" stroke-width="2"/>`,
    `<rect x="8" y="8" width="${outerW}" height="${outerH}" fill="none" stroke="#1e3f63" stroke-width="1"/>`,
  ].join("\n");
}

export function renderBreakerGlyph(props: BreakerGlyphProps): string {
  const { x, y, guarded, label } = props;
  const fill = guarded ? "#082820" : "#3a1219";
  const stroke = guarded ? "#34d399" : "#f87171";
  const tag = guarded ? "[CB]" : "[!]";
  return [
    `<g transform="translate(${x.toFixed(1)},${y.toFixed(1)})">`,
    `  <rect x="-18" y="-9" width="36" height="18" rx="3" fill="${fill}" stroke="${stroke}" stroke-width="1.4"/>`,
    `  <text x="0" y="4" text-anchor="middle" fill="${stroke}" font-family="monospace" font-size="9.5" font-weight="700">${escapeXml(tag || label)}</text>`,
    "</g>",
  ].join("\n");
}

export function renderEngineeringTitleBlock(props: TitleBlockProps): string {
  const { sheetCode, sheetSubtitle, graph, width, yTop } = props;
  const boxW = Math.max(320, width - 48);
  const splitX = Math.max(200, width - 360);
  const inboundCount = graph.port_pins.filter((p) => p.direction === "inbound_port").length;
  const titleText = escapeXml(graph.title.slice(0, 64));
  const subtitle = escapeXml(`${sheetCode} — ${sheetSubtitle}`);
  const edition = escapeXml(
    `EDITION: JBC ${graph.schema_version} (${graph.profile.toUpperCase()})`,
  );
  const counts = escapeXml(
    `ROOMS: ${graph.rooms.length} | INBOUND PORTS: ${inboundCount} | BUSES: ${graph.buses.length} | RISERS: ${graph.external_risers.length}`,
  );
  const digest = escapeXml(`NETLIST SEAL: ${graph.netlist_digest.slice(0, 39)}...`);
  return [
    `<g id="title-block-${sheetCode.toLowerCase()}">`,
    `  <rect x="24" y="${yTop}" width="${boxW}" height="68" fill="#0b1d36" stroke="#3b82f6" stroke-width="1.6"/>`,
    `  <line x1="${splitX}" y1="${yTop}" x2="${splitX}" y2="${yTop + 68}" stroke="#2d5a88" stroke-width="1.2"/>`,
    `  <text x="38" y="${yTop + 24}" fill="#e2e8f0" font-family="monospace" font-size="14" font-weight="700">${subtitle} :: ${titleText}</text>`,
    `  <text x="38" y="${yTop + 46}" fill="#93c5fd" font-family="monospace" font-size="11">${counts}</text>`,
    `  <text x="${splitX + 14}" y="${yTop + 24}" fill="#bfdbfe" font-family="monospace" font-size="11" font-weight="700">${edition}</text>`,
    `  <text x="${splitX + 14}" y="${yTop + 46}" fill="#6ee7b7" font-family="monospace" font-size="10">${digest}</text>`,
    "</g>",
  ].join("\n");
}
