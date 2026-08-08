import * as Dialog from "@radix-ui/react-dialog";
import { ArrowLeft, ArrowRight, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { AuthUser } from "../stores/authStore";
import { useAuthStore } from "../stores/authStore";
import { useLayoutStore } from "../stores/layoutStore";
import { useUiStore } from "../stores/uiStore";

export interface TourStep {
  target: string;
  title: string;
  description: string;
  instruction: string;
}

const CORE_STEPS: TourStep[] = [
  {
    target: '[data-tour="overview"]',
    title: "Welcome to your dashboard",
    description: "This short tour points to the controls you will use most. It does not send any commands or change the robot.",
    instruction: "Select Next to look around.",
  },
  {
    target: '[data-tour="robot-id"]',
    title: "Confirm the robot",
    description: "Check this robot ID before every session so you know which robot you are watching.",
    instruction: "Look here first when more than one robot may be in use.",
  },
  {
    target: '[data-tour="robot-condition"]',
    title: "Read the robot condition",
    description: "This badge summarizes whether the robot is ready, moving, warning, or unavailable. Hover it for the current detail.",
    instruction: "Click the related status widgets below when you need more detail.",
  },
  {
    target: '[data-tour="connection"]',
    title: "Check that information is current",
    description: "Online means current information is arriving. Stale, offline, or reconnecting means you should not trust the screen as a live picture.",
    instruction: "Wait for Online before attempting any robot action.",
  },
  {
    target: '[data-tour="layouts"]',
    title: "Choose a dashboard layout",
    description: "Layouts arrange widgets for different tasks. Your named dashboards also appear in this menu.",
    instruction: "Click this menu whenever you want to switch layouts or save the current arrangement.",
  },
];

interface WidgetTour {
  id: string;
  name: string;
  step: TourStep | ((role: string) => TourStep);
}

const WIDGET_TOURS: WidgetTour[] = [
  {
    id: "liveMap", name: "Live Map",
    step: {
      target: '[data-tour="widget-liveMap"]', title: "Use the Live Map",
      description: "The map shows the robot, its heading, nearby laser points, planned path, traveled path, and selected destination.",
      instruction: "Pan or zoom here, and always confirm the map matches the robot's real floor.",
    },
  },
  {
    id: "robotStatus", name: "Robot Status",
    step: {
      target: '[data-tour="widget-robotStatus"]', title: "Read Robot Status",
      description: "This widget explains whether the robot is moving, whether its drive is available, its speed, and whether its map location is trusted.",
      instruction: "An offline overlay means these are last-known values, not a live reading.",
    },
  },
  {
    id: "battery", name: "Battery",
    step: {
      target: '[data-tour="widget-battery"]', title: "Check the battery",
      description: "Battery shows charge, voltage, charging state, recent trend, and an estimate when enough information is available.",
      instruction: "Check this before a task and before deciding whether the robot should dock.",
    },
  },
  {
    id: "systemHealth", name: "System Health",
    step: {
      target: '[data-tour="widget-systemHealth"]', title: "Review System Health",
      description: "Subsystem checks are grouped here with plain-language explanations for warnings and unavailable systems.",
      instruction: "Expand a row to understand what needs attention.",
    },
  },
  {
    id: "piStats", name: "Robot Computer & Network",
    step: {
      target: '[data-tour="widget-piStats"]', title: "Watch the robot computer and network",
      description: "This widget reports computer load, memory, temperature, storage, network quality, and incoming update rate.",
      instruction: "Use it when the dashboard feels slow or robot information arrives late.",
    },
  },
  {
    id: "bumpers", name: "Bumpers",
    step: {
      target: '[data-tour="widget-bumpers"]', title: "Check bumper contact",
      description: "The top-down robot view highlights the bumper area that reported contact.",
      instruction: "Treat an active bumper as a reason to stop and inspect the robot and path.",
    },
  },
  {
    id: "alerts", name: "Alerts",
    step: {
      target: '[data-tour="widget-alerts"]', title: "Review alerts",
      description: "Filter important events by severity and time. Marking an alert as read organizes your view but does not fix the underlying condition.",
      instruction: "Open related status widgets before deciding what to do next.",
    },
  },
  {
    id: "navControls", name: "Navigation",
    step: (role) => ({
      target: '[data-tour="widget-navControls"]', title: "Move the robot safely",
      description: role === "observer"
        ? "Your Observer account can inspect Navigation, but movement controls remain disabled. Ask an operator or administrator when the robot must move."
        : `Your ${role === "administrator" ? "Administrator" : "Operator"} account can set location, send a destination, stop, or undock when the displayed safety checks pass.`,
      instruction: "Before any movement control is clicked, clear the path, keep a person at the robot, and use the physical emergency stop for emergencies.",
    }),
  },
  {
    id: "recordings", name: "Recordings",
    step: (role) => ({
      target: '[data-tour="widget-recordings"]', title: "Record and replay sessions",
      description: "Choose the information to capture, name the session, and replay or download it later. A recording is shared across everyone watching.",
      instruction: role === "observer"
        ? "Your Observer account can replay and download completed recordings, but cannot start or stop one."
        : role === "administrator"
          ? "Your Administrator account can start, stop, replay, download, and delete recordings."
          : "Your Operator account can start, stop, replay, and download recordings; deletion requires an administrator.",
    }),
  },
];

function listNames(names: string[]): string {
  if (names.length <= 1) return names[0] ?? "";
  if (names.length === 2) return `${names[0]} and ${names[1]}`;
  return `${names.slice(0, -1).join(", ")}, and ${names.at(-1)}`;
}

function roleStep(user: AuthUser | null | undefined): TourStep {
  const role = user?.role ?? "observer";
  const label = role === "administrator" ? "Administrator" : role === "operator" ? "Operator" : "Observer";
  const access = role === "observer"
    ? "You can monitor the robot, customize this dashboard, and review completed recordings. Robot and recording controls remain read-only."
    : role === "administrator"
      ? "You can operate the robot, manage recordings, and perform administrator-only deletion."
      : "You can operate the robot and manage active recordings; destructive deletion remains administrator-only.";
  return {
    target: user?.auth_mode === "oidc" ? '[data-tour="account"]' : '[data-tour="overview"]',
    title: `Your access level: ${label}`,
    description: access,
    instruction: "Disabled controls will explain when your role or the robot's current condition prevents an action.",
  };
}

export function buildTourSteps(
  widgets: string[],
  user: AuthUser | null | undefined,
): TourStep[] {
  const visible = new Set(widgets);
  const missingNames = WIDGET_TOURS.filter((widget) => !visible.has(widget.id)).map((widget) => widget.name);
  const customize: TourStep = {
    target: '[data-tour="edit-dashboard"]',
    title: "Add and arrange your own widgets",
    description: missingNames.length > 0
      ? `This layout does not currently show ${listNames(missingNames)}. You can add any of them without replacing the widgets already here.`
      : "This layout already shows every available widget, but you can still remove, move, resize, or minimize them.",
    instruction: "Click Edit dashboard, then Add widget. Click Add beside each widget you want, arrange it, and click Done editing. Your current layout saves automatically.",
  };
  const role = user?.role ?? "observer";
  const widgetSteps = WIDGET_TOURS
    .filter((widget) => visible.has(widget.id))
    .map((widget) => typeof widget.step === "function" ? widget.step(role) : widget.step);
  return [
    ...CORE_STEPS.slice(0, 4),
    roleStep(user),
    CORE_STEPS[4],
    customize,
    ...widgetSteps,
    {
      target: '[data-tour="help"]',
      title: "Restart this tour any time",
      description: "The question-mark button remains available after onboarding. The tour will rebuild itself from your role and whichever widgets are in your current layout.",
      instruction: "Select Finish to return to the dashboard.",
    },
  ];
}

interface HighlightRect {
  left: number;
  top: number;
  width: number;
  height: number;
}

const EMPTY_RECT: HighlightRect = { left: 12, top: 12, width: 120, height: 44 };

function targetFor(step: TourStep): HTMLElement | null {
  return document.querySelector<HTMLElement>(step.target)
    ?? document.querySelector<HTMLElement>('[data-tour="dashboard"]');
}

export function DashboardTour() {
  const active = useUiStore((state) => state.tourActive);
  const request = useUiStore((state) => state.tourRequest);
  const finishTour = useUiStore((state) => state.finishTour);
  const widgets = useLayoutStore((state) => state.widgets);
  const user = useAuthStore((state) => state.user);
  const [stepIndex, setStepIndex] = useState(0);
  const [rect, setRect] = useState<HighlightRect>(EMPTY_RECT);
  const cardRef = useRef<HTMLDivElement>(null);
  const steps = useMemo(() => buildTourSteps(widgets, user), [widgets, user]);
  const safeStepIndex = Math.min(stepIndex, steps.length - 1);
  const step = steps[safeStepIndex];

  useEffect(() => {
    if (request > 0) setStepIndex(0);
  }, [request]);

  useEffect(() => {
    if (stepIndex >= steps.length) setStepIndex(Math.max(0, steps.length - 1));
  }, [stepIndex, steps.length]);

  const measure = useCallback(() => {
    if (!active) return;
    const target = targetFor(step);
    if (!target) {
      setRect(EMPTY_RECT);
      return;
    }
    const bounds = target.getBoundingClientRect();
    setRect({
      left: Math.max(6, bounds.left - 6),
      top: Math.max(6, bounds.top - 6),
      width: Math.max(40, Math.min(window.innerWidth - 12, bounds.width + 12)),
      height: Math.max(40, Math.min(window.innerHeight - 12, bounds.height + 12)),
    });
  }, [active, step]);

  useEffect(() => {
    if (!active) return;
    const target = targetFor(step);
    target?.scrollIntoView?.({ behavior: "smooth", block: "center", inline: "nearest" });
    measure();
    const settle = window.setTimeout(measure, 250);
    window.addEventListener("resize", measure);
    window.addEventListener("scroll", measure, true);
    return () => {
      window.clearTimeout(settle);
      window.removeEventListener("resize", measure);
      window.removeEventListener("scroll", measure, true);
    };
  }, [active, step, measure]);

  const cardPosition = useMemo(() => {
    const width = Math.min(360, window.innerWidth - 24);
    const height = cardRef.current?.offsetHeight || 230;
    const gap = 14;
    const below = rect.top + rect.height + gap;
    const top = below + height <= window.innerHeight - 12
      ? below
      : Math.max(12, rect.top - height - gap);
    const left = Math.max(12, Math.min(rect.left, window.innerWidth - width - 12));
    return { top, left, width };
  }, [rect]);

  const previous = () => setStepIndex((current) => Math.max(0, current - 1));
  const next = () => {
    if (safeStepIndex === steps.length - 1) finishTour();
    else setStepIndex((current) => current + 1);
  };

  useEffect(() => {
    if (!active) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "ArrowRight") next();
      if (event.key === "ArrowLeft") previous();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  });

  return (
    <Dialog.Root open={active} onOpenChange={(open) => { if (!open) finishTour(); }}>
      <Dialog.Portal>
        <Dialog.Overlay className="tour-overlay" />
        <div
          className="tour-spotlight"
          aria-hidden="true"
          style={{ left: rect.left, top: rect.top, width: rect.width, height: rect.height }}
        />
        <Dialog.Content
          ref={cardRef}
          className="tour-card"
          style={cardPosition}
          aria-describedby="tour-description tour-instruction"
        >
          <div className="tour-progress" aria-label={`Step ${safeStepIndex + 1} of ${steps.length}`}>
            {steps.map((item, index) => (
              <span key={`${item.target}-${item.title}`} className={index <= safeStepIndex ? "complete" : ""} />
            ))}
          </div>
          <span className="tour-count">Step {safeStepIndex + 1} of {steps.length}</span>
          <Dialog.Title asChild><h2>{step.title}</h2></Dialog.Title>
          <Dialog.Description asChild>
            <p id="tour-description">{step.description}</p>
          </Dialog.Description>
          <p id="tour-instruction" className="tour-instruction">{step.instruction}</p>
          <Dialog.Close asChild>
            <button className="tour-close" aria-label="Close guided tour"><X size={17} /></button>
          </Dialog.Close>
          <div className="tour-actions">
            <button className="btn" onClick={finishTour}>Skip tour</button>
            <div className="spacer" />
            <button className="btn icon" onClick={previous} disabled={safeStepIndex === 0} aria-label="Previous tour step">
              <ArrowLeft size={15} />
            </button>
            <button className="btn primary" onClick={next}>
              {safeStepIndex === steps.length - 1 ? "Finish" : "Next"}
              {safeStepIndex < steps.length - 1 && <ArrowRight size={15} />}
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
