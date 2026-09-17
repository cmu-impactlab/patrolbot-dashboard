import { WIDGET_SIZES } from "./defaultSizes";
import { lazy, type ComponentType, type LazyExoticComponent } from "react";
import { AlertsWidget } from "./AlertsWidget";
import { BumpersWidget } from "./BumpersWidget";
import { LiveMapWidget, MapSettings } from "./LiveMapWidget";
import { NavControlsWidget } from "./NavControlsWidget";
import { RecordingsWidget } from "./RecordingsWidget";
import { RobotStatusWidget } from "./RobotStatusWidget";
import { SystemHealthWidget } from "./SystemHealthWidget";

// The two charting widgets are the whole reason the charts chunk exists, and
// it is the largest thing the dashboard downloads. Loading them lazily means
// the map, controls and status widgets render without waiting for it, and a
// layout without either one never fetches it at all. WidgetFrame provides the
// Suspense boundary.
const BatteryWidget = lazy(async () => ({
  default: (await import("./BatteryWidget")).BatteryWidget,
}));
const PiStatsWidget = lazy(async () => ({
  default: (await import("./PiStatsWidget")).PiStatsWidget,
}));

export interface WidgetDefinition {
  id: string;
  title: string;
  description: string;
  component: ComponentType | LazyExoticComponent<ComponentType>;
  /** Optional extra header control (e.g. the map's layer menu). */
  settings?: ComponentType;
  defaultSize: { w: number; h: number; minW?: number; minH?: number };
  /** Grey out with a stale overlay when the robot is offline. */
  needsRobot: boolean;
  noPadding?: boolean;
}

export const WIDGET_REGISTRY: Record<string, WidgetDefinition> = {
  liveMap: {
    id: "liveMap",
    title: "Live Map",
    description: "Occupancy map with the robot, its path and sensors",
    component: LiveMapWidget,
    settings: MapSettings,
    defaultSize: WIDGET_SIZES.liveMap,
    needsRobot: true,
    noPadding: true,
  },
  robotStatus: {
    id: "robotStatus",
    title: "Robot Status",
    description: "Primary state, motors, localization and speed",
    component: RobotStatusWidget,
    defaultSize: WIDGET_SIZES.robotStatus,
    needsRobot: true,
  },
  battery: {
    id: "battery",
    title: "Battery",
    description: "Charge level, voltage, estimated runtime and trend",
    component: BatteryWidget,
    defaultSize: WIDGET_SIZES.battery,
    needsRobot: true,
  },
  systemHealth: {
    id: "systemHealth",
    title: "System Health",
    description: "Per-subsystem health with plain-language explanations",
    component: SystemHealthWidget,
    defaultSize: WIDGET_SIZES.systemHealth,
    needsRobot: true,
  },
  alerts: {
    id: "alerts",
    title: "Alerts",
    description: "Event history with severity and acknowledgment",
    component: AlertsWidget,
    defaultSize: WIDGET_SIZES.alerts,
    needsRobot: false,
  },
  piStats: {
    id: "piStats",
    title: "Robot Computer & Network",
    description: "Raspberry Pi resources, link quality and telemetry rate",
    component: PiStatsWidget,
    defaultSize: WIDGET_SIZES.piStats,
    needsRobot: true,
  },
  bumpers: {
    id: "bumpers",
    title: "Bumpers",
    description: "Top-down view showing which bumper strip was hit",
    component: BumpersWidget,
    defaultSize: WIDGET_SIZES.bumpers,
    needsRobot: true,
  },
  recordings: {
    id: "recordings",
    title: "Recordings",
    description: "Record telemetry sessions and replay them on the map",
    component: RecordingsWidget,
    defaultSize: WIDGET_SIZES.recordings,
    needsRobot: false,
  },
  navControls: {
    id: "navControls",
    title: "Navigation",
    description: "Send the robot to a destination, undock it, fix its location, or stop it",
    component: NavControlsWidget,
    defaultSize: WIDGET_SIZES.navControls,
    needsRobot: true,
  },
};
