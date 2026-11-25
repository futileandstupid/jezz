import { layoutSheetA1, renderSheetA1Svg, type SheetA1Layout } from "./floorplan_a1.js";
import { layoutSheetE1, renderSheetE1Svg, type SheetE1Layout } from "./schematic_e1.js";
import {
  CadViewerStateSchema,
  type BlueprintGraph,
  type CadLayerKey,
  type CadSheetId,
  type CadViewerState,
} from "./schemas.js";
import { defaultCadLayers, escapeXml } from "./svg_primitives.js";

export interface RoomSelectionInput {
  readonly roomId: string | null;
}

export interface BusSelectionInput {
  readonly busId: string | null;
}

export interface ViewportZoomInput {
  readonly zoomScale: number;
  readonly panX: number;
  readonly panY: number;
}

export interface CadRenderedBundle {
  readonly state: CadViewerState;
  readonly layoutA1: SheetA1Layout;
  readonly layoutE1: SheetE1Layout;
  readonly svgA1: string;
  readonly svgE1: string;
  readonly html: string;
}

export function createInitialCadState(initialSheet: CadSheetId = "A-1"): CadViewerState {
  return CadViewerStateSchema.parse({
    activeSheet: initialSheet,
    selectedRoomId: null,
    selectedBusId: null,
    zoomScale: 1.0,
    panX: 0,
    panY: 0,
    layers: defaultCadLayers(),
  });
}

export function switchCadSheet(state: CadViewerState, targetSheet: CadSheetId): CadViewerState {
  return CadViewerStateSchema.parse({
    ...state,
    activeSheet: targetSheet,
  });
}

export function selectCadRoom(
  state: CadViewerState,
  selection: RoomSelectionInput,
): CadViewerState {
  return CadViewerStateSchema.parse({
    ...state,
    selectedRoomId: selection.roomId,
  });
}

export function selectCadBus(
  state: CadViewerState,
  selection: BusSelectionInput,
): CadViewerState {
  return CadViewerStateSchema.parse({
    ...state,
    selectedBusId: selection.busId,
  });
}

export function toggleCadLayer(state: CadViewerState, layer: CadLayerKey): CadViewerState {
  return CadViewerStateSchema.parse({
    ...state,
    layers: {
      ...state.layers,
      [layer]: !state.layers[layer],
    },
  });
}

export function zoomCadViewport(
  state: CadViewerState,
  viewport: ViewportZoomInput,
): CadViewerState {
  const clampedZoom = Math.min(4.0, Math.max(0.25, viewport.zoomScale));
  const clampedPanX = Math.min(10_000, Math.max(-10_000, viewport.panX));
  const clampedPanY = Math.min(10_000, Math.max(-10_000, viewport.panY));
  return CadViewerStateSchema.parse({
    ...state,
    zoomScale: clampedZoom,
    panX: clampedPanX,
    panY: clampedPanY,
  });
}

export function exportActiveSheetSvg(bundle: CadRenderedBundle): string {
  return bundle.state.activeSheet === "A-1" ? bundle.svgA1 : bundle.svgE1;
}

function buildInteractiveScript(): string {
  return [
    "const state = { activeSheet: document.body.dataset.initialSheet || 'A-1', selectedRoomId: null, zoom: 1.0 };",
    "function syncView() {",
    "  document.getElementById('pane-a1').style.display = state.activeSheet === 'A-1' ? 'block' : 'none';",
    "  document.getElementById('pane-e1').style.display = state.activeSheet === 'E-1' ? 'block' : 'none';",
    "  document.getElementById('btn-a1').dataset.active = String(state.activeSheet === 'A-1');",
    "  document.getElementById('btn-e1').dataset.active = String(state.activeSheet === 'E-1');",
    "  document.getElementById('stage-wrap').style.transform = `scale(${state.zoom})`;",
    "  document.getElementById('selection-badge').textContent = state.selectedRoomId ? `FOCUS: ${state.selectedRoomId}` : 'FOCUS: ALL ROOMS';",
    "}",
    "document.getElementById('btn-a1').addEventListener('click', () => { state.activeSheet = 'A-1'; syncView(); });",
    "document.getElementById('btn-e1').addEventListener('click', () => { state.activeSheet = 'E-1'; syncView(); });",
    "document.querySelectorAll('[data-layer-toggle]').forEach((cb) => {",
    "  cb.addEventListener('change', (ev) => {",
    "    const key = ev.target.dataset.layerToggle;",
    "    document.querySelectorAll(`[data-cad-layer='${key}']`).forEach((g) => {",
    "      if (ev.target.checked) { g.removeAttribute('display'); } else { g.setAttribute('display', 'none'); }",
    "    });",
    "  });",
    "});",
    "document.querySelectorAll('[data-room-id]').forEach((el) => {",
    "  el.addEventListener('click', () => { state.selectedRoomId = el.dataset.roomId || null; syncView(); });",
    "});",
    "document.getElementById('btn-export-svg').addEventListener('click', () => {",
    "  const activeId = state.activeSheet === 'A-1' ? 'pane-a1' : 'pane-e1';",
    "  const svgEl = document.getElementById(activeId).querySelector('svg');",
    "  const blob = new Blob([svgEl.outerHTML], { type: 'image/svg+xml;charset=utf-8' });",
    "  const link = document.createElement('a');",
    "  link.href = URL.createObjectURL(blob);",
    "  link.download = `blueprint-${state.activeSheet.toLowerCase()}.svg`;",
    "  link.click();",
    "});",
    "syncView();",
  ].join("\n");
}

export async function renderCadBundle(
  graph: BlueprintGraph,
  initialState?: CadViewerState,
): Promise<CadRenderedBundle> {
  const state = initialState ?? createInitialCadState("A-1");
  const layoutA1 = layoutSheetA1(graph);
  const layoutE1 = await layoutSheetE1(graph);
  const renderOpts = {
    layers: state.layers,
    selectedRoomId: state.selectedRoomId,
    selectedBusId: state.selectedBusId,
  };
  const svgA1 = renderSheetA1Svg(graph, renderOpts);
  const svgE1 = await renderSheetE1Svg(graph, renderOpts);
  const safeTitle = escapeXml(graph.title);

  const html = [
    "<!DOCTYPE html>",
    '<html lang="en"><head><meta charset="utf-8"/>',
    `<title>Jezz CAD Blueprint — ${safeTitle}</title>`,
    "<style>",
    "body { margin: 0; background: #040d1a; color: #e2e8f0; font-family: ui-monospace, monospace; }",
    "header { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; padding: 12px 20px; background: #09192f; border-bottom: 1px solid #1e3a5f; }",
    "button { background: #102a4c; color: #dbeafe; border: 1px solid #3b82f6; padding: 6px 12px; border-radius: 4px; cursor: pointer; font-family: inherit; }",
    "button[data-active='true'] { background: #2563eb; color: #ffffff; font-weight: 700; }",
    "label { font-size: 12px; display: inline-flex; align-items: center; gap: 4px; }",
    "#stage-wrap { transform-origin: top left; padding: 16px; overflow: auto; }",
    "</style></head>",
    `<body data-initial-sheet="${state.activeSheet}">`,
    "<header>",
    `  <strong>JEZZ CAD :: ${safeTitle}</strong>`,
    '  <button id="btn-a1" type="button">Sheet A-1 (Floor Plan)</button>',
    '  <button id="btn-e1" type="button">Sheet E-1 (Wiring Schematic)</button>',
    '  <label><input type="checkbox" data-layer-toggle="structure" checked/>Structure</label>',
    '  <label><input type="checkbox" data-layer-toggle="breakers" checked/>Breakers</label>',
    '  <label><input type="checkbox" data-layer-toggle="risers" checked/>Taint &amp; I/O Risers</label>',
    '  <label><input type="checkbox" data-layer-toggle="redline_diff" checked/>Redline Diff</label>',
    '  <span id="selection-badge">FOCUS: ALL ROOMS</span>',
    '  <button id="btn-export-svg" type="button">Export .svg</button>',
    "</header>",
    '<main id="stage-wrap">',
    `  <section id="pane-a1">${svgA1}</section>`,
    `  <section id="pane-e1">${svgE1}</section>`,
    "</main>",
    `<script>${buildInteractiveScript()}</script>`,
    "</body></html>",
  ].join("\n");

  return { state, layoutA1, layoutE1, svgA1, svgE1, html };
}
