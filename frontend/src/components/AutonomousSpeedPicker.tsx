import { useId, useState } from "react";

type Speed = Readonly<{ linear_m_s: number; angular_rad_s: number }>;
type Profile = "slow" | "fast" | "custom";
export type AutonomousSpeedSelection = Speed & Readonly<{ profile: "Slow" | "Fast" | "Custom" }>;

type Props = {
  /** Local input constraints only; higher limits cannot widen the picker range. */
  limits?: Readonly<{ max_linear_m_s?: number; max_angular_rad_s?: number }>;
  /** Receives a locally validated selection, not an instruction to apply it. */
  onStage: (selection: AutonomousSpeedSelection) => void;
};

const PRESETS = {
  slow: { label: "Slow", linear_m_s: 0.12, angular_rad_s: 0.40 },
  fast: { label: "Fast", linear_m_s: 0.22, angular_rad_s: 0.70 },
  "really-fast": { label: "Really Fast", linear_m_s: 2.00, angular_rad_s: 0.70 },
} as const;

function pickerMax(requested: number | undefined, ceiling: number): number | null {
  if (requested === undefined) return ceiling;
  return withinRange(requested, ceiling) ? requested : null;
}

function withinRange(value: number, max: number | null): boolean {
  return max !== null && Number.isFinite(value) && value >= 0 && value <= max;
}

function numberOrNaN(text: string): number {
  return text.trim() === "" ? NaN : Number(text);
}

function displayNumber(value: number): string {
  const fixed = value.toFixed(2);
  return Number(fixed) === value ? fixed : String(value);
}

function displaySpeed(speed: Speed): string {
  return `${displayNumber(speed.linear_m_s)} m/s, ${displayNumber(speed.angular_rad_s)} rad/s`;
}

function SpeedInput({ id, label, unit, value, max, onChange }: {
  id: string; label: string; unit: string; value: string; max: number | null;
  onChange: (value: string) => void;
}) {
  const invalid = !withinRange(numberOrNaN(value), max);
  return (
    <p>
      <label htmlFor={id}>{label} ({unit})</label>{" "}
      <input id={id} type="number" inputMode="decimal" step="any" min={0} max={max ?? undefined}
        value={value} onChange={(event) => onChange(event.currentTarget.value)}
        aria-describedby={`${id}-range`} aria-invalid={invalid || undefined} />
      <br />
      <small id={`${id}-range`}>
        {max === null ? "Limit unavailable." : `Enter a value from 0 to ${displayNumber(max)} ${unit}.`}
      </small>
    </p>
  );
}

export function AutonomousSpeedPicker({ limits, onStage }: Props) {
  const id = useId();
  const [profile, setProfile] = useState<Profile>("fast");
  const [linearText, setLinearText] = useState(() => displayNumber(PRESETS.fast.linear_m_s));
  const [angularText, setAngularText] = useState(() => displayNumber(PRESETS.fast.angular_rad_s));
  const [staged, setStaged] = useState<AutonomousSpeedSelection | null>(null);

  // Local editing limits, not live robot limits or evidence of commissioning.
  const maxLinear = pickerMax(limits?.max_linear_m_s, 0.26);
  const maxAngular = pickerMax(limits?.max_angular_rad_s, 0.70);
  const { linear_m_s, angular_rad_s } = profile === "custom"
    ? { linear_m_s: numberOrNaN(linearText), angular_rad_s: numberOrNaN(angularText) }
    : PRESETS[profile];
  const canStage = withinRange(linear_m_s, maxLinear) && withinRange(angular_rad_s, maxAngular);

  function stage() {
    if (!canStage) return;
    const selection: AutonomousSpeedSelection = {
      profile: profile === "custom" ? "Custom" : PRESETS[profile].label,
      linear_m_s,
      angular_rad_s,
    };
    setStaged(selection);
    onStage({ ...selection });
  }

  return (
    <fieldset aria-describedby={`${id}-scope`}>
      <legend>Autonomous speed</legend>
      <p id={`${id}-scope`}>Confirming stages a local selection only. It does not apply speed on the robot.</p>
      {(["slow", "fast", "really-fast"] as const).map((key) => (
        <div key={key}>
          <label>
            <input type="radio" name={id} value={key} checked={profile === key}
              disabled={key === "really-fast"} aria-describedby={`${id}-${key}-details`}
              onChange={() => { if (key !== "really-fast") setProfile(key); }} />
            {PRESETS[key].label}
          </label>
          {" — "}<span id={`${id}-${key}-details`}>
            {displaySpeed(PRESETS[key])}
            {key === "fast" && " (default)"}
            {key === "really-fast" && " (uncommissioned; unavailable)"}
          </span>
        </div>
      ))}
      <label>
        <input type="radio" name={id} value="custom" checked={profile === "custom"}
          onChange={() => setProfile("custom")} />
        Custom
      </label>
      {profile === "custom" && (
        <fieldset>
          <legend>Custom speed limits</legend>
          <SpeedInput id={`${id}-linear`} label="Linear speed" unit="m/s" value={linearText}
            max={maxLinear} onChange={setLinearText} />
          <SpeedInput id={`${id}-angular`} label="Angular speed" unit="rad/s" value={angularText}
            max={maxAngular} onChange={setAngularText} />
        </fieldset>
      )}
      {(maxLinear === null || maxAngular === null) ? <p>Picker limits are unavailable; staging is disabled.</p>
        : !canStage && profile !== "custom" && <p>This preset exceeds the current picker limits.</p>}
      <p><button type="button" className="btn" disabled={!canStage} onClick={stage}>Confirm and stage</button></p>
      <p role="status">
        Last locally staged: {staged ? `${staged.profile} — ${displaySpeed(staged)}` : "none"}
      </p>
      <p>Applied speed: not reported by this picker.</p>
    </fieldset>
  );
}
