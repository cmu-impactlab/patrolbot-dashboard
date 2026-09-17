import { WIDGET_SIZES } from "../widgets/defaultSizes";
import { create } from "zustand";
import type { Layout, Layouts } from "react-grid-layout";

export interface LayoutDoc {
  widgets: string[];
  layouts: Layouts;
  /** Preset this layout was derived from, if the user hasn't customized it. */
  preset?: string | null;
}

export interface SavedLayout {
  name: string;
  is_preset: boolean;
  layout: LayoutDoc;
  updated_at: string;
}

interface LayoutState {
  widgets: string[];
  layouts: Layouts;
  presets: SavedLayout[];
  /** User-saved dashboards (persisted server-side; deletable, unlike presets). */
  customLayouts: SavedLayout[];
  activePreset: string | null;
  editMode: boolean;
  minimized: Record<string, number>; // temporary presentation flags
  dirty: boolean;
  revision: number;
  breakpoint: string;
  setBreakpoint: (breakpoint: string) => void;
  editWidget: (id: string, action: "up" | "down" | "taller" | "shorter" | "wider" | "narrower") => void;

  hydrate: (saved: SavedLayout[], current: LayoutDoc | null) => void;
  applyPreset: (name: string) => void;
  registerCustom: (layout: SavedLayout, savedRevision: number) => void;
  dropCustom: (name: string) => void;
  setLayouts: (layouts: Layouts, markDirty?: boolean) => void;
  addWidget: (id: string, defaults: { w: number; h: number; minW?: number; minH?: number }) => void;
  removeWidget: (id: string) => void;
  toggleMinimize: (id: string) => void;
  setEditMode: (edit: boolean) => void;
  markSaved: (revision: number) => void;
  doc: () => LayoutDoc;
}

export const BREAKPOINTS = { lg: 1200, md: 768, sm: 480, xs: 0 };
export const COLUMNS: Record<string, number> = { lg: 12, md: 10, sm: 6, xs: 4 };
export const breakpointFor = (width: number) => width >= 1200 ? "lg" : width >= 768 ? "md" : width >= 480 ? "sm" : "xs";

/** Preserve known coordinates. Derive missing arrangements from desktop reading order. */
export function deriveLayouts(widgets: string[], layouts: Layouts): Layouts {
  const result = structuredClone(layouts);
  const desktop = widgets.map((id) => layouts.lg?.find(item => item.i === id)
    ?? { i: id, x: 0, y: 999, ...(WIDGET_SIZES[id] ?? { w: 4, h: 10 }) });
  desktop.sort((a, b) => a.y - b.y || a.x - b.x || widgets.indexOf(a.i) - widgets.indexOf(b.i));
  for (const [key, cols] of Object.entries(COLUMNS)) {
    let bottoms = [0, 0];
    const existing = result[key] ?? [];
    const items: Layout[] = [];
    for (const source of desktop) {
      const saved = existing.find(item => item.i === source.i);
      if (saved) {
        items.push(saved);
        if (key === "md" && saved.w <= cols / 2) { const column = saved.x < cols / 2 ? 0 : 1; bottoms[column] = Math.max(bottoms[column], saved.y + saved.h); }
        else bottoms = bottoms.map(bottom => Math.max(bottom, saved.y + saved.h));
        continue;
      }
      const full = key !== "md" || source.i === "liveMap";
      const column = bottoms[0] <= bottoms[1] ? 0 : 1;
      const y = full ? Math.max(...bottoms) : bottoms[column];
      const item = key === "lg" ? { ...source } : {
        ...source, minH: source.minH ?? WIDGET_SIZES[source.i]?.minH ?? 3, x: full ? 0 : column * cols / 2, y,
        w: full ? cols : cols / 2, minW: full ? cols : cols / 2, maxW: cols,
      };
      items.push(item);
      if (full) bottoms = [y + item.h, y + item.h];
      else bottoms[column] = y + item.h;
    }
    // Legacy phone entries may have narrow desktop-derived widths.
    result[key] = items.map(item => key === "sm" || key === "xs"
      ? { ...item, x: 0, w: cols, minW: cols, maxW: cols } : item);
  }
  return result;
}

export const useLayoutStore = create<LayoutState>((set, get) => ({
  widgets: [],
  layouts: {},
  presets: [],
  customLayouts: [],
  activePreset: null,
  editMode: false,
  minimized: {},
  dirty: false,
  revision: 0,
  breakpoint: "lg",
  setBreakpoint: (breakpoint) => set({ breakpoint }),

  hydrate: (saved, current) => {
    if (get().dirty) return;
    const presets = saved.filter((item) => item.is_preset);
    // "current" is the autosave slot, not a user-visible dashboard.
    const customLayouts = saved.filter((item) => !item.is_preset && item.name !== "current");
    if (current) {
      set({
        presets,
        customLayouts,
        widgets: current.widgets,
        layouts: deriveLayouts(current.widgets, current.layouts),
        activePreset: current.preset ?? null,
      });
    } else {
      const operator = presets.find((preset) => preset.name === "Operator") ?? presets[0];
      if (operator) {
        set({
          presets,
          customLayouts,
          widgets: operator.layout.widgets,
          layouts: deriveLayouts(operator.layout.widgets, operator.layout.layouts),
          activePreset: operator.name,
        });
      } else {
        set({ presets, customLayouts });
      }
    }
  },

  applyPreset: (name) => {
    const preset = get().presets.find((item) => item.name === name)
      ?? get().customLayouts.find((item) => item.name === name);
    if (!preset) return;
    set({
      widgets: [...preset.layout.widgets],
      layouts: deriveLayouts(preset.layout.widgets, preset.layout.layouts),
      activePreset: name,
      minimized: {},
      dirty: true,
      revision: get().revision + 1,
    });
  },

  registerCustom: (layout, savedRevision) => {
    const rest = get().customLayouts.filter((item) => item.name !== layout.name);
    set({ customLayouts: [...rest, layout], ...(savedRevision === get().revision
      ? { activePreset: layout.name, dirty: true, revision: get().revision + 1 } : {}) });
  },

  dropCustom: (name) => {
    set({
      customLayouts: get().customLayouts.filter((item) => item.name !== name),
      ...(get().activePreset === name
        ? { activePreset: null, dirty: true, revision: get().revision + 1 } : {}),
    });
  },

  // Only explicit drag/resize completions reach this method.
  setLayouts: (layouts, markDirty = true) => {
    if (!markDirty) return;
    const { breakpoint, layouts: previous, minimized } = get();
    const items = layouts[breakpoint];
    if (!items) return;
    const geometry = (entries: Layout[]) => entries.map(item => {
      const expanded = previous[breakpoint]?.find(entry => entry.i === item.i);
      const h = item.i in minimized && expanded ? expanded.h : item.h;
      return `${item.i}:${item.x},${item.y},${item.w},${h}`;
    }).sort().join(";");
    if (geometry(items) === geometry(previous[breakpoint] ?? [])) return;
    set({ layouts: { ...previous, [breakpoint]: items.map(item => {
      const expanded = previous[breakpoint]?.find(entry => entry.i === item.i);
      return item.i in minimized && expanded ? { ...item, h: expanded.h, minH: expanded.minH } : { ...item };
    }) },
      dirty: true, revision: get().revision + 1, activePreset: null });
  },

  addWidget: (id, defaults) => {
    const { widgets, layouts } = get();
    if (widgets.includes(id)) return;
    const next = { ...layouts };
    for (const [key, cols] of Object.entries(COLUMNS)) {
      const items = layouts[key] ?? [];
      const w = key === "lg" ? defaults.w : key === "md" && id !== "liveMap" ? cols / 2 : cols;
      next[key] = [...items, { ...defaults, i: id, x: 0,
        y: items.reduce((max, item) => Math.max(max, item.y + item.h), 0),
        w, minW: key === "sm" || key === "xs" ? cols : Math.min(defaults.minW ?? w, w), maxW: cols }];
    }
    set({ widgets: [...widgets, id], layouts: next, dirty: true,
      revision: get().revision + 1, activePreset: null });
  },

  removeWidget: (id) => {
    const { widgets, layouts } = get();
    const next: Layouts = {};
    for (const [breakpoint, items] of Object.entries(layouts)) {
      next[breakpoint] = (items ?? []).filter((item) => item.i !== id);
    }
    const minimized = { ...get().minimized }; delete minimized[id];
    set({ minimized, widgets: widgets.filter((widget) => widget !== id), layouts: next, dirty: true, revision: get().revision + 1, activePreset: null });
  },

  // Minimize is presentation only; expanded dimensions remain canonical.
  toggleMinimize: (id) => {
    const minimized = { ...get().minimized };
    if (id in minimized) delete minimized[id];
    else minimized[id] = 1;
    set({ minimized });
  },

  editWidget: (id, action) => {
    const { breakpoint, layouts } = get();
    const items = (layouts[breakpoint] ?? []).map(item => ({ ...item }))
      .sort((a, b) => a.y - b.y || a.x - b.x);
    const index = items.findIndex(item => item.i === id);
    if (index < 0) return;
    const item = items[index];
    if (action === "up" || action === "down") {
      const target = index + (action === "up" ? -1 : 1);
      if (!items[target]) return;
      [items[index], items[target]] = [items[target], items[index]];
      if (breakpoint === "xs" || breakpoint === "sm") {
        let y = 0;
        items.forEach(entry => { entry.x = 0; entry.y = y; y += entry.h; });
      } else {
        // Repack in the requested reading order, retaining each widget's size.
        let x = 0, y = 0, rowHeight = 0;
        items.forEach(entry => {
          if (x + entry.w > COLUMNS[breakpoint]) { x = 0; y += rowHeight; rowHeight = 0; }
          entry.x = x; entry.y = y; x += entry.w; rowHeight = Math.max(rowHeight, entry.h);
        });
      }
    } else if (action === "taller" || action === "shorter") {
      item.h = Math.max(item.minH ?? 3, item.h + (action === "taller" ? 1 : -1));
    } else if (breakpoint === "md" || breakpoint === "lg") {
      item.w = Math.max(item.minW ?? 1, Math.min(COLUMNS[breakpoint], item.w + (action === "wider" ? 1 : -1)));
      item.x = Math.min(item.x, COLUMNS[breakpoint] - item.w);
    }
    set({ layouts: { ...layouts, [breakpoint]: items }, dirty: true,
      revision: get().revision + 1, activePreset: null });
  },

  setEditMode: (edit) => set({ editMode: edit }),
  markSaved: (revision) => { if (get().revision === revision) set({ dirty: false }); },
  doc: () => ({ widgets: get().widgets, layouts: get().layouts, preset: get().activePreset }),
}));
