from ..settings import Settings
from .repo import Database


def create_database(settings: Settings) -> Database:
    """Open SQLite at the configured path with the configured history bounds."""
    return Database(settings.database_path, event_limit=settings.event_limit,
                    battery_history_days=settings.battery_history_days)
