"""Load and validate SiteScout's configuration.

Settings (``config/settings.yaml``) and weights (``config/weights.yaml``) come from YAML
files only: environment variables and ``.env`` files are never read. Every model rejects
unknown keys and is immutable once loaded. A parameter that ``docs/SPEC.md`` requires but
does not define is written as ``{pending: <reason>}``: it has no default, ``require`` raises
``PendingParameterError`` for it, and overrides can neither fill nor create one.
"""

from __future__ import annotations

import copy
import logging
import math
from collections.abc import Hashable, Iterator, Mapping
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Annotated, Any, Literal, Self

import yaml
from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    Strict,
    ValidationError,
    model_validator,
)

logger = logging.getLogger(__name__)


class ConfigError(Exception):
    """The configuration files are missing, malformed or invalid."""


class PendingParameterError(ConfigError):
    """A parameter that docs/SPEC.md leaves undefined was used before it was decided."""


def find_project_root(start: Path) -> Path:
    """Return the nearest directory at or above ``start`` that contains pyproject.toml."""
    for directory in (start, *start.parents):
        if (directory / "pyproject.toml").is_file():
            return directory
    raise ConfigError(f"No pyproject.toml found at or above {start}")


PROJECT_ROOT = find_project_root(Path(__file__).resolve().parent)
SETTINGS_FILE = PROJECT_ROOT / "config" / "settings.yaml"
WEIGHTS_FILE = PROJECT_ROOT / "config" / "weights.yaml"


# --- YAML -------------------------------------------------------------------------------


class _UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader that rejects duplicate keys; plain PyYAML silently keeps the last one."""


def _construct_mapping(loader: _UniqueKeyLoader, node: yaml.MappingNode) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        line = key_node.start_mark.line + 1
        if not isinstance(key, Hashable):
            raise ConfigError(f"Unsupported key at line {line}")
        if key in mapping:
            raise ConfigError(f"Duplicate key {key!r} at line {line}")
        mapping[key] = loader.construct_object(value_node, deep=True)
    return mapping


_UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


def read_yaml(path: Path) -> dict[str, Any]:
    """Read a YAML file whose top level is a mapping, rejecting duplicate keys."""
    try:
        with path.open(encoding="utf-8") as handle:
            data = yaml.load(handle, Loader=_UniqueKeyLoader)  # a SafeLoader subclass
    except OSError as error:
        raise ConfigError(f"Cannot read {path}: {error}") from error
    except yaml.YAMLError as error:
        raise ConfigError(f"Invalid YAML in {path}: {error}") from error
    except ConfigError as error:
        raise ConfigError(f"{path}: {error}") from error
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a mapping at the top level")
    return data


# --- Field types ------------------------------------------------------------------------
# Leaf values are strict: YAML "30" or true never turns into a number. Lists become tuples.


def _reject_non_numbers(value: object) -> object:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("must be a number")
    return value


def _check_relative_path(value: str) -> str:
    if "\\" in value:
        raise ValueError("use forward slashes")
    if PurePosixPath(value).is_absolute() or PureWindowsPath(value).drive:
        raise ValueError("must be relative to the repository root")
    if ".." in PurePosixPath(value).parts:
        raise ValueError("must stay inside the repository")
    return value


def _no_duplicates[T](values: tuple[T, ...]) -> tuple[T, ...]:
    if len(set(values)) != len(values):
        raise ValueError("must not contain duplicates")
    return values


def _strictly_increasing(values: tuple[int, ...]) -> tuple[int, ...]:
    if any(earlier >= later for earlier, later in pairwise(values)):
        raise ValueError("must be strictly increasing")
    return values


Text = Annotated[str, Strict(), Field(min_length=1)]
Flag = Annotated[bool, Strict()]
Count = Annotated[int, Strict(), Field(gt=0)]
Metres = Annotated[int, Strict(), Field(gt=0)]
Share = Annotated[float, BeforeValidator(_reject_non_numbers), Field(ge=0, le=1)]
OpenFraction = Annotated[float, BeforeValidator(_reject_non_numbers), Field(gt=0, lt=1)]
BonusPoints = Annotated[int, Strict(), Field(ge=0, le=100)]
RelativePath = Annotated[str, Strict(), Field(min_length=1), AfterValidator(_check_relative_path)]
EpsgCode = Annotated[str, Strict(), Field(pattern=r"^EPSG:\d+$")]
HttpsUrl = Annotated[str, Strict(), Field(pattern=r"^https://[^\s]+$")]
OsmTag = Annotated[str, Strict(), Field(pattern=r"^[a-z_]+(:[a-z_]+)*(=([a-z_]+|\*)|:\*)$")]
OsmValue = Annotated[str, Strict(), Field(pattern=r"^[a-z_]+$")]
Texts = Annotated[tuple[Text, ...], Field(min_length=1), AfterValidator(_no_duplicates)]
IncreasingMetres = Annotated[
    tuple[Metres, ...], Field(min_length=1), AfterValidator(_strictly_increasing)
]
IncreasingCounts = Annotated[
    tuple[Count, ...], Field(min_length=1), AfterValidator(_strictly_increasing)
]

HostType = Literal["fuel", "mall", "supermarket", "logistics", "industrial", "hotel"]
HostTypeOrNone = Literal["fuel", "mall", "supermarket", "logistics", "industrial", "hotel", "none"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Pending(_Model):
    """A parameter docs/SPEC.md requires but does not define. It never gets a default."""

    pending: Text

    # A pending value must never pass for a real one, e.g. in `if value:` or `for x in value:`.
    def __bool__(self) -> bool:
        raise PendingParameterError(f"Pending parameter used as a value: {self.pending}")

    def __iter__(self) -> Iterator[Any]:  # type: ignore[override]
        raise PendingParameterError(f"Pending parameter used as a value: {self.pending}")


def require[T](value: T | Pending, name: str) -> T:
    """Return ``value``, or raise PendingParameterError if it is still pending."""
    if isinstance(value, Pending):
        raise PendingParameterError(f"{name} is pending: {value.pending}")
    return value


# --- settings.yaml ----------------------------------------------------------------------


class PathSettings(_Model):
    raw_dir: RelativePath
    processed_dir: RelativePath
    manual_chargers_csv: RelativePath
    export_json: RelativePath
    evaluation_report: RelativePath
    analyst_scenarios: RelativePath
    analyst_eval_report: RelativePath


class LoggingSettings(_Model):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"]


class CrsSettings(_Model):
    storage: EpsgCode
    metric: EpsgCode


class Download(_Model):
    """Where a public source file is downloaded from, and the credit its licence requires."""

    url: HttpsUrl
    versioning: Literal["fixed", "rolling"]
    licence: Text
    licence_url: HttpsUrl
    credit: Text


class OsmSource(_Model):
    provider: Text
    extract: Text
    download: Download

    @model_validator(mode="after")
    def _url_names_the_extract(self) -> Self:
        if PurePosixPath(self.download.url).name != self.extract:
            raise ValueError("download.url must end with the configured extract file name")
        return self


class PopulationSource(_Model):
    provider: Text
    year: Count
    resolution_m: Metres
    constrained: Flag
    release: Text
    pixel_size_arcsec: Annotated[float, BeforeValidator(_reject_non_numbers), Field(gt=0)]
    download: Download


class ElevationSource(_Model):
    provider: Text
    product: Text
    status: Literal["deferred"]


class BoundaryUnitCounts(_Model):
    ADM2: Count
    ADM1: Count


class BoundaryDownloads(_Model):
    """ADM2 is the master geometry; ADM1 only names the province each district belongs to."""

    ADM2: Download
    ADM1: Download


class BoundarySource(_Model):
    provider: Text
    iso3: Annotated[str, Strict(), Field(pattern=r"^[A-Z]{3}$")]
    master_level: Literal["ADM2"]
    derived_levels: Annotated[tuple[Literal["ADM1", "ADM0"], ...], AfterValidator(_no_duplicates)]
    expected_units: BoundaryUnitCounts
    downloads: BoundaryDownloads


class GridCrossCheckSource(_Model):
    provider: Text
    dataset: Text
    data_year: Count
    use: Literal["cross_check_only"]
    download: Download


class OsmTags(_Model):
    charging_station: OsmTag
    fuel_station_sockets: OsmTag
    grid: OsmTag
    water: OsmTag
    protected_area: OsmTag
    national_park: OsmTag
    roads: OsmTag
    pois: Annotated[tuple[OsmTag, ...], Field(min_length=1), AfterValidator(_no_duplicates)]
    places: Annotated[tuple[OsmTag, ...], Field(min_length=1), AfterValidator(_no_duplicates)]


class ChargerCsv(_Model):
    columns: Texts
    provenance_only_columns: Texts

    @model_validator(mode="after")
    def _provenance_columns_exist(self) -> Self:
        missing = set(self.provenance_only_columns) - set(self.columns)
        if missing:
            raise ValueError(f"provenance_only_columns are not columns: {sorted(missing)}")
        return self


class SourceSettings(_Model):
    osm: OsmSource
    population: PopulationSource
    elevation: ElevationSource
    boundaries: BoundarySource
    grid_cross_check: GridCrossCheckSource
    osm_tags: OsmTags
    charger_csv: ChargerCsv
    charger_match_radius_m: Metres | Pending


class BoundingBox(_Model):
    """A longitude/latitude envelope in EPSG:4326."""

    min_lon: Annotated[float, BeforeValidator(_reject_non_numbers), Field(ge=-180, le=180)]
    min_lat: Annotated[float, BeforeValidator(_reject_non_numbers), Field(ge=-90, le=90)]
    max_lon: Annotated[float, BeforeValidator(_reject_non_numbers), Field(ge=-180, le=180)]
    max_lat: Annotated[float, BeforeValidator(_reject_non_numbers), Field(ge=-90, le=90)]

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.min_lon >= self.max_lon or self.min_lat >= self.max_lat:
            raise ValueError("min_lon/min_lat must be smaller than max_lon/max_lat")
        return self

    def as_tuple(self) -> tuple[float, float, float, float]:
        """(min_lon, min_lat, max_lon, max_lat), the order shapely and geopandas use."""
        return (self.min_lon, self.min_lat, self.max_lon, self.max_lat)


class IngestSettings(_Model):
    """Milestone 1 ingestion checks that SPEC.md does not define (docs/decisions.md D-018)."""

    rwanda_bbox: BoundingBox


class CandidateBudget(_Model):
    """How many candidates the budget stage selects from the eligible ones (D-031)."""

    size: Count


class TargetCount(_Model):
    min: Count
    max: Count

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.min > self.max:
            raise ValueError("min must not exceed max")
        return self


class DedupSettings(_Model):
    radius_m: Metres
    host_priority: Annotated[tuple[HostTypeOrNone, ...], AfterValidator(_no_duplicates)]


class CorridorSettings(_Model):
    spacing_m: Metres
    road_classes: Annotated[
        tuple[OsmValue, ...], Field(min_length=1), AfterValidator(_no_duplicates)
    ]
    host_snap_radius_m: Metres


class CandidateFilters(_Model):
    max_road_distance_m: Metres
    exclude_areas: Annotated[
        tuple[Literal["water", "protected_area"], ...], AfterValidator(_no_duplicates)
    ]


class ProfileRule(_Model):
    """SPEC §3 profile rule (D-039): urban within these radii, otherwise corridor."""

    town_radius_m: Metres
    kigali_radius_m: Metres

    @model_validator(mode="after")
    def _kigali_radius_is_larger(self) -> Self:
        town, kigali = self.town_radius_m, self.kigali_radius_m
        if isinstance(town, int) and isinstance(kigali, int) and kigali <= town:
            raise ValueError("SPEC §3: the Kigali radius must be larger than the town radius")
        return self


OsmTagList = Annotated[tuple[OsmTag, ...], Field(min_length=1), AfterValidator(_no_duplicates)]


class HostOsmTags(_Model):
    """The OSM tags that identify each SPEC §3 host type (D-027)."""

    fuel: OsmTagList
    mall: OsmTagList
    supermarket: OsmTagList
    logistics: OsmTagList
    industrial: OsmTagList
    hotel: OsmTagList

    @model_validator(mode="after")
    def _each_tag_names_one_host_type(self) -> Self:
        seen: dict[str, str] = {}
        for host_type in type(self).model_fields:
            for tag in getattr(self, host_type):
                if tag in seen:
                    raise ValueError(f"{tag!r} is listed for both {seen[tag]} and {host_type}")
                seen[tag] = host_type
        return self


class CandidateSettings(_Model):
    target_count: TargetCount
    budget: CandidateBudget
    host_types: Annotated[tuple[HostType, ...], Field(min_length=1), AfterValidator(_no_duplicates)]
    dedup: DedupSettings
    corridor: CorridorSettings
    filters: CandidateFilters
    host_osm_tags: HostOsmTags
    drivable_road_classes: Annotated[
        tuple[OsmValue, ...], Field(min_length=1), AfterValidator(_no_duplicates)
    ]
    profile: ProfileRule

    @model_validator(mode="after")
    def _priority_lists_every_host_type(self) -> Self:
        if set(self.dedup.host_priority) != {*self.host_types, "none"}:
            raise ValueError("dedup.host_priority must list every host type and 'none' once")
        return self

    @model_validator(mode="after")
    def _budget_within_target(self) -> Self:
        target, size = self.target_count, self.budget.size
        if not target.min <= size <= target.max:
            raise ValueError(
                f"budget.size {size} must lie within target_count {target.min}-{target.max}"
            )
        return self

    @model_validator(mode="after")
    def _corridor_roads_are_drivable(self) -> Self:
        missing = set(self.corridor.road_classes) - set(self.drivable_road_classes)
        if missing:
            raise ValueError(f"corridor road classes {sorted(missing)} are not drivable classes")
        return self


class KigaliCentre(_Model):
    """The OSM node used as the Kigali city centre (D-035); not an official CBD boundary."""

    osm_id: Annotated[str, Strict(), Field(pattern=r"^node/[0-9]+$")]
    label: Text


class PoiTypes(_Model):
    """The OSM tags counted as each POI type for poi_1km and poi_3km (D-033)."""

    amenity: OsmTagList
    shop: OsmTagList
    tourism: OsmTagList
    office: OsmTagList
    industrial: OsmTagList


def _power_values(tags: tuple[str, ...]) -> tuple[str, ...]:
    for tag in tags:
        key, _, value = tag.partition("=")
        if key != "power" or value in ("", "*"):
            raise ValueError(f"{tag!r} must name one power value, as power=<value>")
    return tags


PowerTagList = Annotated[OsmTagList, AfterValidator(_power_values)]


class GridOsmTags(_Model):
    """The power=* values counted as grid evidence (D-033); no other value ever counts."""

    substation: PowerTagList
    line: PowerTagList
    completeness: PowerTagList

    @model_validator(mode="after")
    def _distances_use_counted_values(self) -> Self:
        missing = (set(self.substation) | set(self.line)) - set(self.completeness)
        if missing:
            raise ValueError(f"{sorted(missing)} must also be in the completeness list")
        return self


class ChargerRules(_Model):
    """Which records are existing chargers in production mode (D-034)."""

    exclude_access: Annotated[
        tuple[OsmValue, ...], Field(min_length=1), AfterValidator(_no_duplicates)
    ]
    fuel_sockets_count_as_chargers: Flag


class FeatureSettings(_Model):
    population_radii_m: IncreasingMetres
    poi_radii_m: IncreasingMetres
    charger_count_radii_m: IncreasingMetres
    reported_only: Texts
    trunk_road_classes: Annotated[
        tuple[OsmValue, ...], Field(min_length=1), AfterValidator(_no_duplicates)
    ]
    town_centres: OsmTagList
    kigali_cbd: KigaliCentre
    poi_types: PoiTypes
    poi_exclude: OsmTagList
    grid_osm_tags: GridOsmTags
    chargers: ChargerRules


class GridEvidenceRule(_Model):
    missing_radius_m: Metres
    missing_component_score: Annotated[int, Strict(), Field(ge=0, le=100)]


FeatureNames = Annotated[
    tuple[Annotated[str, Strict(), Field(pattern=r"^[a-z0-9_]+$")], ...],
    AfterValidator(_no_duplicates),
]


class ScoringSettings(_Model):
    scale_max: Count
    grid_evidence: GridEvidenceRule
    log1p_features: FeatureNames
    lower_is_better: FeatureNames


ConfidenceLevel = Literal["High", "Medium", "Low"]


class RemoteLocationRule(_Model):
    """SPEC §6 "remote location with few data points to cross-check" (D-041)."""

    max_poi_3km: Annotated[int, Strict(), Field(ge=0)]


class ConfidenceSettings(_Model):
    grid_evidence_missing_level: ConfidenceLevel
    sparse_coverage_threshold: Annotated[float, BeforeValidator(_reject_non_numbers), Field(gt=0)]
    remote_location_rule: RemoteLocationRule
    factor_count_levels: Annotated[
        tuple[ConfidenceLevel, ...], Field(min_length=1), AfterValidator(_no_duplicates)
    ]
    universal_unknowns: Texts


class BacktestSettings(_Model):
    hit_radius_m: Metres
    precision_at: IncreasingCounts
    recall_at: Count


class BootstrapSettings(_Model):
    resamples: Count | Pending
    confidence_level: OpenFraction | Pending


class StabilitySettings(_Model):
    perturbation: OpenFraction
    renormalize: Flag
    top_k: Count
    min_overlap: Share


class EvaluationSettings(_Model):
    label: Text
    backtest: BacktestSettings
    random_baseline_seeds: Count
    random_seed: Annotated[int, Strict(), Field(ge=0)] | Pending
    bootstrap: BootstrapSettings
    stability: StabilitySettings
    grounding_target: Annotated[float, BeforeValidator(_reject_non_numbers), Field(gt=0, le=1)]


NonNegative = Annotated[float, BeforeValidator(_reject_non_numbers), Field(ge=0)]


class SensitivitySettings(_Model):
    """SPEC §8 sensitivity values (D-046), each tried with the other parameters at default."""

    lambda_: Annotated[
        tuple[NonNegative, ...], Field(min_length=1, alias="lambda"), AfterValidator(_no_duplicates)
    ]
    service_radius_m: Annotated[
        tuple[Metres, ...], Field(min_length=1), AfterValidator(_no_duplicates)
    ]
    existing_charger_demand_factor: Annotated[
        tuple[Share, ...], Field(min_length=1), AfterValidator(_no_duplicates)
    ]


class OptimizationSettings(_Model):
    demand_h3_resolution: Annotated[int, Strict(), Field(ge=0, le=15)]
    service_radius_m: Metres
    n_sites: Count
    lambda_: Annotated[float, BeforeValidator(_reject_non_numbers), Field(ge=0, alias="lambda")]
    min_spacing_m: Metres
    min_score_percentile: Annotated[int, Strict(), Field(gt=0, lt=100)]
    existing_charger_demand_factor: Share
    time_limit_s: Count
    require_host: Flag
    sensitivity: SensitivitySettings


class ExportSettings(_Model):
    boundary_geojson_max_kb: Count


class AnalystSettings(_Model):
    """The optional AI Site Analyst (Milestone 9, D-051, D-054). Its one secret, the chosen
    provider's API key (``ANTHROPIC_API_KEY`` or ``OPENAI_API_KEY``), is read by
    ``sitescout.analyst.credentials`` (D-050); every value here comes from YAML only, like
    the rest of this file."""

    provider: Literal["anthropic", "openai"]  # D-054: two real providers, one interface
    model: Text
    max_tool_calls: Annotated[int, Strict(), Field(ge=1, le=20)]
    max_tokens: Count
    timeout_s: Annotated[float, BeforeValidator(_reject_non_numbers), Field(gt=0, le=600)]
    # null leaves the parameter out of the request; a number is sent to the provider, and a
    # model that does not accept it rejects the request (the run then falls back).
    temperature: Share | None

    @model_validator(mode="after")
    def _model_matches_provider(self) -> AnalystSettings:
        # Catches switching the provider without switching the model. Anthropic model ids
        # all start with "claude-"; the provider still rejects an id it does not serve.
        is_claude = self.model.startswith("claude-")
        if (self.provider == "anthropic") != is_claude:
            raise ValueError(
                f"model {self.model!r} does not belong to provider {self.provider!r}; "
                "set analyst.provider and analyst.model together"
            )
        return self


class Settings(_Model):
    """config/settings.yaml."""

    paths: PathSettings
    logging: LoggingSettings
    crs: CrsSettings
    sources: SourceSettings
    ingest: IngestSettings
    candidates: CandidateSettings
    features: FeatureSettings
    scoring: ScoringSettings
    confidence: ConfidenceSettings
    evaluation: EvaluationSettings
    optimization: OptimizationSettings
    export: ExportSettings
    analyst: AnalystSettings


# --- weights.yaml -----------------------------------------------------------------------


class _WeightGroup(_Model):
    """Weights that must sum to 1 (SPEC §5)."""

    @model_validator(mode="after")
    def _sums_to_one(self) -> Self:
        total = sum(getattr(self, name) for name in type(self).model_fields)
        if not math.isclose(total, 1.0, abs_tol=1e-9):
            raise ValueError(f"weights must sum to 1 (got {total:.6g})")
        return self


class DemandWeights(_WeightGroup):
    pop_5km: Share
    pop_1km: Share
    pop_10km: Share


class AccessWeights(_WeightGroup):
    dist_road_m: Share
    dist_trunk_m: Share


class HostCommercialWeights(_WeightGroup):
    poi_1km: Share
    poi_3km: Share


class ChargingGapWeights(_WeightGroup):
    dist_charger_m: Share
    chargers_10km: Share
    chargers_25km: Share


class GridEvidenceWeights(_WeightGroup):
    dist_substation_m: Share
    dist_line_m: Share


class ComponentWeights(_Model):
    """Feature weights within each scoring component."""

    demand: DemandWeights
    access: AccessWeights
    host_commercial: HostCommercialWeights
    charging_gap: ChargingGapWeights
    grid_evidence: GridEvidenceWeights


class ProfileWeights(_WeightGroup):
    """Component weights for one profile."""

    demand: Share
    host_commercial: Share
    access: Share
    charging_gap: Share
    grid_evidence: Share


class Profiles(_Model):
    urban: ProfileWeights
    corridor: ProfileWeights


class HostTypeBonus(_Model):
    fuel: BonusPoints
    mall: BonusPoints
    supermarket: BonusPoints
    logistics: BonusPoints
    hotel: BonusPoints
    industrial: BonusPoints
    none: BonusPoints


class RoadClassBonus(_Model):
    trunk: BonusPoints
    primary: BonusPoints


class HostCommercialBonuses(_Model):
    host_type: HostTypeBonus


class AccessBonuses(_Model):
    road_class: RoadClassBonus


class Bonuses(_Model):
    """Points added to a component score, which is capped at 100 (SPEC §5)."""

    host_commercial: HostCommercialBonuses
    access: AccessBonuses


class Weights(_Model):
    """config/weights.yaml."""

    components: ComponentWeights
    profiles: Profiles
    bonuses: Bonuses

    def weighted_features(self) -> tuple[str, ...]:
        """Names of every feature that carries a weight."""
        return tuple(
            feature
            for component in type(self.components).model_fields
            for feature in type(getattr(self.components, component)).model_fields
        )


# --- Loading ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Config:
    """Validated settings and weights, with paths resolved against the repository root."""

    root: Path
    settings: Settings
    weights: Weights

    def resolve(self, relative: str) -> Path:
        """Absolute path for a path from settings.yaml; it must stay inside the repository."""
        resolved = (self.root / relative).resolve()
        if not resolved.is_relative_to(self.root):
            raise ConfigError(f"{relative!r} resolves outside the repository")
        return resolved

    def pending(self) -> dict[str, str]:
        """Every pending parameter, as {dotted key: reason}."""
        return {
            **_find_pending(self.settings, "settings."),
            **_find_pending(self.weights, "weights."),
        }

    def snapshot(self) -> dict[str, Any]:
        """JSON-ready copy of both files, for the export's config snapshot (SPEC §10)."""
        return {
            "settings": self.settings.model_dump(mode="json", by_alias=True),
            "weights": self.weights.model_dump(mode="json", by_alias=True),
        }


def load_config(
    *,
    settings_file: Path = SETTINGS_FILE,
    weights_file: Path = WEIGHTS_FILE,
    overrides: Mapping[str, object] | None = None,
) -> Config:
    """Load and validate config/settings.yaml and config/weights.yaml.

    ``overrides`` maps dotted keys such as ``"settings.optimization.lambda"`` to new values,
    for sensitivity runs. Each override is logged, and the result is validated exactly like
    the files themselves.
    """
    return build_config(read_yaml(settings_file), read_yaml(weights_file), overrides=overrides)


def build_config(
    settings_data: Mapping[str, Any],
    weights_data: Mapping[str, Any],
    *,
    overrides: Mapping[str, object] | None = None,
) -> Config:
    """Validate already-parsed settings and weights documents; see ``load_config``."""
    documents = {
        "settings": copy.deepcopy(dict(settings_data)),
        "weights": copy.deepcopy(dict(weights_data)),
    }
    for key, value in (overrides or {}).items():
        _apply_override(documents, key, value)
    settings = _validate(Settings, documents["settings"], "settings")
    weights = _validate(Weights, documents["weights"], "weights")
    _check_across_files(settings, weights)
    return Config(root=PROJECT_ROOT, settings=settings, weights=weights)


def _validate[ModelT: BaseModel](model: type[ModelT], data: dict[str, Any], name: str) -> ModelT:
    try:
        return model.model_validate(data)
    except ValidationError as error:
        raise ConfigError(f"Invalid {name}:\n{error}") from error


def _is_pending_marker(value: object) -> bool:
    return isinstance(value, dict) and set(value) == {"pending"}


def _contains_pending(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    return _is_pending_marker(value) or any(_contains_pending(item) for item in value.values())


def _apply_override(documents: dict[str, dict[str, Any]], dotted: str, value: object) -> None:
    document, _, path = dotted.partition(".")
    if document not in documents or not path:
        raise ConfigError(f"Override {dotted!r} must start with 'settings.' or 'weights.'")
    *parents, leaf = path.split(".")
    node: Any = documents[document]
    for part in parents:
        node = node.get(part) if isinstance(node, dict) else None
    if not isinstance(node, dict) or leaf not in node:
        raise ConfigError(f"Override {dotted!r} does not match any configured key")
    if _contains_pending(node[leaf]) or _contains_pending(value):
        raise ConfigError(
            f"Override {dotted!r} touches a pending parameter; pending parameters are "
            "decided in the YAML file with a recorded decision, never by an override"
        )
    logger.warning("Config override %s: %r -> %r", dotted, node[leaf], value)
    node[leaf] = value


def _check_across_files(settings: Settings, weights: Weights) -> None:
    weighted_but_reported = set(weights.weighted_features()) & set(settings.features.reported_only)
    if weighted_but_reported:
        raise ConfigError(
            f"Reported-only features must never be weighted: {sorted(weighted_but_reported)}"
        )
    if set(HostTypeBonus.model_fields) != {*settings.candidates.host_types, "none"}:
        raise ConfigError(
            "The host-type bonus table in weights.yaml must cover exactly the host types in "
            "settings.yaml, plus 'none'"
        )
    if set(HostOsmTags.model_fields) != set(settings.candidates.host_types):
        raise ConfigError("candidates.host_osm_tags must cover exactly candidates.host_types")
    uncovered = [
        tag
        for host_type in HostOsmTags.model_fields
        for tag in getattr(settings.candidates.host_osm_tags, host_type)
        if not _within_extraction_scope(tag, settings.sources.osm_tags.pois)
    ]
    if uncovered:
        raise ConfigError(
            f"Host tags {uncovered} are not extracted into osm_pois (sources.osm_tags.pois)"
        )
    _check_features(settings)
    weighted = set(weights.weighted_features())
    for name in ("log1p_features", "lower_is_better"):
        unknown = set(getattr(settings.scoring, name)) - weighted
        if unknown:
            raise ConfigError(f"scoring.{name} {sorted(unknown)} are not weighted features")


def _check_features(settings: Settings) -> None:
    """Milestone 3 feature settings that depend on other sections (D-032 to D-035)."""
    features, tags = settings.features, settings.sources.osm_tags
    not_drivable = set(features.trunk_road_classes) - set(settings.candidates.drivable_road_classes)
    if not_drivable:
        raise ConfigError(f"features.trunk_road_classes {sorted(not_drivable)} are not drivable")
    poi_tags = [
        *(tag for name in PoiTypes.model_fields for tag in getattr(features.poi_types, name)),
        *features.poi_exclude,
    ]
    uncovered = [tag for tag in poi_tags if not _within_extraction_scope(tag, tags.pois)]
    if uncovered:
        raise ConfigError(f"POI tags {uncovered} are not extracted into osm_pois")
    if tags.charging_station not in features.poi_exclude:
        raise ConfigError(
            f"features.poi_exclude must list {tags.charging_station}: charging stations never "
            "count as POIs (D-033)"
        )
    outside = [tag for tag in features.town_centres if tag not in tags.places]
    if outside:
        raise ConfigError(f"features.town_centres {outside} are not in sources.osm_tags.places")


def _within_extraction_scope(tag: str, scope: tuple[str, ...]) -> bool:
    """True if an OSM ``key=value`` tag is selected by one of the ``scope`` selectors."""
    key, _, value = tag.partition("=")
    return f"{key}=*" in scope or f"{key}={value}" in scope


def _find_pending(model: BaseModel, prefix: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for name, field in type(model).model_fields.items():
        value = getattr(model, name)
        key = f"{prefix}{field.alias or name}"
        if isinstance(value, Pending):
            found[key] = value.pending
        elif isinstance(value, BaseModel):
            found.update(_find_pending(value, f"{key}."))
    return found


def log_summary(config: Config) -> None:
    """Log the main values of a loaded configuration and every pending parameter."""
    profiles = config.weights.profiles
    for name in type(profiles).model_fields:
        profile = getattr(profiles, name)
        shares = ", ".join(
            f"{component} {getattr(profile, component):.2f}"
            for component in type(profile).model_fields
        )
        logger.info("Profile %s: %s", name, shares)
    network = config.settings.optimization
    logger.info(
        "Network: %d sites, service radius %d m, minimum spacing %d m, lambda %s, "
        "min_score_percentile %d",
        network.n_sites,
        network.service_radius_m,
        network.min_spacing_m,
        network.lambda_,
        network.min_score_percentile,
    )
    pending = config.pending()
    if not pending:
        logger.info("No parameter is pending: every parameter docs/SPEC.md requires is decided.")
    else:
        logger.warning(
            "%d parameters are pending; docs/SPEC.md does not define them:", len(pending)
        )
    for key, reason in pending.items():
        logger.warning("  %s: %s", key, reason)
    logger.info("Configuration is valid.")
