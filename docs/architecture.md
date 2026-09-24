# Architecture

SiteScout is a batch pipeline in Python that writes one JSON export, plus a front end that only displays that export. This page covers the stages, what passes between them and the rules every stage follows. The method itself is in [SPEC.md](SPEC.md); the reasons behind each choice are in [decisions.md](decisions.md).

## Pipeline

| Stage | SPEC | Milestone | Planned module | Output |
|---|---|---|---|---|
| Ingest public sources | §2 | M1 | `sitescout/ingest/` | validated layers in `data/processed/` |
| Generate candidates | §3 | M2 | `sitescout/candidates.py` | 200 to 400 candidates with a generic host label, OSM ID, district, province and profile |
| Compute features | §4 | M3 | `sitescout/features/` | one feature row per candidate, in backtest and production mode |
| Score and rate confidence | §5, §6 | M4 | `sitescout/scoring.py`, `sitescout/confidence.py` | component scores, overall score, confidence level and reasons |
| Evaluate | §7 | M5 to M7 | `sitescout/evaluation.py` | `reports/evaluation.md` |
| Select the network | §8 | M6 | `sitescout/optimize.py` | exact MCLP, greedy and Top-30 selections with marginal coverage |
| Evidence and briefs | §9 | M7 | `sitescout/evidence.py`, `sitescout/briefs.py` | 30 site briefs and the grounding check |
| Export | §10 | M8 | `sitescout/export.py` | `data/export/sitescout.json` |
| Front end | §10 | M8 | `app/` | renders the export |

After Milestone 0 only `sitescout/config.py` and `sitescout/logging_setup.py` exist. Every other module is created in its own milestone.

```text
public sources -> data/raw/ -> ingest -> data/processed/ (GeoParquet, EPSG:4326)
  -> candidates -> features -> scoring + confidence -> evaluation -> reports/evaluation.md
  -> network selection -> evidence + briefs -> export -> data/export/sitescout.json
  -> app/ (display only)
```

## Rules every stage follows

- **Configuration.** Stages receive a loaded `Config` object. They never read `config/` files or environment variables themselves.
- **Pending parameters.** A parameter that SPEC.md requires but does not define is marked `pending` in `config/settings.yaml`. A stage that needs it calls `require(...)`, which stops with an error naming the parameter until the value is decided and recorded.
- **Coordinates.** Data is stored in EPSG:4326. Distances and areas are computed in EPSG:32735, never in degrees.
- **Stage contracts.** Each stage validates its inputs, writes outputs with a declared schema and is idempotent: the same inputs and config give the same outputs.
- **Modes.** Backtest and production mode are an explicit argument, never a global switch. Backtest mode removes existing chargers from every feature, including POI and host features.
- **Numbers.** Python computes every number. The front end filters, sorts and displays; it never computes scores, coverage or selection. LLMs never produce numbers.
- **Logging.** Library modules call `logging.getLogger(__name__)`. Only entry points configure logging, to stderr. Ruff rule `T20` rejects `print`.
- **Scripts.** Files in `scripts/` only parse arguments and call `src/sitescout/`.
- **Synthetic data.** Allowed only when labelled synthetic in code, UI and docs. The export records `data_status` as `pipeline` or `synthetic`.
- **Names.** Outputs use generic host labels plus OSM IDs, never business, brand or operator names.
- **Data in git.** Nothing under `data/` is committed. [data_sources.md](data_sources.md) and `config/` describe how to rebuild it.

## Configuration

`config/settings.yaml` holds every parameter SPEC.md defines, grouped by stage, plus the paths and log level. `config/weights.yaml` holds the feature weights, profile weights and bonus tables. Both are loaded by `sitescout.config.load_config()`:

- YAML is parsed with a loader that rejects duplicate keys.
- Pydantic models reject unknown keys, refuse to convert types (a quoted `"30"` stays an error) and are frozen once loaded. Lists become tuples.
- Paths must be relative and are resolved against the repository root, not the working directory (`Config.resolve`).
- Sensitivity runs pass explicit overrides such as `{"settings.optimization.lambda": 0.02}`. Each is logged, the result is validated like the files, and overrides can never fill a pending parameter.
- `Config.pending()` lists every pending parameter. `Config.snapshot()` returns both files as JSON-ready data for the export's config snapshot.
- `scripts/check_config.py` validates both files and logs a summary with every pending parameter.

## Repository layout

```text
config/                 settings.yaml, weights.yaml
src/sitescout/          config.py, logging_setup.py (later: one module per stage)
scripts/                check_config.py (later: one entry point per stage)
tests/                  pytest suite; synthetic fixtures, if any, go in tests/fixtures/
docs/                   SPEC.md, architecture.md, decisions.md, data_sources.md
data/                   raw/, manual/, processed/, export/ (gitignored, created by the pipeline)
reports/                evaluation.md and site briefs (from Milestone 5)
app/                    front end (Milestone 8)
```
