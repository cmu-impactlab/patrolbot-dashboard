import { create } from "zustand";

export type Theme = "light" | "dark";

export interface MapLayers {
  lidar: boolean;
  plannedPath: boolean;
  trajectory: boolean;
  goal: boolean;
}

interface UiState {
  theme: Theme;
  fullscreenWidget: string | null;
  tourActive: boolean;
  tourRequest: number;
  followRobot: boolean;
  mapLayers: MapLayers;
  setTheme: (theme: Theme) => void;
  setFullscreen: (id: string | null) => void;
  startTour: () => void;
  finishTour: () => void;
  setFollowRobot: (follow: boolean) => void;
  toggleLayer: (layer: keyof MapLayers) => void;
}

function initialTheme(): Theme {
  try {
    const stored = localStorage.getItem("patrolbot.theme");
    if (stored === "light" || stored === "dark") return stored;
    return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  } catch {
    return "light";
  }
}

export const useUiStore = create<UiState>((set, get) => ({
  theme: initialTheme(),
  fullscreenWidget: null,
  tourActive: false,
  tourRequest: 0,
  followRobot: true,
  mapLayers: { lidar: true, plannedPath: true, trajectory: true, goal: true },

  setTheme: (theme) => {
    try {
      localStorage.setItem("patrolbot.theme", theme);
    } catch {
      // storage unavailable (private mode / tests) — theme is session-only
    }
    document.documentElement.dataset.theme = theme;
    set({ theme });
  },
  setFullscreen: (id) => set({ fullscreenWidget: id }),
  startTour: () => set((state) => ({
    tourActive: true,
    tourRequest: state.tourRequest + 1,
  })),
  finishTour: () => set({ tourActive: false }),
  setFollowRobot: (follow) => set({ followRobot: follow }),
  toggleLayer: (layer) =>
    set({ mapLayers: { ...get().mapLayers, [layer]: !get().mapLayers[layer] } }),
}));
