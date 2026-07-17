from ..settings import Settings


def create_database(settings: Settings):
    """Pick the persistence backend from configuration.

    SQLite (default) for single-host use; PostgreSQL when
    PATROLBOT_DATABASE_URL is set. Both expose the same interface.
    """
    if settings.database_url.startswith(("postgres://", "postgresql://")):
        from .pg import PostgresDatabase

        return PostgresDatabase(settings.database_url)
    from .repo import Database

    return Database(settings.database_path)
