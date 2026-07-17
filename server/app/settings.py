from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PATROLBOT_", env_file=".env", extra="ignore")

    robot_token: str = "dev-token"
    database_path: str = "data/patrolbot.db"
    # Set to postgresql://user:pass@host/db to use PostgreSQL instead of
    # SQLite (Phase 5 multi-user deployments). Requires the asyncpg extra.
    database_url: str = ""
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

    # Seconds a robot may sit still while it has an active destination before
    # the dashboard flags it as stuck.
    stall_warning_s: float = 15.0

    # Authentication. "local" = single seeded user (default, current behavior).
    # "oidc" = OpenID Connect authorization-code flow — point the issuer at
    # CMU's IdP (or any OIDC provider) and register the /auth/callback URL.
    auth_mode: str = "local"
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_redirect_url: str = ""  # e.g. https://dashboard.example.edu/auth/callback
    # Usernames granted the administrator role (comma separated); everyone
    # else authenticates as operator.
    admin_usernames: str = ""
    # If set (comma-separated Andrew IDs), ONLY these users may sign in —
    # anyone else authenticates fine at the IdP but is refused here.
    allowed_usernames: str = ""
    # HMAC key for session cookies; MUST be overridden in oidc deployments.
    session_secret: str = "dev-session-secret"
    session_ttl_s: int = 12 * 3600

    # Optional local map (map_server YAML+PGM pair). Served by /api/map when
    # no robot has streamed one — the real robot deliberately never transmits
    # its 7 MB map (it starves /scan; see docs/ARCHITECTURE.md).
    static_map_yaml: str = ""
    static_map_name: str = "CMU-Q Floor 1"


settings = Settings()
