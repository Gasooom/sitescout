"""Source metadata: raw-file manifests, processed-layer metadata and content fingerprints.

Two kinds of information are kept apart:

- **Retrieval metadata** (``retrieved_at``, HTTP headers) describes when and how a file was
  downloaded. It lives in the raw manifest and is copied, unchanged, into layer metadata.
- **Dataset content** is everything derived from the bytes of the raw files. It never
  depends on the clock, so processing the same raw files twice gives identical outputs,
  identical fingerprints and identical metadata files.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, Literal

import geopandas as gpd
import pandas as pd
import shapely
from pydantic import BaseModel, ConfigDict, ValidationError

from sitescout.ingest import DataValidationError, SourceMissingError

MANIFEST_NAME = "source.json"
_CHUNK = 1 << 20


class RawManifest(BaseModel):
    """What was downloaded, from where, when, and under which licence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str
    url: str
    resolved_url: str
    filename: str
    bytes: int
    sha256: str
    retrieved_at: str  # UTC, ISO 8601: retrieval metadata, never dataset content
    http_last_modified: str | None
    http_etag: str | None
    versioning: Literal["fixed", "rolling"]
    licence: str
    licence_url: str
    credit: str


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, data: Any) -> None:
    """Write JSON deterministically (sorted keys, LF endings) and atomically."""
    text = json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"
    write_atomically(path, lambda temp: temp.write_bytes(text.encode("utf-8")))


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise SourceMissingError(f"{path} does not exist") from error
    except json.JSONDecodeError as error:
        raise DataValidationError(str(path), [f"invalid JSON: {error}"]) from error


def write_atomically(path: Path, write: Callable[[Path], object]) -> None:
    """Call ``write`` on a temporary file next to ``path``, then move it into place.

    A crash or a validation error never leaves a half-written file at ``path``.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    os.close(handle)
    temp = Path(name)
    try:
        write(temp)
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def read_manifest(path: Path) -> RawManifest:
    try:
        return RawManifest.model_validate(read_json(path))
    except ValidationError as error:
        raise DataValidationError(str(path), [str(error)]) from error


def write_manifest(path: Path, manifest: RawManifest) -> None:
    write_json(path, manifest.model_dump(mode="json"))


def source_record(manifest: RawManifest) -> dict[str, Any]:
    """The retrieval facts a processed layer records about one of its raw sources."""
    return manifest.model_dump(mode="json")


# --- Content fingerprints ----------------------------------------------------------------


def _canonical_value(value: object) -> object:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    if value is pd.NA or value is pd.NaT:
        return None
    if hasattr(value, "item"):  # numpy scalar
        return _canonical_value(value.item())  # type: ignore[attr-defined]
    return value


def content_fingerprint(frame: gpd.GeoDataFrame, columns: Iterable[str]) -> str:
    """SHA-256 of a layer's content: the listed columns in row order, then geometry as WKB.

    Independent of the file format and writer, so it can confirm that two runs produced the
    same data even if a library changes how it writes Parquet.
    """
    digest = hashlib.sha256()
    for name in columns:
        values = [_canonical_value(value) for value in frame[name].tolist()]
        digest.update(json.dumps([name, values], ensure_ascii=False).encode("utf-8"))
    wkb = shapely.to_wkb(frame.geometry.array, output_dimension=2, byte_order=1, flavor="iso")
    for item in wkb:
        digest.update(b"\x00" if item is None else len(item).to_bytes(8, "big") + item)
    return digest.hexdigest()
