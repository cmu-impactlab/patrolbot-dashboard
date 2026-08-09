"""The production browser security-header policy, checked as configuration.

Serving these is nginx's job, not the app's, so there is no request to make in
this suite. What is checkable — and what actually regressed before — is the
configuration itself: a header commented out "until TLS is confirmed" and never
uncommented, or a single `add_header` added inside a location, which makes
nginx drop *every* server-level header for that location rather than merging
them. Both are silent in testing and only visible to a browser in production.
"""
import re
from pathlib import Path

import pytest

NGINX_CONF = (Path(__file__).resolve().parents[2]
              / "infrastructure" / "nginx" / "nginx.conf")

# Sets, because a CSP source list is order-independent: reordering sources is
# an equivalent policy and should not fail this.
EXPECTED_CSP = {
    "default-src": {"'self'"},
    "script-src": {"'self'"},
    # Required by the server-rendered sign-in rejection page; see nginx.conf.
    "style-src": {"'self'", "'unsafe-inline'"},
    "img-src": {"'self'", "data:"},  # the grid CSS inlines its handle SVG
    "font-src": {"'self'"},
    "connect-src": {"'self'"},  # covers same-origin wss:// (CSP3 §6.7.2.8)
    "frame-ancestors": {"'none'"},
    "base-uri": {"'self'"},
    "form-action": {"'self'"},
    "object-src": {"'none'"},
}


def _block_after(text: str, open_brace: int) -> tuple[str, int]:
    """The brace-balanced body starting just past `open_brace`, and its end."""
    depth, index = 1, open_brace + 1
    while depth:
        depth += {"{": 1, "}": -1}.get(text[index], 0)
        index += 1
    return text[open_brace + 1:index - 1], index


def _bodies(text: str, opener: str) -> list[str]:
    """Every brace-balanced body whose header matches `opener`."""
    return [_block_after(text, match.end() - 1)[0]
            for match in re.finditer(opener, text)]


@pytest.fixture(scope="module")
def tls_server_block() -> str:
    """The TLS `server` block, comments stripped.

    Stripping comments is the point: a commented-out add_header is exactly the
    failure this guards against, so it must not count as present. Parsing is
    brace-balanced and selects by content rather than by file offsets, so
    reordering the file or adding another server block cannot quietly narrow
    what is checked.
    """
    conf = "\n".join(line for line in NGINX_CONF.read_text().splitlines()
                     if not line.strip().startswith("#"))
    tls = [body for body in _bodies(conf, r"\bserver\s*\{")
           if "listen 443" in body]
    assert len(tls) == 1, f"expected exactly one TLS server block, found {len(tls)}"
    return tls[0]


def header(block: str, name: str) -> str:
    match = re.search(rf'add_header\s+{name}\s+"([^"]*)"\s+always;', block)
    assert match is not None, f"{name} is not set (or not `always`)"
    return match.group(1)


@pytest.mark.parametrize("name, expected", [
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "same-origin"),
])
def test_simple_headers(tls_server_block, name, expected):
    assert header(tls_server_block, name) == expected


def test_hsts_is_enabled_without_committing_other_names(tls_server_block):
    value = header(tls_server_block, "Strict-Transport-Security")
    max_age = re.search(r"max-age=(\d+)", value)
    assert max_age is not None and int(max_age.group(1)) >= 15_552_000  # 6 months
    # includeSubDomains and preload bind names this deployment does not own and
    # are close to irreversible; adding them should be a deliberate decision.
    assert "includeSubDomains" not in value and "preload" not in value


def test_permissions_policy_denies_the_hardware_the_dashboard_never_uses(
        tls_server_block):
    value = header(tls_server_block, "Permissions-Policy")
    for feature in ("camera", "microphone", "geolocation"):
        assert f"{feature}=()" in value


def test_content_security_policy_is_exactly_what_was_reasoned_about(
        tls_server_block):
    """An exact comparison, not a substring check: appending a source to an
    existing directive — `script-src 'self' https:` — is precisely the kind of
    quiet loosening that a "contains 'self'" assertion would wave through."""
    csp = header(tls_server_block, "Content-Security-Policy")
    directives = {parts[0]: set(parts[1:]) for parts in
                  (d.split() for d in csp.split(";") if d.strip())}
    assert directives == EXPECTED_CSP


def test_no_location_overrides_the_header_policy(tls_server_block):
    """nginx replaces rather than merges: one add_header inside a location
    silently drops every server-level header for that location."""
    for body in _bodies(tls_server_block, r"\blocation\s+[^{]*\{"):
        assert "add_header" not in body, (
            f"add_header inside a location drops the whole policy:\n{body}")
