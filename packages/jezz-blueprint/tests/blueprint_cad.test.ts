import { describe, expect, it } from "vitest";
import {
  createInitialCadState,
  exportActiveSheetSvg,
  isPolylineOrthogonal,
  layoutSheetA1,
  layoutSheetE1,
  parseBlueprintJson,
  renderCadBundle,
  renderSheetA1Svg,
  selectCadBus,
  selectCadRoom,
  switchCadSheet,
  toggleCadLayer,
  zoomCadViewport,
  type BlueprintGraph,
} from "../src/index.js";

const SAMPLE_DIGEST = "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";

function buildFixtureBlueprint(): BlueprintGraph {
  return parseBlueprintJson(
    JSON.stringify({
      schema_version: "1.3",
      title: "Jezz Polyglot Reference Schematic",
      profile: "standard",
      rooms: [
        {
          room_id: "jezz/agents",
          label: "agents",
          member_id: ".",
          language: "python",
          wall_rating: "compartment_wall",
          module_count: 16,
          max_modules: 35,
          public_symbol_count: 12,
          breaker_count: 10,
          topological_rank: 0,
          redline_status: "existing",
        },
        {
          room_id: "packages/jezz-blueprint",
          label: "jezz-blueprint",
          member_id: "packages/jezz-blueprint",
          language: "typescript",
          wall_rating: "workspace_firewall",
          module_count: 6,
          max_modules: 35,
          public_symbol_count: 9,
          breaker_count: 9,
          topological_rank: 0,
          redline_status: "proposed_add",
        },
        {
          room_id: "jezz/codes",
          label: "codes",
          member_id: ".",
          language: "python",
          wall_rating: "compartment_wall",
          module_count: 34,
          max_modules: 35,
          public_symbol_count: 42,
          breaker_count: 38,
          topological_rank: 1,
          redline_status: "existing",
        },
      ],
      walls: [
        {
          wall_id: "wall:jezz/codes:envelope",
          room_id: "jezz/codes",
          adjacent_room_id: "",
          rating: "compartment_wall",
          thickness_px: 2,
        },
      ],
      port_pins: [
        {
          pin_id: "jezz/codes::in:extract_blueprint",
          room_id: "jezz/codes",
          symbol_name: "extract_blueprint",
          direction: "inbound_port",
          has_circuit_breaker: true,
          redline_status: "existing",
        },
        {
          pin_id: "jezz/codes::in:verify_blueprint_lvs",
          room_id: "jezz/codes",
          symbol_name: "verify_blueprint_lvs",
          direction: "inbound_port",
          has_circuit_breaker: true,
          redline_status: "proposed_add",
        },
      ],
      circuit_breakers: [
        {
          breaker_id: "jezz/codes::cb:extract_blueprint",
          room_id: "jezz/codes",
          symbol_name: "extract_blueprint",
          guard_kind: "jezz_callable",
        },
      ],
      buses: [
        {
          bus_id: "jezz/agents->jezz/codes",
          source_room: "jezz/agents",
          target_room: "jezz/codes",
          port_symbols: ["extract_blueprint", "verify_blueprint_lvs"],
          call_count: 4,
          has_breaker_guard: true,
          redline_status: "existing",
        },
        {
          bus_id: "packages/jezz-blueprint->jezz/codes",
          source_room: "packages/jezz-blueprint",
          target_room: "jezz/codes",
          port_symbols: ["extract_blueprint"],
          call_count: 1,
          has_breaker_guard: true,
          redline_status: "proposed_add",
        },
      ],
      external_risers: [
        {
          riser_id: "packages/jezz-blueprint=>riser:external_package:elkjs",
          room_id: "packages/jezz-blueprint",
          kind: "external_package",
          target_name: "elkjs",
          redline_status: "proposed_add",
        },
      ],
      invariant_pins: [
        {
          invariant_id: "INV-026-02",
          room_id: "packages/jezz-blueprint",
          verification_symbol: "renderCadBundle",
          redline_status: "proposed_add",
        },
      ],
      redlines: [],
      netlist_digest: SAMPLE_DIGEST,
    }),
  );
}

describe("BlueprintGraph strict Zod validation (INV-026-01)", () => {
  it("parses canonical BlueprintGraph payloads and rejects unknown extra fields", () => {
    const graph = buildFixtureBlueprint();
    expect(graph.rooms.length).toBe(3);
    expect(() =>
      parseBlueprintJson(
        JSON.stringify({
          schema_version: "1.3",
          title: "Bad",
          profile: "standard",
          netlist_digest: SAMPLE_DIGEST,
          rogue_field: true,
        }),
      ),
    ).toThrow();
    expect(() => parseBlueprintJson("")).toThrow(RangeError);
  });
});

describe("Sheet A-1 Architectural Floor Plan (INV-026-02)", () => {
  it("enforces spatial permanence, proportional room mass, wall thickness, and occupancy gauges", () => {
    const graph = buildFixtureBlueprint();
    const layout1 = layoutSheetA1(graph);
    const layout2 = layoutSheetA1(graph);
    expect(layout1).toEqual(layout2);

    const agentsBox = layout1.rooms.find((r) => r.room.room_id === "jezz/agents");
    const tsBox = layout1.rooms.find((r) => r.room.room_id === "packages/jezz-blueprint");
    const codesBox = layout1.rooms.find((r) => r.room.room_id === "jezz/codes");
    expect(agentsBox).toBeDefined();
    expect(tsBox).toBeDefined();
    expect(codesBox).toBeDefined();
    expect((agentsBox?.width ?? 0) > (tsBox?.width ?? 0)).toBe(true);
    expect(tsBox?.wallStrokePx).toBe(3);
    expect(codesBox?.wallStrokePx).toBe(2);

    const svg = renderSheetA1Svg(graph);
    expect(svg).toContain('data-sheet="A-1"');
    expect(svg).toContain("OCCUPANCY: 34/35 MODULES");
    expect(svg).toContain("A-1 — ARCHITECTURAL FLOOR PLAN");
    expect(svg).toContain("INV-026-02");
  });
});

describe("Sheet E-1 ELK Manhattan Wiring Schematic (INV-026-02)", () => {
  it("routes cross-room buses orthogonally through fixed WEST/EAST/SOUTH IC perimeter pins", async () => {
    const graph = buildFixtureBlueprint();
    const layout = await layoutSheetE1(graph);
    expect(layout.blocks.length).toBe(3);
    expect(layout.routedBuses.length).toBe(2);
    for (const routed of layout.routedBuses) {
      expect(routed.isOrthogonal).toBe(true);
      expect(isPolylineOrthogonal({ points: routed.points })).toBe(true);
      expect(routed.svgPath.startsWith("M ")).toBe(true);
    }
    const tsBlock = layout.blocks.find((b) => b.room.room_id === "packages/jezz-blueprint");
    expect(tsBlock?.ports.some((p) => p.side === "EAST")).toBe(true);
    expect(tsBlock?.ports.some((p) => p.side === "SOUTH")).toBe(true);
  });
});

describe("Switchable A-1 <-> E-1 CAD state synchronization & SVG export (INV-026-02)", () => {
  it("preserves camera focus, selection state, and layer toggles across sheet switches", async () => {
    const graph = buildFixtureBlueprint();
    let state = createInitialCadState("A-1");
    state = selectCadRoom(state, { roomId: "jezz/codes" });
    state = selectCadBus(state, { busId: "jezz/agents->jezz/codes" });
    state = zoomCadViewport(state, { zoomScale: 1.5, panX: 40, panY: -20 });
    state = toggleCadLayer(state, "breakers");
    const switched = switchCadSheet(state, "E-1");

    expect(switched.activeSheet).toBe("E-1");
    expect(switched.selectedRoomId).toBe("jezz/codes");
    expect(switched.selectedBusId).toBe("jezz/agents->jezz/codes");
    expect(switched.zoomScale).toBe(1.5);
    expect(switched.panX).toBe(40);
    expect(switched.layers.breakers).toBe(false);

    const bundle = await renderCadBundle(graph, switched);
    expect(bundle.svgA1).toContain('data-cad-layer="breakers" display="none"');
    expect(bundle.svgE1).toContain('data-cad-layer="breakers" display="none"');
    expect(bundle.html).toContain("Sheet A-1 (Floor Plan)");
    expect(bundle.html).toContain("Sheet E-1 (Wiring Schematic)");
    expect(bundle.html).toContain("Export .svg");
    expect(exportActiveSheetSvg(bundle)).toContain('data-sheet="E-1"');
  });
});
