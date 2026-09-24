"""OpenStreetMap layers from the Geofabrik Rwanda extract, read with pyosmium (D-001).

Layers and the tags that select them come from ``sources.osm_tags`` in settings.yaml:

- ``osm_roads``: ways tagged ``highway=*`` as lines, every value kept (M2 picks classes);
- ``osm_pois``: nodes and areas matching any ``pois`` tag: a superset, not host types;
- ``osm_charging_stations``: ``amenity=charging_station`` nodes and areas;
- ``osm_power``: ``power=*`` nodes, lines and areas;
- ``osm_water``: ``natural=water`` areas; ``osm_protected_areas``: ``boundary=protected_area``.

The file is read in two filtered passes (keys with any value, then exact key=value pairs),
which keeps the 1.4 million untagged buildings out of Python. Name, brand and operator tags
are never read: outputs use generic labels plus OSM ids (D-012). Features the extract cannot
turn into geometry, and features wholly outside the Rwanda envelope, are counted and logged,
never dropped silently; invalid areas are made valid explicitly and flagged per row (D-020).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import geopandas as gpd
import numpy as np
import osmium
import shapely

from sitescout.config import Settings
from sitescout.ingest import DataValidationError
from sitescout.ingest.acquire import load_raw, raw_sources
from sitescout.ingest.geometry import inside_bbox, repair_polygons
from sitescout.ingest.layers import (
    OSM_CHARGING_STATIONS,
    OSM_POIS,
    OSM_POWER,
    OSM_PROTECTED_AREAS,
    OSM_ROADS,
    OSM_WATER,
    LayerResult,
    LayerSchema,
    empty_frame,
    write_layer,
)

logger = logging.getLogger(__name__)

# OSM convention: power=line, minor_line and cable ways are linear even when closed; other
# closed power ways (substation, plant, generator, ...) are areas.
LINEAR_POWER_VALUES = frozenset({"line", "minor_line", "cable"})
# Relation types libosmium assembles into areas; others (e.g. route=power) are not areas.
AREA_RELATION_TYPES = frozenset({"multipolygon", "boundary"})


@dataclass(frozen=True, slots=True)
class TagSpec:
    """One tag selector from settings.yaml: ``key=value``, ``key=*`` or ``prefix:*``."""

    key: str
    value: str | None = None  # None means any value
    prefix: bool = False

    @classmethod
    def parse(cls, text: str) -> TagSpec:
        if "=" in text:
            key, value = text.split("=", 1)
            return cls(key, None if value == "*" else value)
        if text.endswith(":*"):
            return cls(text[:-1], prefix=True)
        raise ValueError(f"Unsupported OSM tag selector {text!r}")

    def matches(self, tags: Any) -> bool:
        if self.prefix:
            return any(key.startswith(self.key) for key, _ in tags)
        if self.key not in tags:
            return False
        return self.value is None or tags[self.key] == self.value


@dataclass(frozen=True, slots=True)
class OsmLayer:
    schema: LayerSchema
    specs: tuple[TagSpec, ...]
    tag_columns: tuple[str, ...]
    nodes: bool
    ways: Literal["lines", "areas", "power"]  # roads: every way is a line

    def matches(self, tags: Any) -> bool:
        return any(spec.matches(tags) for spec in self.specs)


@dataclass
class _Collected:
    rows: dict[str, dict[str, Any]] = field(default_factory=dict)
    expected_areas: set[str] = field(default_factory=set)
    produced_areas: set[str] = field(default_factory=set)
    skipped_open_ways: int = 0
    skipped_relations: int = 0
    geometry_errors: list[str] = field(default_factory=list)


def osm_layers(settings: Settings) -> list[OsmLayer]:
    tags = settings.sources.osm_tags
    return [
        OsmLayer(
            OSM_ROADS,
            (TagSpec.parse(tags.roads),),
            ("highway", "ref", "surface", "access", "motor_vehicle"),
            nodes=False,
            ways="lines",
        ),
        OsmLayer(
            OSM_POIS,
            tuple(TagSpec.parse(text) for text in tags.pois),
            (
                "amenity",
                "shop",
                "tourism",
                "office",
                "industrial",
                "landuse",
                "building",
                "man_made",
            ),
            nodes=True,
            ways="areas",
        ),
        OsmLayer(
            OSM_CHARGING_STATIONS,
            (TagSpec.parse(tags.charging_station),),
            ("capacity", "access"),
            nodes=True,
            ways="areas",
        ),
        OsmLayer(
            OSM_POWER,
            (TagSpec.parse(tags.grid),),
            ("power", "voltage", "substation"),
            nodes=True,
            ways="power",
        ),
        OsmLayer(OSM_WATER, (TagSpec.parse(tags.water),), ("water",), nodes=False, ways="areas"),
        OsmLayer(
            OSM_PROTECTED_AREAS,
            (TagSpec.parse(tags.protected_area),),
            ("protect_class",),
            nodes=False,
            ways="areas",
        ),
    ]


def _passes(layers: list[OsmLayer]) -> list[osmium.filter.KeyFilter | osmium.filter.TagFilter]:
    """One C++ filter for 'any value' keys and one for exact pairs; OR-ed by two passes."""
    keys = sorted({spec.key for layer in layers for spec in layer.specs if spec.value is None})
    pairs = sorted(
        {(spec.key, spec.value) for layer in layers for spec in layer.specs if spec.value}
    )
    filters: list[osmium.filter.KeyFilter | osmium.filter.TagFilter] = []
    if keys:
        filters.append(osmium.filter.KeyFilter(*keys))
    if pairs:
        filters.append(osmium.filter.TagFilter(*pairs))
    return filters


def _row(
    layer: OsmLayer, osm_type: str, osm_id: int, tags: Any, sockets: TagSpec
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "feature_id": f"{osm_type}/{osm_id}",
        "osm_type": osm_type,
        "osm_id": osm_id,
    }
    for key in layer.tag_columns:
        row[key] = tags.get(key)
    if "socket_tags" in layer.schema.column_names:
        found = {key: value for key, value in tags if key.startswith(sockets.key)}
        row["socket_tags"] = json.dumps(found, sort_keys=True) if found else None
    return row


def extract(pbf: Path, settings: Settings) -> tuple[dict[str, gpd.GeoDataFrame], dict[str, Any]]:
    """Read the PBF into one GeoDataFrame per layer, plus extraction statistics."""
    layers = osm_layers(settings)
    sockets = TagSpec.parse(settings.sources.osm_tags.fuel_station_sockets)
    collected = {layer.schema.name: _Collected() for layer in layers}
    wkb = osmium.geom.WKBFactory()

    for osm_filter in _passes(layers):
        processor = osmium.FileProcessor(str(pbf)).with_locations()
        processor = processor.with_areas(osm_filter).with_filter(osm_filter)
        for obj in processor:
            kind = obj.type_str()
            matching = [layer for layer in layers if layer.matches(obj.tags)]
            if not matching:
                continue
            for layer in matching:
                target = collected[layer.schema.name]
                if kind == "n":
                    if layer.nodes:
                        _add(
                            target, layer, "node", obj.id, obj.tags, sockets, wkb.create_point, obj
                        )
                elif kind == "w":
                    _way(target, layer, obj, sockets, wkb)
                elif kind == "r":
                    if layer.ways == "lines":
                        continue
                    if obj.tags.get("type") in AREA_RELATION_TYPES:
                        target.expected_areas.add(f"relation/{obj.id}")
                    else:
                        target.skipped_relations += 1
                elif kind == "a":
                    _area(target, layer, obj, sockets, wkb)

    frames = {}
    stats: dict[str, Any] = {}
    for layer in layers:
        frame, layer_stats = _to_frame(layer, collected[layer.schema.name], settings)
        frames[layer.schema.name] = frame
        stats[layer.schema.name] = layer_stats
    return frames, stats


def _add(
    target: _Collected,
    layer: OsmLayer,
    osm_type: str,
    osm_id: int,
    tags: Any,
    sockets: TagSpec,
    build: Any,
    obj: Any,
) -> None:
    feature_id = f"{osm_type}/{osm_id}"
    if feature_id in target.rows:  # seen in the other pass
        return
    try:
        geometry = build(obj)
    except (RuntimeError, osmium.InvalidLocationError) as error:
        target.geometry_errors.append(f"{feature_id}: {error}")
        return
    row = _row(layer, osm_type, osm_id, tags, sockets)
    row["_wkb"] = geometry
    target.rows[feature_id] = row


def _way(target: _Collected, layer: OsmLayer, way: Any, sockets: TagSpec, wkb: Any) -> None:
    closed = way.is_closed()
    linear_power = layer.ways == "power" and way.tags.get("power") in LINEAR_POWER_VALUES
    if layer.ways == "lines" or (layer.ways == "power" and (linear_power or not closed)):
        _add(target, layer, "way", way.id, way.tags, sockets, wkb.create_linestring, way)
    elif closed:
        target.expected_areas.add(f"way/{way.id}")
    else:
        target.skipped_open_ways += 1


def _area(target: _Collected, layer: OsmLayer, area: Any, sockets: TagSpec, wkb: Any) -> None:
    if layer.ways == "lines":
        return
    osm_type = "way" if area.from_way() else "relation"
    if (
        layer.ways == "power"
        and osm_type == "way"
        and area.tags.get("power") in LINEAR_POWER_VALUES
    ):
        return  # taken as a line
    osm_id = area.orig_id()
    target.produced_areas.add(f"{osm_type}/{osm_id}")
    _add(target, layer, osm_type, osm_id, area.tags, sockets, wkb.create_multipolygon, area)


def _to_frame(
    layer: OsmLayer, collected: _Collected, settings: Settings
) -> tuple[gpd.GeoDataFrame, dict[str, Any]]:
    name = layer.schema.name
    rows = list(collected.rows.values())
    geoms = shapely.from_wkb([row.pop("_wkb") for row in rows])
    frame = gpd.GeoDataFrame(rows, geometry=geoms, crs=settings.crs.storage)
    if frame.empty:
        frame = empty_frame(layer.schema, settings.crs.storage)

    stats: dict[str, Any] = {}
    missing_areas = sorted(collected.expected_areas - collected.produced_areas)
    if missing_areas:
        stats["areas_not_assembled"] = len(missing_areas)
        logger.warning(
            "%s: %d closed ways/relations could not be assembled into areas and are not in the "
            "layer, e.g. %s",
            name,
            len(missing_areas),
            missing_areas[:5],
        )
    if collected.skipped_open_ways:
        stats["open_ways_skipped"] = collected.skipped_open_ways
        logger.info(
            "%s: %d open (non-closed) ways skipped: this layer takes nodes and areas only",
            name,
            collected.skipped_open_ways,
        )
    if collected.skipped_relations:
        stats["non_area_relations_skipped"] = collected.skipped_relations
        logger.info(
            "%s: %d relations that are not multipolygons or boundaries skipped",
            name,
            collected.skipped_relations,
        )
    if collected.geometry_errors:
        stats["geometry_errors"] = len(collected.geometry_errors)
        logger.warning(
            "%s: %d features have no buildable geometry (missing node locations or an invalid "
            "ring), e.g. %s",
            name,
            len(collected.geometry_errors),
            collected.geometry_errors[:5],
        )

    if "geometry_repaired" in layer.schema.column_names:
        repaired_geoms, repaired = repair_polygons(frame.geometry.array)
        polygonal = np.isin(frame.geom_type.to_numpy(), ["Polygon", "MultiPolygon"])
        repaired &= polygonal
        frame = frame.set_geometry(
            gpd.GeoSeries(
                np.where(polygonal, repaired_geoms, frame.geometry.array),
                crs=settings.crs.storage,
                index=frame.index,
            )
        )
        frame["geometry_repaired"] = repaired
        unrepairable = frame.geometry.isna()
        if repaired.any():
            stats["geometry_repaired"] = int(repaired.sum())
            logger.warning(
                "%s: %d invalid OSM areas made valid (flagged geometry_repaired=True), e.g. %s",
                name,
                int(repaired.sum()),
                frame.loc[repaired, "feature_id"].head(5).tolist(),
            )
        if unrepairable.any():
            stats["dropped_unrepairable"] = int(unrepairable.sum())
            logger.warning(
                "%s: %d areas had no polygonal part after repair and were dropped: %s",
                name,
                int(unrepairable.sum()),
                frame.loc[unrepairable, "feature_id"].head(5).tolist(),
            )
            frame = frame.loc[~unrepairable].copy()

    degenerate = ~frame.geometry.is_valid & frame.geom_type.isin(["LineString", "Point"])
    if degenerate.any():
        stats["dropped_degenerate"] = int(degenerate.sum())
        logger.warning(
            "%s: %d degenerate lines/points dropped: %s",
            name,
            int(degenerate.sum()),
            frame.loc[degenerate, "feature_id"].head(5).tolist(),
        )
        frame = frame.loc[~degenerate].copy()

    if len(frame):
        # Study-area scope (D-020): the extract also holds features wholly outside Rwanda
        # (nodes of cross-border ways, members of cross-border relations). They are dropped
        # and counted; features that cross the envelope are kept whole, never clipped.
        bounds = np.asarray(shapely.bounds(frame.geometry.array))
        bbox = settings.ingest.rwanda_bbox
        touching = inside_bbox(bounds, bbox, mode="intersects")
        if not touching.all():
            stats["outside_envelope_dropped"] = int((~touching).sum())
            logger.info(
                "%s: %d features wholly outside the Rwanda envelope dropped, e.g. %s",
                name,
                int((~touching).sum()),
                frame.loc[~touching, "feature_id"].head(5).tolist(),
            )
            frame = frame.loc[touching].copy()
            bounds = bounds[touching]
        within = inside_bbox(bounds, bbox, mode="within")
        stats["features_crossing_envelope"] = int((~within).sum())
    stats["features"] = len(frame)
    stats["by_osm_type"] = {
        str(key): int(value) for key, value in sorted(frame["osm_type"].value_counts().items())
    }
    return frame.reset_index(drop=True), stats


def check_pbf_signature(pbf: Path) -> None:
    """Refuse a file that does not start like an OSM PBF, before osmium opens it.

    A PBF starts with a 4-byte big-endian BlobHeader length followed by a BlobHeader of
    type "OSMHeader". Checking this gives a clear error for empty, truncated-to-nothing or
    non-PBF files (and avoids libosmium keeping such a file open on Windows).
    """
    with pbf.open("rb") as handle:
        start = handle.read(64)
    size = int.from_bytes(start[:4], "big") if len(start) >= 4 else 0
    if len(start) < 8 or not 0 < size <= 64 * 1024 or b"OSMHeader" not in start[4 : 4 + size]:
        raise DataValidationError(
            f"OSM extract ({pbf.name})", ["the file is not an OSM PBF (no OSMHeader block)"]
        )


def osm_timestamp(pbf: Path) -> str | None:
    """The extract's replication timestamp from the PBF header (data up to this time)."""
    reader = osmium.io.Reader(str(pbf), osmium.osm.osm_entity_bits.NOTHING)
    try:
        header = reader.header()
        return header.get("osmosis_replication_timestamp") or None
    finally:
        reader.close()


def process_osm(settings: Settings, raw_dir: Path, processed_dir: Path) -> list[LayerResult]:
    """Extract every OSM layer from the Geofabrik PBF and write them as GeoParquet."""
    path, manifest = load_raw(raw_sources(settings)["osm"], raw_dir)
    what = f"OSM extract ({path.name})"
    check_pbf_signature(path)
    try:
        timestamp = osm_timestamp(path)
        if timestamp is None:
            raise DataValidationError(what, ["the PBF header has no replication timestamp"])
        logger.info("OSM extract %s: data up to %s", path.name, timestamp)
        frames, stats = extract(path, settings)
    except RuntimeError as error:  # libosmium reports unreadable data as RuntimeError
        raise DataValidationError(what, [f"cannot be read: {error}"]) from error
    results = []
    for name, frame in frames.items():
        layer_stats = {"osm_data_timestamp": timestamp, **stats[name]}
        schema = next(layer.schema for layer in osm_layers(settings) if layer.schema.name == name)
        results.append(
            write_layer(
                frame,
                schema,
                processed_dir,
                settings,
                sources=[manifest],
                stats=layer_stats,
                notes=[
                    "OpenStreetMap data, © OpenStreetMap contributors, ODbL 1.0.",
                    "Name, brand and operator tags are not extracted (D-012).",
                ],
            )
        )
    return results
