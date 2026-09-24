"""scripts/check_config.py: exit code 0 for valid configuration, 1 with a clear error otherwise."""

import importlib.util
import logging
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

from sitescout.config import PROJECT_ROOT, SETTINGS_FILE, WEIGHTS_FILE, load_config

SCRIPT = PROJECT_ROOT / "scripts" / "check_config.py"


@pytest.fixture
def script(monkeypatch) -> Iterator[ModuleType]:
    """The script loaded as a module, run with no command-line arguments.

    The script reconfigures root logging, so the root logger is restored afterwards.
    """
    spec = importlib.util.spec_from_file_location("check_config_script", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(sys, "argv", [str(SCRIPT)])
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield module
    root.handlers[:] = handlers
    root.setLevel(level)


def _use_files(monkeypatch, module: ModuleType, settings_file: Path, weights_file: Path) -> None:
    """Make the script load the given files through the real load_config."""
    monkeypatch.setattr(
        module,
        "load_config",
        lambda: load_config(settings_file=settings_file, weights_file=weights_file),
    )


def test_valid_configuration_exits_0(script, capsys):
    assert script.main() == 0
    assert "Configuration is valid." in capsys.readouterr().err


@pytest.mark.parametrize(
    ("file", "valid_line", "invalid_line", "expected_error"),
    [
        ("settings", "  n_sites: 30\n", "  n_sites: -5\n", "n_sites"),
        ("settings", "  lambda: 0.01\n", '  lambda: "0.01"\n', "lambda"),
        ("weights", "    demand: 0.30\n", "    demand: 0.50\n", "must sum to 1"),
    ],
    ids=["negative-site-count", "quoted-number", "weights-not-summing-to-1"],
)
def test_invalid_configuration_exits_1_with_a_clear_error(
    script, capsys, monkeypatch, temp_dir, file, valid_line, invalid_line, expected_error
):
    source = SETTINGS_FILE if file == "settings" else WEIGHTS_FILE
    text = source.read_text(encoding="utf-8")
    assert text.count(valid_line) == 1
    broken = temp_dir / source.name
    broken.write_text(text.replace(valid_line, invalid_line), encoding="utf-8")
    settings_file = broken if file == "settings" else SETTINGS_FILE
    weights_file = broken if file == "weights" else WEIGHTS_FILE
    _use_files(monkeypatch, script, settings_file, weights_file)

    assert script.main() == 1
    errors = capsys.readouterr().err
    assert "ERROR" in errors
    assert f"Invalid {file}" in errors
    assert expected_error in errors
    assert "Configuration is valid." not in errors
