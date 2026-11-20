/**
 * Compile canonical JSON as fresh TS literals against the real interfaces.
 * This checks structural parity, not Python's runtime bounds/UUID validators.
 */
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";
import { beforeAll, describe, expect, it } from "vitest";
import {
  decodeRle,
  type Envelope,
  type MapData,
  type TeleopVelocityFrame,
} from "./protocol";

const FIXTURES = join(
  dirname(fileURLToPath(import.meta.url)),
  "..", "..", "..", "shared", "schemas", "fixtures",
);

// These frames are outside the browser's inbound AnyFrame union. Reuse their
// exported payloads; hello_ack is server->robot only and has no browser mirror.
const TYPE_INPUT = `
import type * as P from "./protocol";
type FixtureFrame = P.AnyFrame
  | P.Envelope<"robot.hello", P.HelloData>
  | P.Envelope<"telemetry.heartbeat", P.HeartbeatData>
  | P.Envelope<"command.request", P.CommandRequestData>
  | P.TeleopVelocityFrame
  | P.Envelope<"server.hello_ack", { want_map: boolean }>;
`;

function fixture(type: string) {
  // Parsing is only for generating compiler input, never evidence of validity.
  return JSON.parse(readFileSync(join(FIXTURES, `${type}.json`), "utf-8"));
}

interface CompileCase {
  name: string;
  type: string;
  value: unknown;
  valid: boolean;
}

function payloadCase(name: string, type: string, data: unknown, valid: boolean): CompileCase {
  return { name, type, value: { ...fixture(type), data }, valid };
}

// One program checks every case, including negatives. Vitest's transpiler alone
// would erase `satisfies`, and `JSON.parse(...) satisfies T` would check `any`.
function compileCases(cases: CompileCase[]) {
  const inputs = new Map(cases.map((test, index) => [
    join(dirname(fileURLToPath(import.meta.url)), `__protocol_fixture_${index}.ts`),
    `${TYPE_INPUT}\n(${JSON.stringify(test.value)}) satisfies Extract<FixtureFrame, { type: ${JSON.stringify(test.type)} }>;`,
  ]));
  const options: ts.CompilerOptions = {
    strict: true, noEmit: true, skipLibCheck: true, types: [],
    target: ts.ScriptTarget.ES2022,
    module: ts.ModuleKind.ESNext,
    moduleResolution: ts.ModuleResolutionKind.Bundler,
  };
  const host = ts.createCompilerHost(options);
  const getSourceFile = host.getSourceFile.bind(host);
  host.getSourceFile = (path, languageVersion, onError, shouldCreateNewSourceFile) => {
    const source = inputs.get(path);
    return source === undefined
      ? getSourceFile(path, languageVersion, onError, shouldCreateNewSourceFile)
      : ts.createSourceFile(path, source, languageVersion, true);
  };
  const program = ts.createProgram([...inputs.keys()], options, host);
  const diagnostics = ts.getPreEmitDiagnostics(program);
  const format = (items: readonly ts.Diagnostic[]) => ts.formatDiagnosticsWithColorAndContext(items, {
    getCanonicalFileName: (path) => path,
    getCurrentDirectory: () => host.getCurrentDirectory(),
    getNewLine: () => "\n",
  });
  const infrastructureErrors = diagnostics.filter((item) => !item.file || !inputs.has(item.file.fileName));
  expect(format(infrastructureErrors)).toBe("");
  return [...inputs.keys()].map((path) => {
    const errors = diagnostics.filter((item) => item.file?.fileName === path);
    return { errors, message: format(errors) };
  });
}

describe("protocol fixtures", () => {
  const files = readdirSync(FIXTURES).filter((name) => name.endsWith(".json")).sort();
  const cases: CompileCase[] = files.flatMap((file) => {
    const type = file.replace(/\.json$/, "");
    return [
      { name: `${file} payload matches its TS variant`, type, value: fixture(type), valid: true },
      payloadCase(`${file} rejects missing payload fields`, type, {}, false),
    ];
  });

  const pose = fixture("telemetry.pose").data;
  const snapshot = fixture("server.snapshot").data;
  const request = fixture("command.request").data;
  cases.push(
    payloadCase("snapshot accepts populated nested slices", "server.snapshot", {
      ...snapshot,
      pose, battery: fixture("telemetry.battery").data,
      base_state: fixture("telemetry.base_state").data,
      motion: fixture("telemetry.motion").data,
      diagnostics: fixture("telemetry.diagnostics").data,
      resources: fixture("telemetry.resources").data,
      path: fixture("telemetry.path").data,
      last_known_pose: fixture("telemetry.path").data.goal,
      battery_estimate: { state: "discharging", minutes_remaining: null, confidence: "low" },
      events: [fixture("event.append").data],
      slice_ages_s: { pose: 0.1, motion: null },
    }, true),
    payloadCase("pose accepts nullable covariance entries", "telemetry.pose", {
      ...pose, planar_covariance: [0.01, null, -1e-9],
      covariance_limit: null, localization_age_s: null, localization_max_age_s: null,
    }, true),
    payloadCase("pose accepts unavailable covariance", "telemetry.pose", {
      ...pose, planar_covariance: null, covariance_trace: null,
    }, true),
    payloadCase("legacy pose can omit uncertainty and map context", "telemetry.pose", {
      x: pose.x, y: pose.y, yaw: pose.yaw,
      linear_velocity: pose.linear_velocity, angular_velocity: pose.angular_velocity,
    }, true),
    payloadCase("battery accepts nullable optionals", "telemetry.battery", {
      ...fixture("telemetry.battery").data, current: null, percentage: null, estimate: null,
    }, true),
    payloadCase("command accepts Python's nullable optional targets", "command.request", {
      ...request, seed_profile: null, enabled: null,
    }, true),
    payloadCase("localization command accepts measured seed profile", "command.request", {
      ...request, command: "set_initial_pose", seed_profile: "measured",
    }, true),
    payloadCase("lidar power command accepts explicit boolean target", "command.request", {
      command_id: request.command_id, command: "set_lidar_power", enabled: false,
    }, true),
  );

  const invalidFields: [string, string, unknown][] = [
    ["telemetry.pose", "x", "3.42"],
    ["telemetry.pose", "x", null],
    ["telemetry.pose", "planar_covariance", [0.1, 0.2]],
    ["telemetry.pose", "planar_covariance", [0.1, 0.2, 0.3, 0.4]],
    ["telemetry.lidar", "ranges", [1, "unknown", null]],
    ["telemetry.path", "points", [[1, 2, 3]]],
    ["telemetry.path", "goal", { ...request.goal, yaw: "north" }],
    ["telemetry.map", "origin", { ...fixture("telemetry.map").data.origin, yaw: false }],
    ["telemetry.map", "rle", [[100, "8"]]],
    ["telemetry.base_state", "lidar_state", "BROKEN"],
    ["telemetry.motion", "source", "amcl"],
    ["telemetry.diagnostics", "items", [{ ...fixture("telemetry.diagnostics").data.items[0], level: "fatal" }]],
    ["telemetry.diagnostics", "items", [{ ...fixture("telemetry.diagnostics").data.items[0], values: { count: 1 } }]],
    ["server.snapshot", "pose", { ...pose, x: "wrong" }],
    ["server.snapshot", "motion", { ...fixture("telemetry.motion").data, source: "amcl" }],
    ["server.snapshot", "slice_ages_s", { pose: "fresh" }],
    ["state.system_health", "subsystems", [{ ...snapshot.system_health.subsystems[0], level: "OK" }]],
    ["command.request", "command", "dock"],
    ["command.request", "seed_profile", "estimated"],
    ["command.request", "enabled", "false"],
    ["command.result", "outcome", "success"],
    ["teleop.velocity", "deadman", "true"],
  ];
  for (const [type, field, value] of invalidFields) {
    cases.push(payloadCase(`${type} rejects ${field}=${JSON.stringify(value)}`, type, {
      ...fixture(type).data, [field]: value,
    }, false));
  }
  cases.push({
    name: "rejects a changed discriminant with the original payload",
    type: "telemetry.motion",
    value: { ...fixture("telemetry.pose"), type: "telemetry.motion" },
    valid: false,
  });

  let results: ReturnType<typeof compileCases>;
  beforeAll(() => { results = compileCases(cases); }, 15_000);

  cases.forEach((test, index) => {
    it(test.name, () => {
      const { errors, message } = results[index];
      if (test.valid) expect(message).toBe("");
      else {
        expect(errors.length, "invalid literal unexpectedly type-checked").toBeGreaterThan(0);
        // Resolution/syntax errors must never count as a rejected payload.
        expect(errors.every((error) => [1360, 2322, 2353, 2739, 2740, 2741].includes(error.code)), message).toBe(true);
      }
    });
  });

  for (const file of files) {
    it(`${file} parses with a valid envelope`, () => {
      const frame = JSON.parse(readFileSync(join(FIXTURES, file), "utf-8")) as Envelope;
      expect(frame.version).toBe(1);
      expect(frame.type).toBe(file.replace(".json", ""));
      expect(typeof frame.robot_id).toBe("string");
      expect(typeof frame.sequence).toBe("number");
      expect(typeof frame.timestamp).toBe("string");
      expect(frame.data).toBeTypeOf("object");
    });
  }

  it("map fixture RLE decodes to width*height cells", () => {
    const frame = JSON.parse(
      readFileSync(join(FIXTURES, "telemetry.map.json"), "utf-8"),
    ) as Envelope<"telemetry.map", MapData>;
    const cells = decodeRle(frame.data.rle);
    expect(cells.length).toBe(frame.data.width * frame.data.height);
  });

  it("teleop golden frame has bounded velocity and a monotonic-session sequence", () => {
    const frame = JSON.parse(readFileSync(join(FIXTURES, "teleop.velocity.json"), "utf-8")) as TeleopVelocityFrame;
    expect(frame.data.command_id).toBeTypeOf("string");
    expect(Number.isInteger(frame.data.seq) && frame.data.seq >= 0).toBe(true);
    expect(Math.abs(frame.data.linear)).toBeLessThanOrEqual(.3);
    expect(Math.abs(frame.data.angular)).toBeLessThanOrEqual(.7);
    if (!frame.data.deadman) expect([frame.data.linear, frame.data.angular]).toEqual([0, 0]);
  });
});
