"""The SYNTHETIC world the agent evaluation runs on, built outside pytest.

The same world as the ``analyst_world`` fixture in conftest.py (three candidates, one network
site, one brief), so ``scripts/agent_eval.py`` can produce the committed report without any
data in ``data/``. Nothing here is real."""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sitescout.agent import AgentContext
from sitescout.config import SETTINGS_FILE, WEIGHTS_FILE, Config, read_yaml
from sitescout.investigation import InvestigationData
from sitescout.knowledge import build_knowledge_index


@contextmanager
def agent_world() -> Iterator[tuple[Config, AgentContext]]:
    """The SYNTHETIC world's config and an ``AgentContext`` over it and the real knowledge index."""
    from sitescout.briefs import run_briefs
    from sitescout.features import run_features
    from sitescout.optimize import run_network
    from sitescout.scoring import run_scores
    from synthetic_features import feature_config, write_feature_world

    config = feature_config(
        read_yaml(SETTINGS_FILE), read_yaml(WEIGHTS_FILE), **{"settings.optimization.n_sites": 1}
    )
    with tempfile.TemporaryDirectory(prefix="sitescout-agent-eval-") as name:
        processed, briefs = Path(name) / "processed", Path(name) / "briefs"
        processed.mkdir()
        write_feature_world(processed, config.settings)
        run_features(config.settings, processed)
        run_scores(config.settings, config.weights, processed)
        run_network(config, processed)
        run_briefs(config, processed, briefs)
        yield (
            config,
            AgentContext(
                investigation=InvestigationData.load(config, processed),
                knowledge=build_knowledge_index(config),
                knowledge_top_k=config.settings.agent.knowledge_top_k,
            ),
        )
