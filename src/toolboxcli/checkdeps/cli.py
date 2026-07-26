"""
check-deps — Scan subdirectories for specific npm package versions.

Iterates over every immediate subdirectory of <root> and, for each one that
contains a package-lock.json, reports whether any of the configured packages
(optionally at specific flagged versions) are present. The lockfile is parsed
directly as JSON, so no `npm install` / node_modules is required.

Usage:
    check-deps [options] [root]
    check-deps -p axios@1.14.1,0.30.4 -p plain-crypto-js
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from toolboxcli._common.console import console, warn

DEFAULT_CHECKS: list[tuple[str, list[str] | None]] = [
    ("axios", ["1.14.1", "0.30.4"]),
    ("plain-crypto-js", None),
]


def _iter_lockfile_versions(lockfile: dict, name: str):
    packages = lockfile.get("packages")
    if isinstance(packages, dict):
        for pkg_path, meta in packages.items():
            if pkg_path == f"node_modules/{name}" or pkg_path.endswith(f"/node_modules/{name}"):
                version = meta.get("version")
                if version:
                    yield version
        return

    def walk(deps: dict):
        for pkg_name, meta in deps.items():
            if pkg_name == name and meta.get("version"):
                yield meta["version"]
            nested = meta.get("dependencies")
            if isinstance(nested, dict):
                yield from walk(nested)

    deps = lockfile.get("dependencies")
    if isinstance(deps, dict):
        yield from walk(deps)


def check_directory(directory: Path, checks: list[tuple[str, list[str] | None]]) -> None:
    console.rule(f"[bold]{directory}[/bold]", style="dim")

    lockfile_path = directory / "package-lock.json"
    if not lockfile_path.is_file():
        return

    try:
        lockfile = json.loads(lockfile_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        warn(f"could not read package-lock.json: {exc}")
        return

    for name, flagged_versions in checks:
        versions = set(_iter_lockfile_versions(lockfile, name))
        if not versions:
            continue
        hits = versions if flagged_versions is None else versions & set(flagged_versions)
        for v in sorted(hits):
            warn(f"{name}@{v}")


def parse_package_arg(raw: str) -> tuple[str, list[str] | None]:
    if "@" in raw:
        name, versions = raw.split("@", 1)
        return name, [v.strip() for v in versions.split(",") if v.strip()]
    return raw, None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="check-deps",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "root", nargs="?", default=".", help="Directory whose immediate subdirectories will be scanned (default: .)"
    )
    parser.add_argument(
        "-p",
        "--package",
        dest="packages",
        action="append",
        metavar="NAME[@VER1,VER2,...]",
        help="Check for a specific package, optionally at specific versions (omit versions to flag any presence). "
        "Repeatable. Overrides the built-in default check list (axios@1.14.1,0.30.4 and plain-crypto-js).",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    checks = [parse_package_arg(p) for p in args.packages] if args.packages else DEFAULT_CHECKS

    root = Path(args.root)
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        check_directory(d, checks)


if __name__ == "__main__":
    main()
