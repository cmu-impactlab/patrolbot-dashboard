import registrySource from "../../../contracts/robot-tuning.registry.json?raw";

/**
 * Frontend side of the `robot_tuning_v1` capability.
 *
 * This module NEVER invents runtime state: a value is "active" only when the
 * robot's runtime snapshot says `active_known`, and the packaged registry is
 * shown only as source candidates when the server catalog is missing. There is
 * no localStorage: the persisted desired state belongs to the robot's YAML
 * revision store, not to the browser.
 */

export const TUNING_CONTRACT = "patrolbot-robot-tuning";
export const TUNING_VERSION = 1;
export const TUNING_CAPABILITY = "robot_tuning_v1";

export type TuningValue = number | boolean | string;

export interface TuningDependency {
  kind: string;
  target: string;
  note?: string;
  equals?: number;
  tolerance?: number;
  condition?: string;
}

export interface TuningCatalogEntry {
  id: string;
  label: string;
  group: string;
  type: "number" | "integer" | "boolean" | "string";
  value_kind: string;
  unit: string;
  source: { file: string; node: string; key: string; index: number | null };
  default: TuningValue;
  accepts_edits: boolean;
  activatable: boolean;
  activation_block: string | null;
  protected: boolean;
  not_applicable: boolean;
  mutability: string;
  mutability_fallback: string;
  live_verified: boolean;
  restart_scopes: string[];
  bounds: {
    status: string;
    lower: number | null;
    upper: number | null;
    lower_inclusive: boolean;
    upper_inclusive: boolean;
    note: string;
  };
  effective_ceiling_ref: string | null;
  exceeds_effective_ceiling: boolean;
  dependencies: TuningDependency[];
  side_effects: string[];
}

interface RegistryDocument {
  groups: { id: string; label: string }[];
  parameters: TuningCatalogEntry[];
  generated_from: { baseline_commit: string; inspected: string };
  capability: { advertised: boolean; runtime_implemented: boolean };
}

const registry = JSON.parse(registrySource) as RegistryDocument;

/** Bundled reviewed candidates. Only ever shown as source candidates. */
export const BUNDLED_ENTRIES: TuningCatalogEntry[] = registry.parameters;
export const REGISTRY_GROUPS = registry.groups;
export const REGISTRY_PROVENANCE = registry.generated_from;
export const REGISTRY_CAPABILITY = registry.capability;

export function registryEntry(id: string): TuningCatalogEntry | undefined {
  return BUNDLED_ENTRIES.find((entry) => entry.id === id);
}

/** Field badge. `Live` requires verified server mutability evidence, never a default. */
export type FieldBadge = "live" | "redeploy" | "read-only";

export function fieldBadge(entry: TuningCatalogEntry): FieldBadge {
  if (!entry.accepts_edits || entry.protected || entry.not_applicable) return "read-only";
  if (entry.activatable === true && entry.mutability === "live" && entry.live_verified === true) return "live";
  return "redeploy";
}

export const BADGE_LABEL: Record<FieldBadge, string> = {
  live: "Live",
  redeploy: "Redeploy required",
  "read-only": "Read-only",
};

/* ------------------------------------------------------------------ */
/* Runtime snapshot                                                     */
/* ------------------------------------------------------------------ */

export interface SafeApply {
  allowed: boolean;
  reason: string;
}

export interface TuningJob {
  job_id?: string;
  request_id?: string;
  state?: string;
  detail?: string;
  revision?: number | null;
  affected_services?: string[];
  [key: string]: unknown;
}

/** Coordination-contract shape, with every field optional so a partial or
 *  older response is handled honestly instead of defaulting to "active". */
export interface RawTuningSnapshot {
  readback?: unknown;
  write_disabled_reason?: unknown;
  session_id?: unknown;
  desired_revision?: unknown;
  active_revision?: unknown;
  last_known_good_revision?: unknown;
  active_known?: unknown;
  desired?: unknown;
  active?: unknown;
  field_active_known?: unknown;
  effective_limits?: unknown;
  pending_keys?: unknown;
  catalog?: unknown;
  writable?: unknown;
  activation_supported?: unknown;
  safe_apply?: unknown;
  latest_job?: unknown;
  digest?: unknown;
  desired_digest?: unknown;
  compatibility?: unknown;
  /** Slice-1 contract fallback. */
  kind?: unknown;
  runtime?: unknown;
  revisions?: unknown;
  fields?: unknown;
  advertised?: unknown;
}

export interface NormalizedSnapshot {
  readback?: ParameterReadback | null;
  writeDisabledReason?: string | null;
  available: boolean;
  reason: string | null;
  compatibility: string;
  sessionId: string | null;
  desiredRevision: number | null;
  activeRevision: number | null;
  lastKnownGoodRevision: number | null;
  writable: boolean;
  activationSupported: boolean;
  activeKnown: boolean;
  safeApply: SafeApply;
  effectiveLimits: { max_linear_m_s: number; max_angular_rad_s: number } | null;
  latestJob: TuningJob | null;
  digest: string | null;
  desiredDigest: string | null;
  desired: Record<string, TuningValue>;
  active: Record<string, TuningValue | null>;
  fieldActiveKnown: Record<string, boolean>;
  pendingKeys: string[];
  entries: TuningCatalogEntry[];
  catalogFromServer: boolean;
}

export interface ParameterReadback {
  ageS: number;
  generation: number;
  values: Record<string, TuningValue>;
  limits: { max_linear_m_s: number; max_angular_rad_s: number } | null;
}

function normalizeReadback(raw: unknown): ParameterReadback | null {
  if (!isPlainObject(raw) || raw.source !== "ros_parameters") return null;
  const age = finite(raw.age_s);
  const generation = safeRevision(raw.hardware_session_generation);
  if (age === null || age < 0 || age > 3 || generation === null || !isPlainObject(raw.values)) return null;
  const values = valueMap(raw.values);
  const limits = isPlainObject(raw.limits) ? raw.limits : null;
  const linear = finite(limits?.max_linear_m_s);
  const angular = finite(limits?.max_angular_rad_s);
  return { ageS: age, generation, values,
    limits: linear !== null && angular !== null && linear >= 0 && linear <= .30 && angular >= 0 && angular <= .70
      && values.max_linear_m_s === linear && values.max_angular_rad_s === angular
      ? { max_linear_m_s: linear, max_angular_rad_s: angular } : null };
}

export function freshReadback(snapshot: NormalizedSnapshot | null, receivedAt: number | null,
  now: number, generation: number | null): ParameterReadback | null {
  const readback = snapshot?.readback;
  if (!readback || receivedAt === null || generation !== readback.generation) return null;
  const localAge = (now - receivedAt) / 1000;
  return Number.isFinite(localAge) && localAge >= 0 && localAge + readback.ageS <= 3 ? readback : null;
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function finite(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function safeRevision(value: unknown): number | null {
  const n = finite(value);
  return n !== null && Number.isInteger(n) && n >= 0 && n <= Number.MAX_SAFE_INTEGER ? n : null;
}

function scalar(value: unknown): TuningValue | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "boolean") return value;
  if (typeof value === "string" && value.length <= 256) return value;
  return null;
}

function valueMap(raw: unknown): Record<string, TuningValue> {
  const out: Record<string, TuningValue> = {};
  if (!isPlainObject(raw)) return out;
  for (const [id, value] of Object.entries(raw)) {
    const parsed = scalar(value);
    if (parsed !== null) out[id] = parsed;
  }
  return out;
}

function validEntries(raw: unknown): TuningCatalogEntry[] | null {
  if (!Array.isArray(raw) || raw.length === 0) return null;
  const bundled = new Map(BUNDLED_ENTRIES.map((entry) => [entry.id, entry]));
  const entries: TuningCatalogEntry[] = [];
  for (const value of raw) {
    if (!isPlainObject(value) || typeof value.id !== "string" || typeof value.group !== "string") continue;
    // The robot's canonical entry is authoritative; any display field it omits
    // is filled from the packaged registry with the same id.
    const fallback = bundled.get(value.id);
    const merged = { ...(fallback ?? {}), ...value } as TuningCatalogEntry;
    if (!merged.label) merged.label = value.id;
    if (!merged.unit) merged.unit = "dimensionless";
    if (!merged.bounds || typeof merged.bounds !== "object") {
      merged.bounds = { status: "unknown", lower: null, upper: null, lower_inclusive: false, upper_inclusive: false, note: "" };
    }
    if (!Array.isArray(merged.dependencies)) merged.dependencies = [];
    if (!Array.isArray(merged.restart_scopes)) merged.restart_scopes = [];
    merged.accepts_edits = merged.accepts_edits === true && merged.protected !== true;
    entries.push(merged);
  }
  return entries.length > 0 ? entries : null;
}

/**
 * Convert any server response into a snapshot without inferring activity.
 * A missing/malformed response is `available: false`, and the bundled registry
 * is then shown strictly as source candidates.
 */
export function normalizeSnapshot(raw: unknown): NormalizedSnapshot {
  const base: NormalizedSnapshot = {
    available: false,
    reason: "The robot tuning service did not return a runtime snapshot.",
    compatibility: "unknown",
    sessionId: null,
    desiredRevision: null,
    activeRevision: null,
    lastKnownGoodRevision: null,
    writable: false,
    activationSupported: false,
    activeKnown: false,
    safeApply: { allowed: false, reason: "No runtime snapshot — the robot's tuning state is unknown." },
    effectiveLimits: null,
    latestJob: null,
    digest: null,
    desiredDigest: null,
    desired: {},
    active: {},
    fieldActiveKnown: {},
    pendingKeys: [],
    entries: BUNDLED_ENTRIES,
    catalogFromServer: false,
  };
  if (!isPlainObject(raw)) return base;

  const slice1Revisions = isPlainObject(raw.revisions) ? raw.revisions : {};
  const slice1Fields = Array.isArray(raw.fields) ? raw.fields.filter(isPlainObject) : [];
  const slice1Desired: Record<string, TuningValue> = {};
  const slice1Active: Record<string, TuningValue | null> = {};
  const slice1Known: Record<string, boolean> = {};
  const slice1Pending: string[] = [];
  for (const field of slice1Fields) {
    const id = typeof field.id === "string" ? field.id : null;
    if (!id) continue;
    const desired = scalar(field.desired);
    const active = scalar(field.active);
    if (desired !== null) slice1Desired[id] = desired;
    slice1Active[id] = active;
    slice1Known[id] = field.active_known === true;
    if (field.pending === true) slice1Pending.push(id);
  }

  const catalog = validEntries(raw.catalog);
  const desiredRaw = valueMap(raw.desired);
  const activeRaw = valueMap(raw.active);
  const knownRaw = isPlainObject(raw.field_active_known) ? raw.field_active_known : {};
  const pendingRaw = Array.isArray(raw.pending_keys)
    ? raw.pending_keys.filter((id): id is string => typeof id === "string") : [];

  const desired = Object.keys(desiredRaw).length ? desiredRaw : slice1Desired;
  const active = Object.keys(activeRaw).length ? activeRaw : slice1Active;
  const globalActiveKnown = raw.active_known === true || (raw.kind === "tuning_state_snapshot" && raw.runtime === "known");
  const fieldActiveKnown: Record<string, boolean> = {};
  const entryIds = new Set((catalog ?? BUNDLED_ENTRIES).map((entry) => entry.id));
  for (const id of entryIds) {
    fieldActiveKnown[id] = knownRaw[id] === true || slice1Known[id] === true
      || (globalActiveKnown && activeRaw[id] !== undefined && activeRaw[id] !== null);
  }

  const limitsRaw = isPlainObject(raw.effective_limits) ? raw.effective_limits : null;
  const linear = limitsRaw ? finite(limitsRaw.max_linear_m_s) : null;
  const angular = limitsRaw ? finite(limitsRaw.max_angular_rad_s) : null;
  const effectiveLimits = globalActiveKnown && linear !== null && angular !== null && linear >= 0 && angular >= 0 && linear <= .30 && angular <= .70
    ? { max_linear_m_s: linear, max_angular_rad_s: angular } : null;

  const safeApplyRaw = isPlainObject(raw.safe_apply) ? raw.safe_apply : null;
  const safeApply: SafeApply = safeApplyRaw
    ? { allowed: safeApplyRaw.allowed === true, reason: typeof safeApplyRaw.reason === "string" && safeApplyRaw.reason.length > 0
      ? safeApplyRaw.reason : "No reason provided by the robot." }
    : { allowed: false, reason: "The robot did not report whether a safe apply is allowed." };

  const isSlice1 = raw.kind === "tuning_state_snapshot";
  const sessionId = typeof raw.session_id === "string" && raw.session_id.length > 0 ? raw.session_id : null;
  const uninitialized = raw.session_id === null && raw.desired_revision === null
    && raw.active_known === false && raw.writable === false && raw.activation_supported === false
    && catalog !== null && safeApply.allowed === false;
  if (!sessionId && !isSlice1 && !uninitialized) return base;

  return {
    available: true,
    readback: normalizeReadback(raw.readback),
    writeDisabledReason: typeof raw.write_disabled_reason === "string" ? raw.write_disabled_reason : null,
    reason: null,
    compatibility: typeof raw.compatibility === "string" ? raw.compatibility : isSlice1 ? "contract-only" : "unknown",
    sessionId,
    desiredRevision: safeRevision(raw.desired_revision) ?? safeRevision(slice1Revisions.desired),
    activeRevision: safeRevision(raw.active_revision) ?? safeRevision(slice1Revisions.active),
    lastKnownGoodRevision: safeRevision(raw.last_known_good_revision) ?? safeRevision(slice1Revisions.last_known_good),
    writable: raw.writable === true,
    activationSupported: raw.activation_supported === true,
    activeKnown: raw.active_known === true || (isSlice1 && raw.runtime === "known"),
    safeApply,
    effectiveLimits,
    latestJob: isPlainObject(raw.latest_job) ? (raw.latest_job as TuningJob) : null,
    digest: typeof raw.digest === "string" ? raw.digest : null,
    desiredDigest: typeof raw.desired_digest === "string" ? raw.desired_digest : null,
    desired,
    active,
    fieldActiveKnown,
    pendingKeys: pendingRaw.length ? pendingRaw : slice1Pending,
    entries: catalog ?? BUNDLED_ENTRIES,
    catalogFromServer: catalog !== null,
  };
}

/** Saving desired configuration is separate from activating it. */
export function editDisabledReason(snapshot: NormalizedSnapshot | null, isAdmin: boolean): string | null {
  if (!snapshot || !snapshot.available) return "The robot tuning snapshot is unavailable — showing read-only source candidates.";
  if (!isAdmin) return "An administrator account is required to change tuning.";
  if (!snapshot.writable) return snapshot.writeDisabledReason ?? (snapshot.desiredRevision === null
    ? "Robot tuning has not been initialized; editing is disabled. Live readback remains available."
    : "The robot reported its tuning store is not writable.");
  return null;
}

/** A saved restart-required field that the robot has not activated. */
export function unappliedRestartFields(snapshot: NormalizedSnapshot): TuningCatalogEntry[] {
  return snapshot.entries.filter((entry) => {
    if (fieldBadge(entry) !== "redeploy") return false;
    if (snapshot.pendingKeys.includes(entry.id)) return true;
    if (!snapshot.fieldActiveKnown[entry.id]) return false;
    // The runtime verified the generated artifact for this exact revision.
    // A source target may legitimately be clamped by a lower global ceiling.
    if (snapshot.desiredRevision !== null && snapshot.activeRevision === snapshot.desiredRevision) return false;
    return !valuesEqual(snapshot.desired[entry.id], snapshot.active[entry.id]);
  });
}

export function affectedServices(entries: TuningCatalogEntry[]): string[] {
  return [...new Set(entries.flatMap((entry) => entry.restart_scopes))].sort();
}

export function valuesEqual(a: TuningValue | null | undefined, b: TuningValue | null | undefined): boolean {
  return a === b;
}

/* ------------------------------------------------------------------ */
/* Speed bands and gauges                                               */
/* ------------------------------------------------------------------ */

export type SpeedBand = "ok" | "warn" | "danger" | "unknown";

/** magnitude / cap: < 0.5 green, < 0.8 yellow, >= 0.8 red. Never NaN. */
export function speedBand(ratio: number | null): SpeedBand {
  if (ratio === null || !Number.isFinite(ratio)) return "unknown";
  if (ratio < 0.5) return "ok";
  if (ratio < 0.8) return "warn";
  return "danger";
}

export interface GaugeReading {
  band: SpeedBand;
  ratio: number | null;
  percent: number | null;
  overLimit: boolean;
  direction: string;
  cap: number | null;
  value: number | null;
}

/** Derived gauge reading from a measured value and the ACTIVE effective cap. */
export function gaugeReading(value: number | null, cap: number | null, kind: "linear" | "angular"): GaugeReading {
  if (value === null) {
    return { band: "unknown", ratio: null, percent: null, overLimit: false, direction: "no data", cap, value: null };
  }
  const magnitude = Math.abs(value);
  const stopped = magnitude < 1e-9;
  const direction = kind === "linear"
    ? stopped ? "stopped" : value > 0 ? "forward" : "reverse"
    : stopped ? "stopped" : value > 0 ? "left (CCW)" : "right (CW)";
  if (cap === null) {
    return { band: "unknown", ratio: null, percent: null, overLimit: false, direction, cap: null, value };
  }
  // A zero cap means motion is not permitted: any measured speed is over-limit,
  // but the ratio must not become NaN.
  const ratio = cap > 0 ? magnitude / cap : magnitude > 1e-9 ? Infinity : 0;
  const overLimit = ratio > 1 + 1e-6;
  return { band: overLimit ? "danger" : speedBand(Number.isFinite(ratio) ? ratio : null), ratio, percent: Number.isFinite(ratio) ? ratio * 100 : null, overLimit, direction, cap, value };
}

/** Group used to collect dependent drafts into a single validated batch. */
export function entriesForGroup(entries: TuningCatalogEntry[], group: string): TuningCatalogEntry[] {
  return entries.filter((entry) => entry.group === group);
}

/* ------------------------------------------------------------------ */
/* Validation                                                           */
/* ------------------------------------------------------------------ */

export interface ParsedNumber {
  ok: boolean;
  value?: number;
  reason?: string;
}

const COMPLETE_NUMBER = /^[+-]?(\d+(\.\d+)?|\.\d+)([eE][+-]?\d+)?$/;

/**
 * A complete valid numeric input only. Incomplete text such as "", "-", "0.",
 * ".", "1e" or "NaN"/"Infinity"/booleans is rejected and never sent.
 */
export function parseNumericInput(text: string, entry: TuningCatalogEntry): ParsedNumber {
  const trimmed = text.trim();
  if (trimmed === "") return { ok: false, reason: "Enter a value." };
  if (!COMPLETE_NUMBER.test(trimmed)) return { ok: false, reason: "Enter a complete number." };
  const value = Number(trimmed);
  if (!Number.isFinite(value)) return { ok: false, reason: "Enter a finite number." };
  if (entry.type === "integer" && !Number.isInteger(value)) return { ok: false, reason: "Enter a whole number." };
  const { lower, upper, lower_inclusive: lowInc, upper_inclusive: upInc } = entry.bounds;
  if (lower !== null && (lowInc ? value < lower : value <= lower)) {
    return { ok: false, reason: `Must be ${lowInc ? "at least" : "above"} ${lower} ${unitSuffix(entry)}`.trim() };
  }
  if (upper !== null && (upInc ? value > upper : value >= upper)) {
    return { ok: false, reason: `Must be ${upInc ? "at most" : "below"} ${upper} ${unitSuffix(entry)}`.trim() };
  }
  return { ok: true, value };
}

function unitSuffix(entry: TuningCatalogEntry): string {
  return entry.unit && entry.unit !== "boolean" && entry.unit !== "string" ? entry.unit : "";
}

/* ------------------------------------------------------------------ */
/* Declared parameter bounds                                            */
/* ------------------------------------------------------------------ */

function finiteBound(value: number | null | undefined): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

/** Compact decimal text: at most six places, and no trailing zeros. */
function boundText(value: number): string {
  return Number(value.toFixed(6)).toString();
}

/**
 * The declared allowed range for a parameter, in the operator's terms.
 *
 * Only the catalog's own bounds are used. An unset side is reported as
 * unbounded rather than invented from the current value, and inclusive/
 * exclusive endpoints are spelled with ≥/≤ and >/< so the exact boundary the
 * robot enforces is visible instead of a rounded "min–max" interval.
 */
export function formatBounds(entry: TuningCatalogEntry): string {
  const { lower_inclusive: lowInc, upper_inclusive: upInc } = entry.bounds;
  const low = entry.bounds.lower;
  const up = entry.bounds.upper;
  const suffix = unitSuffix(entry) ? ` ${unitSuffix(entry)}` : "";
  if (finiteBound(low) && finiteBound(up)) {
    return `Allowed range: ${boundText(low)} ${lowInc ? "≤" : "<"} value ${upInc ? "≤" : "<"} ${boundText(up)}${suffix}.`;
  }
  if (finiteBound(low)) return `Allowed range: value ${lowInc ? "≥" : ">"} ${boundText(low)}${suffix}.`;
  if (finiteBound(up)) return `Allowed range: value ${upInc ? "≤" : "<"} ${boundText(up)}${suffix}.`;
  return "No declared parameter bounds.";
}

/**
 * The robot's edit policy for a parameter, derived only from catalog flags.
 * Purely descriptive — it never enables a control the robot has not approved.
 */
export function editPolicyText(entry: TuningCatalogEntry): string {
  if (!entry.accepts_edits) return "Read-only: the robot does not accept edits for this parameter.";
  if (entry.protected) return "Protected: this safety-relevant parameter cannot be changed from the dashboard.";
  if (entry.not_applicable) return "Not applicable to this robot's configuration.";
  if (!entry.activatable) return "Editable in the source, but the robot has not enabled activation for this parameter.";
  return "The robot validates every change against these bounds before saving.";
}

export interface NumericSliderRange {
  min: number;
  max: number;
  step: number;
}

/** The optional slider's step: whole numbers for integer fields, else 0.01. */
export function numericStep(entry: TuningCatalogEntry): number {
  return entry.type === "integer" ? 1 : 0.01;
}

function gridDecimals(step: number): number {
  const dot = String(step).indexOf(".");
  return dot === -1 ? 0 : String(step).length - dot - 1;
}

function snapToGrid(value: number, step: number): number {
  const factor = 10 ** gridDecimals(step);
  return Math.round(value * factor) / factor;
}

/** Smallest step-grid value strictly above `value`. */
function gridAbove(value: number, step: number): number {
  return snapToGrid((Math.floor(value / step + 1e-9) + 1) * step, step);
}

/** Largest step-grid value strictly below `value`. */
function gridBelow(value: number, step: number): number {
  return snapToGrid((Math.ceil(value / step - 1e-9) - 1) * step, step);
}

/**
 * The honest range for the optional slider, or null when one must not be shown.
 *
 * Both endpoints must be declared finite, and an exclusive endpoint is pulled
 * onto the step grid so the slider can never emit the excluded boundary. An
 * unbounded side, or an interval too narrow to hold a representable interior,
 * yields no slider — the operator types an exact value instead.
 */
export function sliderRange(entry: TuningCatalogEntry): NumericSliderRange | null {
  const { lower_inclusive: lowInc, upper_inclusive: upInc } = entry.bounds;
  const low = entry.bounds.lower;
  const up = entry.bounds.upper;
  if (!finiteBound(low) || !finiteBound(up)) return null;
  const step = numericStep(entry);
  const min = lowInc ? low : gridAbove(low, step);
  const max = upInc ? up : gridBelow(up, step);
  if (!(min < max)) return null;
  return { min, max, step };
}

export interface BatchChange {
  id: string;
  value: TuningValue;
}

export interface BatchValidation {
  ok: boolean;
  reason: string | null;
  ids: string[];
}

/**
 * Dependency validation for the edits the operator is composing. Only
 * dependencies whose two endpoints are BOTH in this batch are enforced here;
 * a change to a single logical ceiling is expanded by the robot store itself,
 * so the server remains the authority for cross-group generation.
 */
export function validateBatch(
  changes: BatchChange[],
  baseValues: Record<string, TuningValue>,
  entries: TuningCatalogEntry[],
): BatchValidation {
  const ids = changes.map((change) => change.id);
  const dupes = ids.filter((id, index) => ids.indexOf(id) !== index);
  if (dupes.length) return { ok: false, reason: `Duplicate field in one save: ${dupes[0]}.`, ids: dupes };
  const touched = new Set(ids);
  const effective: Record<string, TuningValue> = { ...baseValues };
  for (const change of changes) effective[change.id] = change.value;

  const byId = new Map(entries.map((entry) => [entry.id, entry]));
  for (const entry of entries) {
    for (const dependency of entry.dependencies ?? []) {
      if (!touched.has(entry.id) && !touched.has(dependency.target)) continue;
      const a = effective[entry.id];
      const b = effective[dependency.target];
      if (typeof a !== "number" || typeof b !== "number" || !Number.isFinite(a) || !Number.isFinite(b)) continue;
      if (dependency.kind === "less_than_or_equal" && a > b + 1e-9) {
        if (!touched.has(entry.id) && (entry.value_kind === "speed" || entry.value_kind === "angular_speed")) continue;
        return { ok: false, ids: [entry.id, dependency.target],
          reason: `${entry.label} must be no more than ${byId.get(dependency.target)?.label ?? dependency.target}.` };
      }
      if (dependency.kind === "sum_equals") {
        const target = dependency.equals ?? 1;
        const tolerance = dependency.tolerance ?? 1e-6;
        if (Math.abs(a + b - target) > tolerance) {
          return { ok: false, ids: [entry.id, dependency.target],
            reason: `${entry.label} and ${byId.get(dependency.target)?.label ?? dependency.target} must add up to ${target}.` };
        }
      }
    }
  }
  return { ok: true, reason: null, ids };
}

/** Base values the batch is compared against: persisted desired, then reviewed default. */
export function effectiveValues(snapshot: NormalizedSnapshot): Record<string, TuningValue> {
  const base: Record<string, TuningValue> = {};
  for (const entry of snapshot.entries) {
    const desired = snapshot.desired[entry.id];
    base[entry.id] = desired !== undefined ? desired : entry.default;
  }
  return base;
}

/* ------------------------------------------------------------------ */
/* Canonical requests                                                   */
/* ------------------------------------------------------------------ */

export interface CanonicalRequest {
  contract: typeof TUNING_CONTRACT;
  version: typeof TUNING_VERSION;
  operation: "read" | "save" | "apply" | "redeploy" | "status" | "restore";
  request_id: string;
  generation: { capability: string; session_id: string };
  base_revision?: number;
  expected_revision?: number;
  restore_revision?: number;
  target?: "last_known_good";
  job_id?: string;
  changes?: BatchChange[];
}

export function newRequestId(): string {
  return crypto.randomUUID();
}

export function generationFor(sessionId: string) {
  return { capability: TUNING_CAPABILITY, session_id: sessionId };
}

export function readRequest(sessionId: string, requestId = newRequestId()): CanonicalRequest {
  return { contract: TUNING_CONTRACT, version: TUNING_VERSION, operation: "read",
    request_id: requestId, generation: generationFor(sessionId) };
}

export function saveRequest(sessionId: string, baseRevision: number, changes: BatchChange[], requestId = newRequestId()): CanonicalRequest {
  return { contract: TUNING_CONTRACT, version: TUNING_VERSION, operation: "save",
    request_id: requestId, generation: generationFor(sessionId), base_revision: baseRevision, changes };
}

export function applyRequest(sessionId: string, expectedRevision: number, requestId = newRequestId()): CanonicalRequest {
  return { contract: TUNING_CONTRACT, version: TUNING_VERSION, operation: "apply",
    request_id: requestId, generation: generationFor(sessionId), expected_revision: expectedRevision };
}

export function redeployRequest(sessionId: string, expectedRevision: number, requestId = newRequestId()): CanonicalRequest {
  return { contract: TUNING_CONTRACT, version: TUNING_VERSION, operation: "redeploy",
    request_id: requestId, generation: generationFor(sessionId), expected_revision: expectedRevision };
}

export function statusRequest(sessionId: string, jobId?: string, requestId = newRequestId()): CanonicalRequest {
  return { contract: TUNING_CONTRACT, version: TUNING_VERSION, operation: "status",
    request_id: requestId, generation: generationFor(sessionId), ...(jobId ? { job_id: jobId } : {}) };
}

export function restoreRequest(sessionId: string, expectedRevision: number, restoreRevision: number, requestId = newRequestId()): CanonicalRequest {
  return { contract: TUNING_CONTRACT, version: TUNING_VERSION, operation: "restore",
    request_id: requestId, generation: generationFor(sessionId), expected_revision: expectedRevision,
    restore_revision: restoreRevision, target: "last_known_good" };
}

/* ------------------------------------------------------------------ */
/* Response handling                                                    */
/* ------------------------------------------------------------------ */

export type TuningOutcome = "ok" | "conflict" | "refused" | "failed" | "unknown";

export interface TuningResponse {
  request_id: string;
  outcome: TuningOutcome;
  detail: string;
  snapshot: NormalizedSnapshot | null;
  job: TuningJob | null;
}

function outcome(value: unknown): TuningOutcome {
  return value === "ok" || value === "conflict" || value === "refused" || value === "failed" || value === "unknown"
    ? value : "unknown";
}

export function parseTuningResponse(raw: unknown): TuningResponse {
  if (!isPlainObject(raw)) {
    return { request_id: "", outcome: "unknown", detail: "The tuning service returned an unreadable response.", snapshot: null, job: null };
  }
  return {
    request_id: typeof raw.request_id === "string" ? raw.request_id : "",
    outcome: outcome(raw.outcome),
    detail: typeof raw.detail === "string" ? raw.detail : "",
    snapshot: raw.snapshot === null || raw.snapshot === undefined ? null : normalizeSnapshot(raw.snapshot),
    job: isPlainObject(raw.job) ? (raw.job as TuningJob) : null,
  };
}

/** Map an operation outcome to the status line shown beside a field. */
export function outcomeLabel(outcome: TuningOutcome): "Saved and active" | "Saved — redeploy required" | "Saved — waiting for safe apply" | "Conflict" | "Failed" | "Outcome unknown" {
  switch (outcome) {
    case "ok": return "Saved and active";
    case "conflict": return "Conflict";
    case "refused": return "Failed";
    case "failed": return "Failed";
    default: return "Outcome unknown";
  }
}
