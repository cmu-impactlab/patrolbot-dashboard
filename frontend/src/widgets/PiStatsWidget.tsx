import { Line, LineChart, ResponsiveContainer, YAxis } from "recharts";
import { useEffect, useState } from "react";
import { useTelemetryStore } from "../stores/telemetryStore";
import { MAX_RESOURCES_AGE_MS, useIsFresh } from "../lib/freshness";

function useTelemetryRate(): number {
  const [rate, setRate] = useState(0);
  useEffect(() => {
    let last = useTelemetryStore.getState().frameCount;
    const timer = setInterval(() => {
      const now = useTelemetryStore.getState().frameCount;
      setRate(now - last);
      last = now;
    }, 1000);
    return () => clearInterval(timer);
  }, []);
  return rate;
}

export function PiStatsWidget() {
  const resources = useTelemetryStore((state) => state.resources);
  const history = useTelemetryStore((state) => state.resourceHistory);
  const wsConnected = useTelemetryStore((state) => state.wsConnected);
  const rate = useTelemetryRate();
  const fresh = useIsFresh(
    useTelemetryStore((state) => state.resourcesAt), MAX_RESOURCES_AGE_MS);

  if (!resources) {
    return <p className="subtext">No data from the robot's onboard computer yet.</p>;
  }

  if (!fresh) {
    // CPU load and temperature are statements about right now. Left on screen
    // after the reports stop, they are a picture of a moment that has passed.
    return (
      <p className="subtext">
        The robot's onboard computer has stopped reporting itself. The last
        readings are too old to rely on.
      </p>
    );
  }

  return (
    <div>
      <div className="kv-list">
        <div className="kv">
          <span className="k">Processor load</span>
          <span className="v">{resources.cpu_percent.toFixed(0)}%</span>
        </div>
        <div className="kv">
          <span className="k">Temperature</span>
          <span className="v">
            {resources.cpu_temp_c != null ? `${resources.cpu_temp_c.toFixed(0)} °C` : "—"}
          </span>
        </div>
        <div className="kv">
          <span className="k">Memory</span>
          <span className="v">{resources.memory_percent.toFixed(0)}%</span>
        </div>
        <div className="kv">
          <span className="k">Storage used</span>
          <span className="v">{resources.disk_percent.toFixed(0)}%</span>
        </div>
        <div className="kv">
          <span className="k">Wi-Fi signal</span>
          <span className="v">
            {resources.wifi_signal_dbm != null ? `${resources.wifi_signal_dbm} dBm` : "—"}
          </span>
        </div>
        <div className="kv">
          <span className="k">Dashboard link</span>
          <span className="v">{wsConnected ? "Connected" : "Reconnecting…"}</span>
        </div>
        <div className="kv">
          <span className="k">Telemetry rate</span>
          <span className="v">{rate} msg/s</span>
        </div>
      </div>
      <div className="section-label">Processor load</div>
      <div className="chart-box" style={{ height: 70 }}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={history} margin={{ top: 4, right: 2, bottom: 0, left: 2 }}>
            <YAxis domain={[0, 100]} hide />
            <Line
              dataKey="cpu"
              stroke="var(--info)"
              strokeWidth={1.5}
              dot={false}
              isAnimationActive={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
