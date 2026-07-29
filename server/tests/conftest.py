"""Test-wide isolation from the developer's local configuration.

`Settings` reads `server/.env` so `make server` can be pointed at real Google
OIDC credentials. pytest runs with `server/` as the working directory, so
without this the suite inherits that file: `auth_mode=oidc` turns every
unauthenticated fixture request into a 401, and a local username allowlist
rejects the users the auth tests invent. The result was a suite whose outcome
depended on whether the machine had ever been configured to run the dashboard.

Applied at import time, before any test constructs Settings.
"""
import os

from app.settings import Settings

# Ignore server/.env for the whole run...
Settings.model_config["env_file"] = None

# ...and any PATROLBOT_* variable already exported into the shell, which would
# otherwise outrank both the .env file and the tests' explicit overrides.
for _name in [name for name in os.environ if name.startswith("PATROLBOT_")]:
    del os.environ[_name]
