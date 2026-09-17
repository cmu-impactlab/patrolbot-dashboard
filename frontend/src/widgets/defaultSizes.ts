/** Geometry defaults shared by layout migration and the widget library. */
export const WIDGET_SIZES: Record<string, { w: number; h: number; minW?: number; minH?: number }> = {
  liveMap: { w: 6, h: 16, minW: 3, minH: 6 },
  robotStatus: { w: 3, h: 9, minW: 2, minH: 4 },
  battery: { w: 3, h: 12, minW: 2, minH: 5 },
  systemHealth: { w: 4, h: 13, minW: 3, minH: 5 },
  alerts: { w: 4, h: 10, minW: 3, minH: 4 },
  piStats: { w: 4, h: 10, minW: 3, minH: 4 },
  bumpers: { w: 3, h: 13, minW: 2, minH: 8 },
  recordings: { w: 4, h: 13, minW: 3, minH: 6 },
  navControls: { w: 3, h: 9, minW: 2, minH: 5 },
};
