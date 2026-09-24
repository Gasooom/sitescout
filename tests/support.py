"""Helpers for editing and comparing parsed YAML documents in tests."""

from typing import Any


def set_path(data: dict[str, Any], dotted: str, value: Any) -> None:
    """Set a nested key, e.g. ``set_path(data, "optimization.n_sites", 5)``."""
    *parents, leaf = dotted.split(".")
    node = data
    for key in parents:
        node = node[key]
    node[leaf] = value


def delete_path(data: dict[str, Any], dotted: str) -> None:
    """Delete a nested key, e.g. ``delete_path(data, "optimization.n_sites")``."""
    *parents, leaf = dotted.split(".")
    node = data
    for key in parents:
        node = node[key]
    del node[leaf]


def flatten(data: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """Flatten nested mappings to dotted keys, leaving out pending parameters."""
    flat: dict[str, Any] = {}
    for key, value in data.items():
        dotted = f"{prefix}{key}"
        if isinstance(value, dict):
            if set(value) != {"pending"}:
                flat.update(flatten(value, f"{dotted}."))
        else:
            flat[dotted] = value
    return flat
