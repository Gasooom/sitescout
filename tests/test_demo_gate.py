"""M11 (D-063): the demo-readiness gate's live part, run entirely offline here with scripted
models on the SYNTHETIC world; no test reads a key or calls a provider."""

import re

import pytest

from agent_support import call, final, make_context, reactive, stmt
from sitescout.config import PROJECT_ROOT, load_config
from sitescout.demo_gate import GATE_KINDS, GateRun, render_report, run_gate, verdicts
from sitescout.server import InvestigationService

SYNTHETIC_ID = re.compile(r"^cand-[a-z]$")
SENTINEL = "SENTINEL-ANSWER-TEXT"


@pytest.fixture(scope="module")
def context(analyst_world):
    return make_context(analyst_world)


@pytest.fixture(scope="module")
def config():
    return load_config()


def counting_model(answer_ok: bool):
    calls = {"answers": 0}

    def step(ctx):
        site = re.search(r"cand-[a-z]\b", ctx.question).group()
        if not ctx.transcript:
            return call("get_site", candidate_id=site)
        calls["answers"] += 1
        number = "71.0" if answer_ok else "99.9"
        text = f"The stored score of {site} is {number}. {SENTINEL}"
        return final(stmt(text, "CALCULATED", (f"{site}/score",)))

    return reactive(step), calls


def service(context, config, model):
    return InvestigationService(context, config.settings.agent, model, id_pattern=SYNTHETIC_ID)


def run(kind, status):
    return GateRun(
        kind=kind, group=None, candidate_id="cand-a", status=status, termination=None,
        category=None, attempts=1, rules=(), steps=(), elapsed_display="1.0 s",
    )  # fmt: skip


@pytest.mark.parametrize(
    ("statuses", "passed"),
    [
        (["answered", "answered"], True),
        (["answered", "fallback"], True),
        (["fallback", "fallback"], False),  # completed safely, but never validated
        (["answered", "busy"], False),  # a run that did not complete
        (["answered"], False),  # fewer runs than configured
    ],
)
def test_the_verdict_rule(statuses, passed):
    kind = GATE_KINDS[0][0]
    result = verdicts([run(kind, s) for s in statuses], runs_per_kind=2, min_validated=1)
    assert result[0].passed is passed


def test_the_gate_runs_each_kind_the_configured_number_of_times(context, config):
    model, calls = counting_model(answer_ok=True)
    result = run_gate(service(context, config, model), config)
    per_kind = config.settings.demo_gate.runs_per_kind
    assert len(result.runs) == per_kind * len(GATE_KINDS)
    assert [r.kind for r in result.runs] == [k for k, _ in GATE_KINDS for _ in range(per_kind)]
    assert calls["answers"] == len(result.runs)  # one validated answer per run
    assert result.passed and all(r.category == "answered_grounded" for r in result.runs)
    assert all(r.steps == ("Retrieved site record",) for r in result.runs)


def test_a_kind_that_never_validates_fails_the_gate(context, config):
    model, _ = counting_model(answer_ok=False)
    result = run_gate(service(context, config, model), config)
    assert not result.passed
    assert all(r.status == "fallback" and r.rules == ("number_not_grounded",) for r in result.runs)
    assert "**fail**" in render_report(result)


def test_the_report_never_contains_answer_text(context, config):
    model, _ = counting_model(answer_ok=True)
    report = render_report(run_gate(service(context, config, model), config))
    assert SENTINEL not in report and "71.0" not in report
    assert "not a measure of the agent's reliability" in report


def test_the_report_is_a_deterministic_function_of_the_result(context, config):
    model, _ = counting_model(answer_ok=True)
    result = run_gate(service(context, config, model), config, generated_at="2026-09-28T00:00:00")
    assert render_report(result) == render_report(result)
    assert "\r" not in render_report(result)


def test_the_gate_script_only_parses_arguments_and_calls_src():
    text = (PROJECT_ROOT / "scripts" / "demo_gate.py").read_text(encoding="utf-8")
    assert "from sitescout.demo_gate import" in text
    assert "os.environ" not in text and "dotenv" not in text
