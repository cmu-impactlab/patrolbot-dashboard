/**
 * Contract test: every golden fixture in shared/schemas/fixtures must parse
 * into our protocol types. The server validates the same files with pydantic;
 * drift on either side breaks one of the two suites.
 */
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import {
  decodeRle,
  type AnyFrame,
  type Envelope,
  type MapData,
} from "./protocol";

const FIXTURES = join(
  dirname(fileURLToPath(import.meta.url)),
  "..", "..", "..", "shared", "schemas", "fixtures",
);

const KNOWN_TYPES = new Set([
  "robot.hello", "server.hello_ack", "server.snapshot",
  "state.connection", "state.robot_status", "state.system_health", "event.append",
  "telemetry.heartbeat", "telemetry.pose", "telemetry.lidar", "telemetry.path",
  "telemetry.battery", "telemetry.base_state", "telemetry.diagnostics",
  "telemetry.resources", "telemetry.map",
]);

describe("protocol fixtures", () => {
  const files = readdirSync(FIXTURES).filter((name: string) => name.endsWith(".json"));

  it("covers at least the browser-facing types", () => {
    expect(files.length).toBeGreaterThanOrEqual(14);
  });

  for (const file of files) {
    it(`${file} parses with a valid envelope`, () => {
      const frame = JSON.parse(readFileSync(join(FIXTURES, file), "utf-8")) as Envelope;
      expect(frame.version).toBe(1);
      expect(frame.type).toBe(file.replace(".json", ""));
      expect(KNOWN_TYPES.has(frame.type)).toBe(true);
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

  it("pose fixture satisfies the AnyFrame union", () => {
    const frame = JSON.parse(
      readFileSync(join(FIXTURES, "telemetry.pose.json"), "utf-8"),
    ) as AnyFrame;
    if (frame.type !== "telemetry.pose") throw new Error("wrong type");
    expect(frame.data.x).toBeTypeOf("number");
    expect(frame.data.yaw).toBeTypeOf("number");
  });
});
