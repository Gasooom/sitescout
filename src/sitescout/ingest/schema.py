"""Declared schemas for processed layers, and the table checks every layer must pass.

A schema lists every column with its type, whether it may be null and what it means. The
checks never convert or repair data: a wrong type, a missing or unexpected column, a null
in a required column, a duplicate identifier or an unexpectedly empty table is an error.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

from sitescout.ingest import DataValidationError

ColumnKind = Literal["string", "int64", "float64", "bool"]
GEOMETRY_COLUMN = "geometry"
_MAX_EXAMPLES = 5


@dataclass(frozen=True, slots=True)
class Column:
    name: str
    kind: ColumnKind
    required: bool
    description: str


@dataclass(frozen=True, slots=True)
class LayerSchema:
    """The contract for one processed vector layer (GeoParquet, storage CRS)."""

    name: str
    description: str
    id_column: str
    columns: tuple[Column, ...]
    geometry_types: frozenset[str]
    sort_by: tuple[str, ...]
    allow_empty: bool = False
    # "within": every geometry lies inside the Rwanda envelope.
    # "intersects": geometries may cross it (OSM extracts keep ways that cross the border).
    bounds_mode: Literal["within", "intersects"] = "within"

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(column.name for column in self.columns)

    def column(self, name: str) -> Column:
        for column in self.columns:
            if column.name == name:
                return column
        raise KeyError(name)


def _kind_matches(series: pd.Series, kind: ColumnKind) -> bool:
    dtype = series.dtype
    if kind == "bool":
        return pd.api.types.is_bool_dtype(dtype)
    if kind == "int64":
        return pd.api.types.is_integer_dtype(dtype) and not pd.api.types.is_bool_dtype(dtype)
    if kind == "float64":
        return pd.api.types.is_float_dtype(dtype)
    # string: a string dtype, or an object column whose non-null values are all str
    if isinstance(dtype, pd.StringDtype):
        return True
    if pd.api.types.is_object_dtype(dtype):
        return all(isinstance(value, str) for value in series.dropna())
    return False


def check_table(frame: pd.DataFrame, schema: LayerSchema) -> list[str]:
    """Every schema problem in ``frame``, as readable sentences; empty when it conforms."""
    problems: list[str] = []
    expected = [*schema.column_names, GEOMETRY_COLUMN]
    missing = [name for name in expected if name not in frame.columns]
    unexpected = [name for name in frame.columns if name not in expected]
    if missing:
        problems.append(f"missing columns: {missing}")
    if unexpected:
        problems.append(f"unexpected columns: {unexpected}")
    if len(frame) == 0 and not schema.allow_empty:
        problems.append("the layer is empty")

    for column in schema.columns:
        if column.name not in frame.columns:
            continue
        series = frame[column.name]
        if not _kind_matches(series, column.kind):
            problems.append(
                f"column {column.name!r} has type {series.dtype}, expected {column.kind}"
            )
        nulls = int(series.isna().sum())
        if column.required and nulls:
            problems.append(f"required column {column.name!r} has {nulls} null value(s)")

    if schema.id_column in frame.columns:
        ids = frame[schema.id_column]
        duplicated = ids[ids.duplicated(keep=False)].dropna().unique().tolist()
        if duplicated:
            examples = sorted(map(str, duplicated))[:_MAX_EXAMPLES]
            problems.append(
                f"{len(duplicated)} duplicate {schema.id_column!r} value(s), e.g. {examples}"
            )
    return problems


def validate_table(frame: pd.DataFrame, schema: LayerSchema) -> None:
    """Raise DataValidationError listing every schema problem in ``frame``."""
    problems = check_table(frame, schema)
    if problems:
        raise DataValidationError(f"Layer {schema.name!r}", problems)
