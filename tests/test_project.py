"""Project-wide checks: every command starts, and the three lists of scripts agree.

CLAUDE.md asks for pyproject.toml's [project.scripts], toolbox's SCRIPTS list and the
README's scripts table to be kept in sync by hand; this is what catches it when they drift.
"""

from __future__ import annotations

import importlib
import re
import sys
import tomllib
from pathlib import Path

import pytest

from toolboxcli.toolbox.cli import SCRIPTS

ROOT = Path(__file__).resolve().parent.parent
PROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
ENTRY_POINTS: dict[str, str] = PROJECT["project"]["scripts"]


def _readme_scripts() -> list[str]:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    table = readme.split("## Scripts", 1)[1].split("\n\n", 2)[1]
    return re.findall(r"^\| `([^`]+)` \|", table, re.MULTILINE)


def test_toolbox_lists_every_script():
    assert [name for name, _ in SCRIPTS] == sorted(ENTRY_POINTS)


def test_readme_table_lists_every_script():
    assert _readme_scripts() == sorted(ENTRY_POINTS)


@pytest.mark.parametrize("command", sorted(ENTRY_POINTS))
def test_help_works(command, monkeypatch, capsys):
    module_name, func_name = ENTRY_POINTS[command].split(":")
    main = getattr(importlib.import_module(module_name), func_name)
    monkeypatch.setattr(sys, "argv", [command, "--help"])
    with pytest.raises(SystemExit) as exit_info:
        main()
    assert exit_info.value.code == 0
    out = capsys.readouterr().out
    assert out.startswith(f"usage: {command}")
