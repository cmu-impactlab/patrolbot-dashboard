from app.telemetry.battery_estimator import BatteryRuntimeEstimator, _TrendEstimator


def test_charging_short_circuits():
    est = BatteryRuntimeEstimator()
    result = est.estimate(charging=True, has_percentage=True)
    assert result.state == "charging"
    assert result.minutes_remaining is None


def test_insufficient_samples_is_calculating():
    est = _TrendEstimator(lower_threshold=0.2)
    est.add_sample(0.0, 0.9)
    assert est.estimate(charging=False).state == "calculating"


def test_linear_percentage_drain_estimates_minutes():
    est = _TrendEstimator(lower_threshold=0.2)
    # 0.1% per second decline over 90 samples.
    for i in range(90):
        est.add_sample(i * 1.0, 0.9 - 0.001 * i)
    result = est.estimate(charging=False)
    assert result.state == "estimated"
    # remaining = (0.811 - 0.2) / 0.001 per s ≈ 611 s ≈ 10 min
    assert 8 <= result.minutes_remaining <= 13
    assert result.confidence == "high"


def test_stable_when_flat():
    est = _TrendEstimator(lower_threshold=0.2)
    for i in range(20):
        est.add_sample(i * 1.0, 0.8)
    assert est.estimate(charging=False).state == "stable"


def test_threshold_reached():
    est = _TrendEstimator(lower_threshold=0.5)
    for i in range(10):
        est.add_sample(i * 1.0, 0.5 - 0.001 * i)
    result = est.estimate(charging=False)
    assert result.state == "threshold_reached"
    assert result.minutes_remaining == 0


def test_voltage_mode_used_when_no_percentage():
    est = BatteryRuntimeEstimator(low_percent=20.0, cutoff_voltage=22.0)
    # Voltage declining from 25.0 at 0.001 V/s -> (24.941-22)/0.001 s ≈ 49 min
    for i in range(60):
        est.add_sample(float(i), voltage=25.0 - 0.001 * i, percentage=None, charging=False)
    result = est.estimate(charging=False, has_percentage=False)
    assert result.state == "estimated"
    assert 40 <= result.minutes_remaining <= 60


def test_charge_session_resets_discharge_trend():
    est = BatteryRuntimeEstimator()
    for i in range(30):
        est.add_sample(float(i), voltage=24.0 - 0.01 * i, percentage=None, charging=False)
    est.add_sample(31.0, voltage=24.0, percentage=None, charging=True)
    # After charging starts, previous trend must be gone.
    result = est.estimate(charging=False, has_percentage=False)
    assert result.state == "calculating"
