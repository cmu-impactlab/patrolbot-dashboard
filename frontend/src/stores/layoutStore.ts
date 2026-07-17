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
  minimized: Record<string, number>; // widget id -> original height
  dirty: boolean;

  hydrate: (saved: SavedLayout[], current: LayoutDoc | null) => void;
  applyPreset: (name: string) => void;
  registerCustom: (layout: SavedLayout) => void;
  dropCustom: (name: string) => void;
  setLayouts: (layouts: Layouts, markDirty?: boolean) => void;
  addWidget: (id: string, defaults: { w: number; h: number; minW?: number; minH?: number }) => void;
  removeWidget: (id: string) => void;
  toggleMinimize: (id: string) => void;
  setEditMode: (edit: boolean) => void;
  markSaved: () => void;
  doc: () => LayoutDoc;
}

/** Geometry fingerprint: equal signatures mean no widget moved or resized. */
function signature(layouts: Layouts): string {
  return Object.entries(layouts)
    .map(([breakpoint, items]) => breakpoint + ":" + (items ?? [])
      .map((item) => `${item.i}=${item.x},${item.y},${item.w},${item.h}`)
      .sort()
      .join(";"))
    .sort()
    .join("|");
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

  hydrate: (saved, current) => {
    const presets = saved.filter((item) => item.is_preset);
    // "current" is the autosave slot, not a user-visible dashboard.
    const customLayouts = saved.filter((item) => !item.is_preset && item.name !== "current");
    if (current) {
      set({
        presets,
        customLayouts,
        widgets: current.widgets,
        layouts: current.layouts,
        activePreset: current.preset ?? null,
      });
    } else {
      const operator = presets.find((preset) => preset.name === "Operator") ?? presets[0];
      if (operator) {
        set({
          presets,
          customLayouts,
          widgets: operator.layout.widgets,
          layouts: operator.layout.layouts,
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
      layouts: structuredClone(preset.layout.layouts),
      activePreset: name,
      minimized: {},
      dirty: true,
    });
  },

  registerCustom: (layout) => {
    const rest = get().customLayouts.filter((item) => item.name !== layout.name);
    set({ customLayouts: [...rest, layout], activePreset: layout.name });
  },

  dropCustom: (name) => {
    set({
      customLayouts: get().customLayouts.filter((item) => item.name !== name),
      activePreset: get().activePreset === name ? null : get().activePreset,
    });
  },

  // Breakpoint reflows outside edit mode shouldn't turn the layout "custom",
  // and neither should the grid's echo right after applying a preset — only
  // a change that actually moves/resizes something counts as an edit. The
  // grid invents entries for breakpoints the stored layout doesn't have yet,
  // so only breakpoints both sides know are compared.
  setLayouts: (layouts, markDirty = true) => {
    const known = Object.keys(get().layouts);
    const trimmed: Layouts = Object.fromEntries(
      Object.entries(layouts).filter(([breakpoint]) => known.includes(breakpoint)),
    );
    if (markDirty && signature(trimmed) !== signature(get().layouts)) {
      set({ layouts, dirty: true, activePreset: null });
    } else {
      set({ layouts });
    }
  },

  addWidget: (id, defaults) => {
    const { widgets, layouts } = get();
    if (widgets.includes(id)) return;
    const lg = layouts.lg ?? [];
    const maxY = lg.reduce((max, item) => Math.max(max, item.y + item.h), 0);
    const item: Layout = { i: id, x: 0, y: maxY, ...defaults };
    set({
      widgets: [...widgets, id],
      layouts: { ...layouts, lg: [...lg, item] },
      dirty: true,
    });
  },

  removeWidget: (id) => {
    const { widgets, layouts } = get();
    const next: Layouts = {};
    for (const [breakpoint, items] of Object.entries(layouts)) {
      next[breakpoint] = (items ?? []).filter((item) => item.i !== id);
    }
    set({ widgets: widgets.filter((widget) => widget !== id), layouts: next, dirty: true });
  },

  toggleMinimize: (id) => {
    const { layouts, minimized } = get();
    const lg = layouts.lg ?? [];
    const entry = lg.find((item) => item.i === id);
    if (!entry) return;
    const nextMin = { ...minimized };
    let nextLg: Layout[];
    if (id in minimized) {
      nextLg = lg.map((item) => (item.i === id ? { ...item, h: minimized[id] } : item));
      delete nextMin[id];
    } else {
      nextMin[id] = entry.h;
      nextLg = lg.map((item) => (item.i === id ? { ...item, h: 1, minH: 1 } : item));
    }
    set({ layouts: { ...layouts, lg: nextLg }, minimized: nextMin, dirty: true });
  },

  setEditMode: (edit) => set({ editMode: edit }),
  markSaved: () => set({ dirty: false }),
  doc: () => ({ widgets: get().widgets, layouts: get().layouts, preset: get().activePreset }),
}));
