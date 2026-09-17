import { beforeEach, describe, expect, it } from "vitest";
import { breakpointFor, COLUMNS, deriveLayouts, useLayoutStore } from "./layoutStore";

const widgets = ["liveMap", "battery", "navControls"];
const desktop = [
  { i: "liveMap", x: 0, y: 0, w: 6, h: 16, minH: 6 },
  { i: "battery", x: 6, y: 0, w: 3, h: 12, minH: 5 },
  { i: "navControls", x: 9, y: 0, w: 3, h: 9, minH: 5 },
];
const state = useLayoutStore.getState;
beforeEach(() => {
  useLayoutStore.setState({ widgets, layouts: deriveLayouts(widgets, { lg: desktop }),
    minimized: {}, dirty: false, revision: 0, breakpoint: "lg", presets: [], customLayouts: [], activePreset: null });
});
describe("responsive arrangements", () => {
  it.each([[320,"xs"],[479,"xs"],[480,"sm"],[767,"sm"],[768,"md"],[1199,"md"],[1200,"lg"],[1440,"lg"]])("classifies container width %s", (width, expected) => {
    expect(breakpointFor(Number(width))).toBe(expected);
  });
  it("derives two tablet columns and a full-width map without changing desktop", () => {
    const next = deriveLayouts(widgets, { lg: desktop });
    expect(next.lg).toEqual(desktop);
    expect(next.md.map(item => [item.x, item.y, item.w])).toEqual([[0,0,10],[0,16,5],[5,16,5]]);
    expect(next.xs.every(item => item.x === 0 && item.w === 4)).toBe(true);
    expect(deriveLayouts(widgets, next)).toEqual(next);
  });
  it("keeps active edits independent and ignores automatic reflows", () => {
    state().setBreakpoint("xs");
    state().editWidget("battery", "taller");
    expect(state().layouts.lg).toEqual(desktop);
    expect(state().layouts.xs[1].h).toBe(13);
    const revision = state().revision;
    state().setLayouts({ lg: [] }, false);
    expect(state().revision).toBe(revision);
    expect(state().layouts.lg).toEqual(desktop);
  });
  it("adds and removes shared widgets at every breakpoint", () => {
    state().addWidget("alerts", { w: 4, h: 10, minW: 3 });
    for (const key of Object.keys(COLUMNS)) expect(state().layouts[key].at(-1)?.i).toBe("alerts");
    expect(state().layouts.xs.at(-1)?.minW).toBe(4);
    state().removeWidget("alerts");
    for (const items of Object.values(state().layouts)) expect(items.some(item => item.i === "alerts")).toBe(false);
  });
  it("never persists minimized dimensions or corrupts them across breakpoints", () => {
    const original = structuredClone(state().doc());
    state().toggleMinimize("battery");
    state().setBreakpoint("md");
    state().toggleMinimize("battery");
    expect(state().doc()).toEqual(original);
    expect(state().dirty).toBe(false);
  });
  it("restores expanded height when another widget is dragged beside a minimized one", () => {
    state().toggleMinimize("battery");
    state().setLayouts({lg: desktop.map(item => item.i === "battery" ? {...item, h: 2, minH: 2} : {...item, y: item.y + 1})});
    expect(state().layouts.lg.find(item => item.i === "battery")?.h).toBe(12);
    expect(state().layouts.lg.find(item => item.i === "battery")?.minH).toBe(5);
  });
  it("does not mark newer changes saved when an older request succeeds", () => {
    state().editWidget("battery", "taller");
    const saving = state().revision;
    state().editWidget("battery", "taller");
    state().markSaved(saving);
    expect(state().dirty).toBe(true);
    state().hydrate([], { widgets: [], layouts: {} });
    expect(state().widgets).toEqual(widgets);
    state().markSaved(state().revision);
    expect(state().dirty).toBe(false);
  });
});

it("persists a named dashboard selection without claiming newer edits belong to it", () => {
  const layout = {name: "Phone", is_preset: false, layout: state().doc(), updated_at: ""};
  const saving = state().revision;
  state().editWidget("battery", "taller");
  state().registerCustom(layout, saving);
  expect(state().activePreset).toBeNull();
  expect(state().customLayouts).toEqual([layout]);
  state().markSaved(state().revision);
  state().registerCustom({...layout, layout: state().doc()}, state().revision);
  expect(state().activePreset).toBe("Phone");
  expect(state().dirty).toBe(true);
  state().markSaved(state().revision);
  state().dropCustom("Phone");
  expect(state().activePreset).toBeNull();
  expect(state().dirty).toBe(true);
});
