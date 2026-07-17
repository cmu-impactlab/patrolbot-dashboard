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
  activePreset: string | null;
  editMode: boolean;
  minimized: Record<string, number>; // widget id -> original height
  dirty: boolean;

  hydrate: (saved: SavedLayout[], current: LayoutDoc | null) => void;
  applyPreset: (name: string) => void;
  setLayouts: (layouts: Layouts, markDirty?: boolean) => void;
  addWidget: (id: string, defaults: { w: number; h: number; minW?: number; minH?: number }) => void;
  removeWidget: (id: string) => void;
  toggleMinimize: (id: string) => void;
  setEditMode: (edit: boolean) => void;
  markSaved: () => void;
  doc: () => LayoutDoc;
}

export const useLayoutStore = create<LayoutState>((set, get) => ({
  widgets: [],
  layouts: {},
  presets: [],
  activePreset: null,
  editMode: false,
  minimized: {},
  dirty: false,

  hydrate: (saved, current) => {
    const presets = saved.filter((item) => item.is_preset);
    if (current) {
      set({
        presets,
        widgets: current.widgets,
        layouts: current.layouts,
        activePreset: current.preset ?? null,
      });
    } else {
      const operator = presets.find((preset) => preset.name === "Operator") ?? presets[0];
      if (operator) {
        set({
          presets,
          widgets: operator.layout.widgets,
          layouts: operator.layout.layouts,
          activePreset: operator.name,
        });
      } else {
        set({ presets });
      }
    }
  },

  applyPreset: (name) => {
    const preset = get().presets.find((item) => item.name === name);
    if (!preset) return;
    set({
      widgets: [...preset.layout.widgets],
      layouts: structuredClone(preset.layout.layouts),
      activePreset: name,
      minimized: {},
      dirty: true,
    });
  },

  // Breakpoint reflows outside edit mode shouldn't turn the layout "custom".
  setLayouts: (layouts, markDirty = true) =>
    set(markDirty ? { layouts, dirty: true, activePreset: null } : { layouts }),

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
