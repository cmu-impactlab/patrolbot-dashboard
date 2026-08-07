from ..settings import Settings


def create_database(settings: Settings):
    """Pick the persistence backend from configuration.

    SQLite is *the* supported backend — the dashboard is a single-host
    deployment and SQLite covers it comfortably (the live database is ~30 MB,
    dominated by battery samples).

    The PostgreSQL path below is retained but unsupported: not deployed, not
    in CI, and not advertised anywhere in the deployment configuration. It is
    selected only by explicitly setting PATROLBOT_DATABASE_URL. See
    settings.database_url.
    """
    if settings.database_url.startswith(("postgres://", "postgresql://")):
        from .pg import PostgresDatabase

        return PostgresDatabase(settings.database_url)
    from .repo import Database

    return Database(settings.database_path)
