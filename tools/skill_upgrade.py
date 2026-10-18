"""Stage a manifest-verified skill side by side. Never replaces an installation."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath


def verified(root):
    root = Path(root).resolve(strict=True)
    manifest_path = root / "release_manifest.json"
    raw = manifest_path.read_bytes(); value = json.loads(raw)
    if value.get("schema") != "public-release-allowlist/v1":
        raise ValueError("Unsupported release manifest")
    files = {"release_manifest.json": raw}
    for row in value["files"]:
        name = row["path"]; relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts or "\\" in name or ":" in name or name in files:
            raise ValueError("Unsafe or duplicate release path")
        path = root / name
        path.resolve(strict=True).relative_to(root)
        if any(parent.is_symlink() for parent in [path, *path.parents] if parent != root and root in parent.parents):
            raise ValueError("Linked release path refused")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise ValueError("Release file changed: " + name)
        files[name] = data
    if "SKILL.md" not in files:
        raise ValueError("Skill entry missing")
    return files, hashlib.sha256(raw).hexdigest()


def stage_upgrade(candidate, installed, destination, preserve=("local-adapter.md",)):
    candidate = Path(candidate).resolve(strict=True); installed = Path(installed).resolve(strict=True)
    destination = Path(destination).resolve()
    for root in (candidate, installed):
        if destination == root or root in destination.parents or destination in root.parents:
            raise ValueError("Staging must be separate from installed and candidate packages")
    files, version = verified(candidate)
    old_manifest = installed / "release_manifest.json"
    old_identity = hashlib.sha256(old_manifest.read_bytes()).hexdigest() if old_manifest.is_file() else None
    # Explicitly preserve local resources separately; never inject them into verified release files.
    local = {}
    for name in preserve:
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts or "\\" in name or ":" in name:
            raise ValueError("Unsafe local adapter path")
        path = installed / name
        path.resolve().relative_to(installed)
        if path.is_symlink(): raise ValueError("Linked local adapter refused")
        if path.is_file(): local[name] = path.read_bytes()
    destination.mkdir(parents=True, exist_ok=False)
    package = destination / "skill"; package.mkdir()
    for name, data in files.items():
        path = package / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
    for name, data in local.items():
        path = destination / "preserved-local" / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
    record = {"schema": "dft-skill-upgrade/v1", "old_manifest_identity": old_identity,
              "candidate_manifest_identity": version, "preserved_local": list(local),
              "status": "STAGED_NOT_ACTIVATED", "compatibility": "UNVERIFIED",
              "old_installation_modified": False, "running_tasks_migrated": False,
              "next_actions": ["Review installed local instructions and other custom files", "Verify environment and relevant tests against staged package", "Reapply local routing after review", "Switch discovery only after explicit approval; keep active jobs on original tools"]}
    (destination / "upgrade.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("candidate", "installed", "destination"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--preserve", action="append", help="Local file to preserve separately; repeat for multiple files")
    args = parser.parse_args()
    stage_upgrade(args.candidate, args.installed, args.destination, args.preserve or ("local-adapter.md",))

if __name__ == "__main__":
    main()
