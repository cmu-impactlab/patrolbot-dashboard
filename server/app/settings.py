from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PATROLBOT_", env_file=".env", extra="ignore")

    robot_token: str = "dev-token"
    database_path: str = "data/patrolbot.db"
    default_robot_id: str = "patrolbot-01"

    # Connection staleness thresholds (heartbeat age, seconds).
    online_threshold_s: float = 3.0
    offline_threshold_s: float = 10.0

    # Battery. Two 12 V lead-acid batteries in series; SOC from firmware is
    # unreliable, so estimates are voltage-trend based and labeled "Estimated".
    battery_low_percent: float = 20.0
    battery_critical_percent: float = 10.0
    battery_cutoff_voltage: float = 22.0

    event_limit: int = 5000
    battery_history_days: int = 7


settings = Settings()
