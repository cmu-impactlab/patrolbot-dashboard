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
    # Usernames granted the administrator role (comma separated). Admins are
    # implicitly operators too.
    admin_usernames: str = ""
    # Usernames granted the operator role (comma separated) — the only role
    # (besides administrator) allowed to send commands. Everyone else who
    # signs in is a read-only observer. Command authorization is read-only by
    # default; operators must be listed explicitly.
    operator_usernames: str = ""
    # If set (comma-separated Andrew IDs), ONLY these users may sign in —
    # anyone else authenticates fine at the IdP but is refused here.
    allowed_usernames: str = ""
    # Required email domain for OIDC sign-in (Google returns the Workspace
    # email). A verified email must end with "@<this domain>". Empty disables
    # the check (production startup refuses to run without it — see
    # validate_startup).
    oidc_email_domain: str = "andrew.cmu.edu"
    # HMAC key for session cookies; MUST be overridden in oidc deployments.
    session_secret: str = "dev-session-secret"
    session_ttl_s: int = 12 * 3600

    # Browser WebSocket Origin allowlist (comma-separated, e.g.
    # https://dashboard.example.edu). Empty disables the check for local dev;
    # production startup refuses to run without it (see validate_startup).
    allowed_origins: str = ""
    # Per-operator command rate limit: at most this many command.request frames
    # accepted per minute (token bucket). Excess is rejected with a clear reason.
    command_rate_per_min: int = 30
    # Per-browser-connection inbound frame cap; frames beyond this many per
    # second are dropped (a misbehaving or malicious client cannot flood the hub).
    max_ui_frames_per_s: int = 20
    # Sanity bound on navigation goal coordinates (metres from the map origin)
    # used when no occupancy map is available to bound them precisely.
    max_map_coordinate_m: float = 1000.0

    # "development" (default) keeps the permissive dev behavior. "production"
    # makes startup fail closed on insecure configuration (see validate_startup)
    # and marks cookies Secure.
    environment: str = "development"

    # Optional local map (map_server YAML+PGM pair). Served by /api/map when
    # no robot has streamed one — the real robot deliberately never transmits
    # its 7 MB map (it starves /scan; see docs/ARCHITECTURE.md).
    static_map_yaml: str = ""
    static_map_name: str = "CMU-Q Floor 1"

    @property
    def is_production(self) -> bool:
        return self.environment.strip().lower() == "production"

    @property
    def cookie_secure(self) -> bool:
        """Mark cookies Secure in production or whenever the callback URL is
        served over TLS. Stays False for the http TestClient / local dev so
        cookies are still returned."""
        return self.is_production or self.oidc_redirect_url.lower().startswith("https://")


# Default values that MUST be overridden before exposing the dashboard.
_DEV_SESSION_SECRET = "dev-session-secret"
_DEV_ROBOT_TOKEN = "dev-token"


def validate_startup(settings: "Settings") -> None:
    """Fail closed on insecure production configuration.

    Only enforced when PATROLBOT_ENVIRONMENT=production, so development and the
    test suite keep their permissive defaults. Raises RuntimeError listing every
    problem so the operator can fix them all at once.
    """
    if not settings.is_production:
        return

    problems: list[str] = []
    if settings.auth_mode != "oidc":
        problems.append("PATROLBOT_AUTH_MODE must be 'oidc' in production (no anonymous local access).")
    if settings.session_secret == _DEV_SESSION_SECRET or not settings.session_secret:
        problems.append("PATROLBOT_SESSION_SECRET must be set to a non-default value.")
    if settings.robot_token == _DEV_ROBOT_TOKEN or not settings.robot_token:
        problems.append("PATROLBOT_ROBOT_TOKEN must be set to a non-default value.")
    for name, value in (
        ("PATROLBOT_OIDC_ISSUER", settings.oidc_issuer),
        ("PATROLBOT_OIDC_CLIENT_ID", settings.oidc_client_id),
        ("PATROLBOT_OIDC_CLIENT_SECRET", settings.oidc_client_secret),
        ("PATROLBOT_OIDC_REDIRECT_URL", settings.oidc_redirect_url),
    ):
        if not value:
            problems.append(f"{name} must be configured for OIDC.")
    if not settings.oidc_email_domain and not settings.allowed_usernames:
        problems.append("Set PATROLBOT_OIDC_EMAIL_DOMAIN (or PATROLBOT_ALLOWED_USERNAMES) "
                        "to restrict who may sign in.")
    if not settings.allowed_origins:
        problems.append("PATROLBOT_ALLOWED_ORIGINS must list the dashboard origin(s).")
    if settings.oidc_redirect_url and not settings.oidc_redirect_url.lower().startswith("https://"):
        problems.append("PATROLBOT_OIDC_REDIRECT_URL must be https:// (TLS) in production.")

    if problems:
        raise RuntimeError("Refusing to start — insecure production configuration:\n  - "
                           + "\n  - ".join(problems))


settings = Settings()
