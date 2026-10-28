"""Browser acceptance is manual; automatic CI retains frontend unit/build checks."""
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]


def test_image_matches_lockfile_and_uses_immutable_digest():
    lock = json.loads((ROOT / 'frontend/package-lock.json').read_text())
    workflow = (ROOT / '.github/workflows/mobile-browser.yml').read_text()
    match = re.search(r'image: mcr\.microsoft\.com/playwright:v([^\s@]+)-noble@sha256:([0-9a-f]{64})', workflow)
    assert match, 'browser CI must use a versioned, digest-pinned Playwright image'
    assert match.group(1) == lock['packages']['node_modules/playwright']['version']
    assert match.group(1) == lock['packages']['node_modules/@playwright/test']['version']


def test_browser_acceptance_is_manual_and_unsharded():
    workflow = (ROOT / '.github/workflows/mobile-browser.yml').read_text()
    assert 'workflow_dispatch:' in workflow
    assert 'pull_request:' not in workflow
    assert 'push:' not in workflow
    assert 'matrix:' not in workflow
    assert '--shard' not in workflow
    assert 'playwright install' not in workflow
    assert 'check-browser-runtime.mjs' in workflow
    assert 'npm run check:e2e' in workflow
    assert 'npm run test:e2e -- --reporter=line' in workflow
    assert 'npm run test:mock-browser' in workflow
    assert 'continue-on-error' not in workflow


def test_automatic_frontend_unit_typecheck_and_build_gate_remains():
    workflow = (ROOT / '.github/workflows/ci.yml').read_text()
    assert 'pull_request:' in workflow
    assert 'npx vitest run' in workflow
    assert 'npx tsc --noEmit' in workflow
    assert 'npx vite build' in workflow
    assert 'npm run check-bundle' in workflow
