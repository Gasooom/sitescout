"""Download public source files into ``data/raw/<source_id>/`` with a manifest.

Each download records its URL, the URL it resolved to, size, SHA-256, HTTP headers, licence,
credit and a UTC retrieval time in ``source.json``. Fetching is idempotent: a file that is
already present and matches its manifest is not downloaded again. With ``refresh=True``:

- a **fixed** source (a URL naming one release) that returns different bytes raises
  SourceChangedError and the recorded file is kept;
- a **rolling** source (such as Geofabrik's ``rwanda-latest``) that changed is logged at
  WARNING level with both hashes, and the new file and manifest replace the old ones.
"""

from __future__ import annotations

import hashlib
import logging
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import IO, Any
from urllib.parse import urlsplit

from sitescout.config import Download, Settings
from sitescout.ingest import (
    DataValidationError,
    SourceChangedError,
    SourceError,
    SourceMissingError,
)
from sitescout.ingest.metadata import (
    MANIFEST_NAME,
    RawManifest,
    read_manifest,
    sha256_file,
    write_atomically,
    write_manifest,
)

logger = logging.getLogger(__name__)

USER_AGENT = "SiteScout-ingest/0.1 (independent public-data project; github.com/Gasooom/sitescout)"
TIMEOUT_S = 120
_CHUNK = 1 << 20

Opener = Callable[[urllib.request.Request], Any]


@dataclass(frozen=True, slots=True)
class RawSource:
    """One downloadable file, identified by a stable source id."""

    source_id: str
    download: Download

    @property
    def filename(self) -> str:
        name = PurePosixPath(urlsplit(self.download.url).path).name
        if not name:
            raise SourceError(f"{self.download.url} does not end with a file name")
        return name


def raw_sources(settings: Settings) -> dict[str, RawSource]:
    """Every downloadable source in settings.yaml, by source id."""
    sources = settings.sources
    entries = {
        "osm": sources.osm.download,
        "worldpop": sources.population.download,
        "boundaries_adm2": sources.boundaries.downloads.ADM2,
        "boundaries_adm1": sources.boundaries.downloads.ADM1,
        "grid_transmission": sources.grid_cross_check.download,
    }
    return {source_id: RawSource(source_id, download) for source_id, download in entries.items()}


def raw_path(source: RawSource, raw_dir: Path) -> Path:
    return raw_dir / source.source_id / source.filename


def manifest_path(source: RawSource, raw_dir: Path) -> Path:
    return raw_dir / source.source_id / MANIFEST_NAME


def load_raw(source: RawSource, raw_dir: Path) -> tuple[Path, RawManifest]:
    """The raw file and its manifest, after checking the file still matches the manifest."""
    path, manifest_file = raw_path(source, raw_dir), manifest_path(source, raw_dir)
    if not path.is_file() or not manifest_file.is_file():
        raise SourceMissingError(
            f"Raw source {source.source_id!r} is not downloaded ({path}); run the fetch step"
        )
    manifest = read_manifest(manifest_file)
    problems = []
    if manifest.url != source.download.url:
        problems.append(f"manifest URL {manifest.url} differs from settings {source.download.url}")
    actual = sha256_file(path)
    if actual != manifest.sha256:
        problems.append(f"file SHA-256 {actual} differs from the manifest {manifest.sha256}")
    if problems:
        raise DataValidationError(f"Raw source {source.source_id!r}", problems)
    return path, manifest


def fetch(
    source: RawSource,
    raw_dir: Path,
    *,
    refresh: bool = False,
    opener: Opener | None = None,
    now: Callable[[], datetime] | None = None,
) -> RawManifest:
    """Download ``source`` unless it is already present; return its manifest."""
    path, manifest_file = raw_path(source, raw_dir), manifest_path(source, raw_dir)
    previous: RawManifest | None = None
    if path.is_file() and manifest_file.is_file():
        _, previous = load_raw(source, raw_dir)
        if not refresh:
            logger.info(
                "%s: already retrieved %s (%s, sha256 %s)",
                source.source_id,
                previous.retrieved_at,
                _size(previous.bytes),
                previous.sha256[:12],
            )
            return previous
    elif path.exists() or manifest_file.exists():
        raise SourceError(
            f"{source.source_id}: {path.parent} holds a file without its manifest (or the "
            "reverse); delete the directory and fetch again"
        )

    logger.info("%s: downloading %s", source.source_id, source.download.url)
    request = urllib.request.Request(source.download.url, headers={"User-Agent": USER_AGENT})
    open_url = opener or (lambda req: urllib.request.urlopen(req, timeout=TIMEOUT_S))
    retrieved_at = (now or (lambda: datetime.now(UTC)))()
    result: dict[str, Any] = {}

    def download_to(temp: Path) -> None:
        try:
            response = open_url(request)
        except urllib.error.HTTPError as error:
            raise SourceError(
                f"{source.source_id}: HTTP {error.code} {error.reason} for {source.download.url}"
            ) from error
        except (urllib.error.URLError, OSError) as error:
            raise SourceError(
                f"{source.source_id}: cannot reach {source.download.url}: {error}"
            ) from error
        with response:
            headers = response.headers
            size, digest = _copy(response, temp)
            expected = headers.get("Content-Length")
            if expected is not None and int(expected) != size:
                raise SourceError(
                    f"{source.source_id}: received {size} bytes, server announced {expected}"
                )
            if size == 0:
                raise SourceError(f"{source.source_id}: the server returned an empty file")
            result.update(
                resolved_url=response.geturl(),
                bytes=size,
                sha256=digest,
                http_last_modified=headers.get("Last-Modified"),
                http_etag=headers.get("ETag"),
            )
        if previous is not None and digest != previous.sha256:
            if source.download.versioning == "fixed":
                raise SourceChangedError(
                    f"{source.source_id}: {source.download.url} is a fixed release but now "
                    f"returns different bytes (sha256 {digest[:12]}, recorded "
                    f"{previous.sha256[:12]}). The recorded file is kept; investigate before "
                    "accepting the change."
                )
            logger.warning(
                "%s: rolling source changed upstream: sha256 %s -> %s (%s)",
                source.source_id,
                previous.sha256[:12],
                digest[:12],
                result["resolved_url"],
            )

    write_atomically(path, download_to)
    manifest = RawManifest(
        source_id=source.source_id,
        url=source.download.url,
        filename=source.filename,
        retrieved_at=retrieved_at.astimezone(UTC).replace(microsecond=0).isoformat(),
        versioning=source.download.versioning,
        licence=source.download.licence,
        licence_url=source.download.licence_url,
        credit=source.download.credit,
        **result,
    )
    write_manifest(manifest_file, manifest)
    logger.info(
        "%s: retrieved %s, sha256 %s, from %s",
        source.source_id,
        _size(manifest.bytes),
        manifest.sha256[:12],
        manifest.resolved_url,
    )
    return manifest


def _copy(response: IO[bytes], target: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with target.open("wb") as handle:
        while chunk := response.read(_CHUNK):
            handle.write(chunk)
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()


def _size(n: int) -> str:
    return f"{n / 1_048_576:.1f} MB" if n >= 1_048_576 else f"{n / 1024:.1f} KB"
