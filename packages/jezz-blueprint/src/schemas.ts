import { z } from "zod";

const IdentifierSchema = z.string().min(1).max(256);
const BoundedTextSchema = z.string().min(0).max(4096);
const ShortTextSchema = z.string().min(1).max(512);
const SealDigestSchema = z
  .string()
  .min(71)
  .max(71)
  .regex(/^sha256:[0-9a-f]{64}$/);
const InvariantIdSchema = z
  .string()
  .min(6)
  .max(64)
  .regex(/^INV-[A-Za-z0-9_-]{2,56}$/);

export const SupportedLanguageSchema = z.enum([
  "python",
  "typescript",
  "rust",
  "go",
]);
export type SupportedLanguage = z.infer<typeof SupportedLanguageSchema>;

export const PermitProfileSchema = z.enum(["standard", "strict_internal"]);
export type PermitProfile = z.infer<typeof PermitProfileSchema>;

export const WallFireRatingSchema = z.enum([
  "exterior_envelope",
  "workspace_firewall",
  "compartment_wall",
  "package_lobby",
]);
export type WallFireRating = z.infer<typeof WallFireRatingSchema>;

export const PortDirectionSchema = z.enum(["inbound_port", "outbound_port"]);
export type PortDirection = z.infer<typeof PortDirectionSchema>;

export const RiserKindSchema = z.enum([
  "external_package",
  "subprocess_io",
  "network_io",
  "filesystem_io",
  "database_io",
]);
export type RiserKind = z.infer<typeof RiserKindSchema>;

export const RedlineStatusSchema = z.enum([
  "existing",
  "proposed_add",
  "proposed_remove",
]);
export type RedlineStatus = z.infer<typeof RedlineStatusSchema>;

export const RedlineOpSchema = z.enum([
  "add_room",
  "add_port_pin",
  "add_bus",
  "remove_bus",
  "add_external_riser",
  "bind_invariant_pin",
]);
export type RedlineOp = z.infer<typeof RedlineOpSchema>;

export const BlueprintRoomSchema = z
  .object({
    room_id: IdentifierSchema,
    label: IdentifierSchema,
    member_id: IdentifierSchema.default("."),
    language: SupportedLanguageSchema.default("python"),
    wall_rating: WallFireRatingSchema.default("compartment_wall"),
    module_count: z.number().int().min(0).max(100_000).default(0),
    max_modules: z.number().int().min(1).max(100_000).default(35),
    public_symbol_count: z.number().int().min(0).max(100_000).default(0),
    breaker_count: z.number().int().min(0).max(100_000).default(0),
    topological_rank: z.number().int().min(0).max(4096).default(0),
    redline_status: RedlineStatusSchema.default("existing"),
  })
  .strict();
export type BlueprintRoom = z.infer<typeof BlueprintRoomSchema>;

export const BlueprintWallSchema = z
  .object({
    wall_id: IdentifierSchema,
    room_id: IdentifierSchema,
    adjacent_room_id: BoundedTextSchema.default(""),
    rating: WallFireRatingSchema,
    thickness_px: z.number().int().min(1).max(16),
  })
  .strict();
export type BlueprintWall = z.infer<typeof BlueprintWallSchema>;

export const PerimeterPortPinSchema = z
  .object({
    pin_id: IdentifierSchema,
    room_id: IdentifierSchema,
    symbol_name: IdentifierSchema,
    direction: PortDirectionSchema.default("inbound_port"),
    has_circuit_breaker: z.boolean().default(true),
    redline_status: RedlineStatusSchema.default("existing"),
  })
  .strict();
export type PerimeterPortPin = z.infer<typeof PerimeterPortPinSchema>;

export const CircuitBreakerNodeSchema = z
  .object({
    breaker_id: IdentifierSchema,
    room_id: IdentifierSchema,
    symbol_name: IdentifierSchema,
    guard_kind: IdentifierSchema,
  })
  .strict();
export type CircuitBreakerNode = z.infer<typeof CircuitBreakerNodeSchema>;

export const BlueprintBusSchema = z
  .object({
    bus_id: IdentifierSchema,
    source_room: IdentifierSchema,
    target_room: IdentifierSchema,
    port_symbols: z.array(IdentifierSchema).max(256).default([]),
    call_count: z.number().int().min(0).max(1_000_000).default(0),
    has_breaker_guard: z.boolean().default(true),
    redline_status: RedlineStatusSchema.default("existing"),
  })
  .strict();
export type BlueprintBus = z.infer<typeof BlueprintBusSchema>;

export const ExternalRiserSchema = z
  .object({
    riser_id: IdentifierSchema,
    room_id: IdentifierSchema,
    kind: RiserKindSchema,
    target_name: IdentifierSchema,
    redline_status: RedlineStatusSchema.default("existing"),
  })
  .strict();
export type ExternalRiser = z.infer<typeof ExternalRiserSchema>;

export const InvariantPinSchema = z
  .object({
    invariant_id: InvariantIdSchema,
    room_id: IdentifierSchema,
    verification_symbol: IdentifierSchema,
    redline_status: RedlineStatusSchema.default("existing"),
  })
  .strict();
export type InvariantPin = z.infer<typeof InvariantPinSchema>;

export const RedlineDeltaSchema = z
  .object({
    op: RedlineOpSchema,
    room_id: IdentifierSchema,
    target_room: BoundedTextSchema.default(""),
    symbol_name: BoundedTextSchema.default(""),
    member_id: IdentifierSchema.default("."),
    language: SupportedLanguageSchema.default("python"),
    wall_rating: WallFireRatingSchema.default("compartment_wall"),
    max_modules: z.number().int().min(1).max(100_000).default(35),
    has_circuit_breaker: z.boolean().default(true),
    riser_kind: RiserKindSchema.default("external_package"),
    invariant_id: BoundedTextSchema.default(""),
  })
  .strict();
export type RedlineDelta = z.infer<typeof RedlineDeltaSchema>;

export const BlueprintGraphSchema = z
  .object({
    schema_version: IdentifierSchema.default("1.3"),
    title: ShortTextSchema.default("Workspace Blueprint"),
    profile: PermitProfileSchema.default("standard"),
    rooms: z.array(BlueprintRoomSchema).max(4096).default([]),
    walls: z.array(BlueprintWallSchema).max(16_384).default([]),
    port_pins: z.array(PerimeterPortPinSchema).max(32_768).default([]),
    circuit_breakers: z.array(CircuitBreakerNodeSchema).max(32_768).default([]),
    buses: z.array(BlueprintBusSchema).max(16_384).default([]),
    external_risers: z.array(ExternalRiserSchema).max(16_384).default([]),
    invariant_pins: z.array(InvariantPinSchema).max(4096).default([]),
    redlines: z.array(RedlineDeltaSchema).max(2048).default([]),
    netlist_digest: SealDigestSchema,
  })
  .strict();
export type BlueprintGraph = z.infer<typeof BlueprintGraphSchema>;

export const CadSheetIdSchema = z.enum(["A-1", "E-1"]);
export type CadSheetId = z.infer<typeof CadSheetIdSchema>;

export const CadLayerKeySchema = z.enum([
  "structure",
  "breakers",
  "risers",
  "redline_diff",
]);
export type CadLayerKey = z.infer<typeof CadLayerKeySchema>;

export const CadLayerStateSchema = z
  .object({
    structure: z.boolean().default(true),
    breakers: z.boolean().default(true),
    risers: z.boolean().default(true),
    redline_diff: z.boolean().default(true),
  })
  .strict();
export type CadLayerState = z.infer<typeof CadLayerStateSchema>;

export const CadViewerStateSchema = z
  .object({
    activeSheet: CadSheetIdSchema.default("A-1"),
    selectedRoomId: z.string().min(1).max(256).nullable().default(null),
    selectedBusId: z.string().min(1).max(256).nullable().default(null),
    zoomScale: z.number().finite().min(0.25).max(4.0).default(1.0),
    panX: z.number().finite().min(-10_000).max(10_000).default(0),
    panY: z.number().finite().min(-10_000).max(10_000).default(0),
    layers: CadLayerStateSchema,
  })
  .strict();
export type CadViewerState = z.infer<typeof CadViewerStateSchema>;

export const WALL_THICKNESS_BY_RATING: Readonly<Record<WallFireRating, number>> = {
  exterior_envelope: 4,
  workspace_firewall: 3,
  compartment_wall: 2,
  package_lobby: 1,
};

export function parseBlueprintJson(rawJson: string): BlueprintGraph {
  if (typeof rawJson !== "string" || rawJson.length < 2 || rawJson.length > 2_000_000) {
    throw new RangeError(
      "Blueprint JSON payload must be a bounded string between 2 and 2,000,000 characters.",
    );
  }
  return BlueprintGraphSchema.parse(JSON.parse(rawJson));
}
