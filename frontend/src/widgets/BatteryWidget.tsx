import { useChartInspection } from "../lib/useChartInspection";
import { Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useBatteryHistory } from "../api/queries";
import { formatMinutes } from "../lib/format";
import { ESTIMATE_COPY } from "../lib/plainLanguage";
import { useTelemetryStore } from "../stores/telemetryStore";
import { MAX_BATTERY_AGE_MS, useIsFresh } from "../lib/freshness";

export function BatteryWidget() {
  const inspection = useChartInspection();
  const battery = useTelemetryStore((state) => state.battery);
  const batteryFresh = useIsFresh(
    useTelemetryStore((state) => state.batteryAt), MAX_BATTERY_AGE_MS);
  const history = useBatteryHistory(120);

  if (!battery) {
    return <p className="subtext">No battery data yet.</p>;
  }

  if (!batteryFresh) {
    // The robot's battery stops being reported when its drive base goes off,
    // while the Pi keeps the socket alive. Showing the last percentage as the
    // current charge is how an operator plans a task around a battery nobody
    // has heard from.
    return (
      <p className="subtext">
        The robot has stopped reporting its battery. The last reading was{" "}
        {battery.percentage != null
          ? `${Math.round(battery.percentage)}%`
          : `${battery.voltage.toFixed(1)} V`}
        , but it is too old to rely on.
      </p>
    );
  }

  const percentage = battery.percentage;
  const estimate = battery.estimate;
  const fillClass = battery.charging
    ? "charging"
    : percentage != null && percentage <= 10
      ? "critical"
      : percentage != null && percentage <= 20
        ? "low"
        : "";

  const chartData = (history.data ?? []).map((row) => ({
    time: row.ts.slice(11, 16),
    voltage: row.voltage,
    percentage: row.percentage,
  }));

  return (
    <div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
        <span className="big-number">
          {percentage != null ? `${Math.round(percentage)}%` : `${battery.voltage.toFixed(1)} V`}
        </span>
        {battery.charging && <span className="status-pill tone-info">Charging</span>}
      </div>
      {percentage != null && (
        <div className="battery-bar">
          <div className={`fill ${fillClass}`} style={{ width: `${Math.min(100, percentage)}%` }} />
        </div>
      )}
      <div className="kv-list">
        <div className="kv">
          <span className="k">Voltage</span>
          <span className="v">{battery.voltage.toFixed(1)} V</span>
        </div>
        <div className="kv">
          <span className="k">Time remaining</span>
          <span className="v">
            {estimate?.state === "estimated"
              ? `Estimated ${formatMinutes(estimate.minutes_remaining)}`
              : ESTIMATE_COPY[estimate?.state ?? "calculating"] ?? "—"}
          </span>
        </div>
        {estimate?.state === "estimated" && (
          <div className="kv">
            <span className="k">Estimate confidence</span>
            <span className="v" style={{ textTransform: "capitalize" }}>{estimate.confidence}</span>
          </div>
        )}
        {battery.current != null && (
          <div className="kv">
            <span className="k">Current</span>
            <span className="v">{battery.current.toFixed(1)} A</span>
          </div>
        )}
      </div>
      <div className="section-label">Voltage — last 2 hours</div>
      <div {...inspection.events} className="chart-box">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={chartData} margin={{ top: 4, right: 4, bottom: 0, left: -18 }}>
            <XAxis dataKey="time" tick={{ fontSize: 10 }} minTickGap={40} stroke="var(--text-faint)" />
            <YAxis
              domain={["dataMin - 0.4", "dataMax + 0.4"]}
              tick={{ fontSize: 10 }}
              stroke="var(--text-faint)"
              tickFormatter={(value: number) => value.toFixed(1)}
            />
            <Tooltip trigger={inspection.trigger}
              contentStyle={{
                background: "var(--surface)",
                border: "1px solid var(--border)",
                borderRadius: 8,
                fontSize: 12,
              }}
              formatter={(value) => [`${Number(value).toFixed(2)} V`, "Voltage"]}
            />
            <Area
              type="monotone"
              dataKey="voltage"
              stroke="var(--info)"
              fill="var(--info-bg)"
              strokeWidth={1.6}
              isAnimationActive={false}
              dot={false}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
      <p className="subtext" style={{ marginTop: 6 }}>
        Runtime is an estimate from recent voltage trends, not an exact hardware reading.
      </p>
    </div>
  );
}
