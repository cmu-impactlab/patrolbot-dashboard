#!/usr/bin/env python3
"""Byte-for-byte parity for the shared robot-tuning contract.

The canonical robot-tuning contract lives in the robot repository
(``cmu-impactlab/patrolbot``) under ``contracts/``. The dashboard vendors the
exact same files so both sides validate one schema, registry and fixture set
instead of drifting apart. This script compares the vendored copies against a
LOCAL canonical checkout that the caller names explicitly. It never opens a
network connection and never fetches anything: if the canonical tree is not on
disk, it fails with a clear message rather than guessing.

The compared artifacts are the ones the robot-side owner authors:

    contracts/robot-tuning.schema.json
    contracts/robot-tuning.registry.json
    contracts/robot-tuning.fixtures.json
    contracts/validate_robot_tuning.py
    contracts/test_robot_tuning.py
    contracts/requirements-test.txt

Parity is exact: same bytes, same sha256. A semantic diff would let the two
repos disagree about, for example, an allowed range or a protected flag while
still "passing". Every copied file must be the canonical file. All six files are
required: the comparison is fail-closed, so an empty or partial vendor directory
is an error, never a silent pass.

Exit codes:
    0  every canonical artifact exists and every vendored copy matches
    1  a vendored copy is missing, unreadable or differs from the canonical
    2  the canonical repository or one of its artifacts is missing or unreadable

Usage:
    python scripts/check_tuning_contract.py --core-repo ../patrolbot-repo
    python scripts/check_tuning_contract.py --core-repo "$PWD/core" --report-json /tmp/parity.json
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import sys
from pathlib import Path

# Exact relative paths, in the order the robot-side owner publishes them. Keep
# this list literal and ordered: it is the definition of "the shared contract".
CONTRACT_FILES: tuple[str, ...] = (
    "robot-tuning-auth.json",
    "robot-tuning-runtime.json",
    "robot-tuning.schema.json",
    "robot-tuning.registry.json",
    "robot-tuning.fixtures.json",
    "validate_robot_tuning.py",
    "test_robot_tuning.py",
    "requirements-test.txt",
)

CONTRACT_SUBDIR = "contracts"

# Status vocabulary shared by the report and the exit-code decision.
MATCH = "match"
MISMATCH = "mismatch"
MISSING_VENDORED = "missing-vendored"
UNREADABLE_VENDORED = "unreadable-vendored"
MISSING_CORE = "missing-core"
UNREADABLE_CORE = "unreadable-core"

# Every vendored-side problem fails the run (exit 1). Every canonical-side
# problem is the source-of-truth being unavailable (exit 2).
VENDORED_FAILURES = frozenset({MISMATCH, MISSING_VENDORED, UNREADABLE_VENDORED})
CORE_FAILURES = frozenset({MISSING_CORE, UNREADABLE_CORE})


@dataclasses.dataclass(frozen=True)
class FileComparison:
    name: str
    core_path: str
    vendored_path: str
    status: str
    core_sha256: str | None = None
    vendored_sha256: str | None = None
    detail: str | None = None

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


def repo_root() -> Path:
    """The dashboard repository root (this file lives in ``scripts/``)."""
    return Path(__file__).resolve().parent.parent


def sha256_file(path: Path) -> str:
    """Hash a file's bytes. OSError (permission denied, I/O error) propagates
    to the caller, which turns it into a predictable exit code rather than a
    traceback."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def compare_contracts(core_repo: Path, vendored_dir: Path) -> list[FileComparison]:
    """Compare every canonical artifact with its vendored copy.

    ``core_repo`` is the robot repository root; ``vendored_dir`` is the
    dashboard's ``contracts/`` directory. A missing canonical artifact is
    reported as ``missing-core`` regardless of the vendored copy, because the
    canonical tree is the source of truth. A canonical artifact that exists but
    cannot be read is ``unreadable-core``; a vendored copy that exists but
    cannot be read is ``unreadable-vendored``. Both are fatal.
    """
    core_dir = core_repo / CONTRACT_SUBDIR
    results: list[FileComparison] = []
    for name in CONTRACT_FILES:
        core_path = core_dir / name
        vendored_path = vendored_dir / name
        if not core_path.is_file():
            results.append(FileComparison(
                name=name, core_path=str(core_path), vendored_path=str(vendored_path),
                status=MISSING_CORE,
                detail=f"canonical artifact not found in the provided core repository: {core_path}",
            ))
            continue
        try:
            core_sha = sha256_file(core_path)
        except OSError as exc:
            results.append(FileComparison(
                name=name, core_path=str(core_path), vendored_path=str(vendored_path),
                status=UNREADABLE_CORE,
                detail=f"cannot read canonical artifact: {exc}",
            ))
            continue

        if not vendored_path.is_file():
            results.append(FileComparison(
                name=name, core_path=str(core_path), vendored_path=str(vendored_path),
                status=MISSING_VENDORED, core_sha256=core_sha,
                detail=f"not vendored: {vendored_path}",
            ))
            continue

        try:
            vendored_sha = sha256_file(vendored_path)
        except OSError as exc:
            results.append(FileComparison(
                name=name, core_path=str(core_path), vendored_path=str(vendored_path),
                status=UNREADABLE_VENDORED, core_sha256=core_sha,
                detail=f"cannot read vendored copy: {exc}",
            ))
            continue

        if core_sha == vendored_sha:
            results.append(FileComparison(
                name=name, core_path=str(core_path), vendored_path=str(vendored_path),
                status=MATCH, core_sha256=core_sha, vendored_sha256=vendored_sha,
            ))
        else:
            results.append(FileComparison(
                name=name, core_path=str(core_path), vendored_path=str(vendored_path),
                status=MISMATCH, core_sha256=core_sha, vendored_sha256=vendored_sha,
                detail="vendored copy differs from the canonical file",
            ))
    return results


def exit_code(results: list[FileComparison]) -> int:
    """0 clean, 1 vendored problem, 2 canonical problem. Canonical wins: a
    missing source of truth is never reported as a mere vendoring drift."""
    if any(result.status in CORE_FAILURES for result in results):
        return 2
    if any(result.status in VENDORED_FAILURES for result in results):
        return 1
    return 0


def format_report(core_repo: Path, vendored_dir: Path,
                  results: list[FileComparison]) -> str:
    lines = [
        f"core repository : {core_repo}",
        f"vendored copies : {vendored_dir}",
        "",
    ]
    for result in results:
        marker = "OK  " if result.status == MATCH else "FAIL"
        if result.status == MATCH:
            lines.append(f"{marker} {result.name}  sha256 {result.core_sha256}")
        elif result.status == MISMATCH:
            lines.append(f"{marker} {result.name}  vendored differs from canonical")
            lines.append(f"       canonical {result.core_sha256}")
            lines.append(f"       vendored  {result.vendored_sha256}")
        elif result.status == MISSING_VENDORED:
            lines.append(f"{marker} {result.name}  not vendored: {result.vendored_path}")
        else:
            lines.append(f"{marker} {result.name}  {result.detail}")
    counts: dict[str, int] = {}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    lines.append("")
    lines.append("summary: " + ", ".join(
        f"{status}={counts[status]}" for status in sorted(counts)))
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare vendored robot-tuning contract files, byte for "
                    "byte, against an explicitly provided local robot repo.")
    parser.add_argument(
        "--core-repo", required=True, type=Path,
        help="path to the local robot repository that carries contracts/robot-tuning.*")
    parser.add_argument(
        "--vendored-dir", type=Path, default=None,
        help="dashboard contracts directory (default: <repo>/contracts)")
    parser.add_argument(
        "--report-json", type=Path, default=None,
        help="write the comparison as JSON to this path")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    core_repo = args.core_repo.resolve()
    vendored_dir = (args.vendored_dir or repo_root() / CONTRACT_SUBDIR).resolve()

    if not core_repo.is_dir():
        print(f"core repository not found: {core_repo}", file=sys.stderr)
        return 2

    results = compare_contracts(core_repo, vendored_dir)
    report = format_report(core_repo, vendored_dir, results)
    print(report)

    if args.report_json is not None:
        args.report_json.write_text(json.dumps({
            "core_repo": str(core_repo),
            "vendored_dir": str(vendored_dir),
            "exit_code": exit_code(results),
            "files": [result.as_dict() for result in results],
        }, indent=2, sort_keys=True) + "\n")

    code = exit_code(results)
    if code == 2:
        print("\nCanonical robot-tuning artifacts are missing or unreadable. Point "
              "--core-repo at a checkout that carries contracts/robot-tuning.*.",
              file=sys.stderr)
    elif code == 1:
        print("\nVendored contract is missing, unreadable or drifted. Vendor the "
              "exact canonical files; do not edit them in the dashboard.",
              file=sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
