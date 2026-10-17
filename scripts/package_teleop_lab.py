"""Assemble an allowlisted test context; never send credentials/data/backups."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil


parser = argparse.ArgumentParser()
parser.add_argument("output", type=Path)
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
output = args.output.resolve()
output.mkdir(parents=True, exist_ok=False)
mapping = {
    "server": root / "server",
    "ros2_ws/src/patrolbot_web_bridge": root / "ros2_ws/src/patrolbot_web_bridge",
    "contracts": root / "contracts",
    "shared/schemas": root / "shared/schemas",
    "frontend/dist": root / "frontend/dist",
}
excluded = {".venv", "build", "__pycache__", ".pytest_cache", ".git", "data", "node_modules", "backups"}
manifest = {}
for destination, source in mapping.items():
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if any(part in excluded or part.startswith(".env") for part in relative.parts):
            continue
        if path.is_symlink():
            raise SystemExit("Unreviewed symlink in test context: " + str(path))
        if not path.is_file() or path.suffix in {".pyc", ".db", ".sqlite3", ".bak"}:
            continue
        target = output / destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        manifest[str(target.relative_to(output))] = hashlib.sha256(target.read_bytes()).hexdigest()
for name in ("teleop_lab.py", "check_teleop_receipt.py", "run_teleop_lab.sh"):
    target = output / "scripts" / name
    target.parent.mkdir(exist_ok=True)
    shutil.copy2(root / "scripts" / name, target)
    manifest[str(target.relative_to(output))] = hashlib.sha256(target.read_bytes()).hexdigest()
shutil.copy2(root / "infrastructure/teleop-lab/Dockerfile", output / "Dockerfile")
(output / "CONTEXT.sha256.json").write_text(json.dumps(manifest, indent=2) + "\n")
print(json.dumps({"context": str(output), "files": len(manifest),
                  "implementation_bind_mounts": False, "physical_acceptance": False}))
