"""Visual QA for the M11 page (D-062): serve the real page, export and processed data with a
SCRIPTED model, so every investigation state can be looked at in a browser without a key.

  uv run python tests/qa_server.py answered        # validated answer with citations and trace
  uv run python tests/qa_server.py fallback        # two rejected answers -> retrieved records only
  uv run python tests/qa_server.py provider_error  # the provider fails
  uv run python tests/qa_server.py unavailable     # no provider at all

Not a test module (pytest collects only test_*.py) and never a production path: the scripted
answers here are fixtures, not model output. It makes no network call beyond 127.0.0.1 and
writes nothing. `?qa=site|network|verify|explain` in the page URL clicks that action once the
page has loaded, and `&open=1` expands citations and the trace, so a headless browser
screenshot can show a result.
"""

import argparse
import logging
import re
import sys
from urllib.parse import urlsplit

from agent_support import call, final, reactive, seen, stmt
from sitescout.agent import AgentContext
from sitescout.analyst.provider import Provider
from sitescout.analyst.provider_common import ProviderError
from sitescout.config import load_config
from sitescout.logging_setup import configure_logging
from sitescout.server import InvestigationService, SiteScoutServer, _Handler

log = logging.getLogger("qa_server")
SITE = re.compile(r"cand-[0-9a-f]{12}")

AUTORUN = """
window.addEventListener("load", () => setTimeout(() => {
  const query = new URLSearchParams(location.search);
  const labels = { site: "Investigate this site", network: "Investigate network difference",
    verify: "What would we need to verify?", explain: "Explain" };
  const wanted = labels[query.get("qa")];
  const buttons = [...document.querySelectorAll("button.inv-action")]
    .filter((b) => !b.hidden && b.textContent === wanted);
  const button = query.get("qa") === "explain" ? buttons[buttons.length - 1] : buttons[0];
  if (button) button.click();
  if (query.get("open")) {
    const expand = () => {
      for (const d of document.querySelectorAll(".inv-result details")) d.open = true;
    };
    new MutationObserver(expand).observe(document.body, { childList: true, subtree: true });
  }
}, 400));
"""


def _answer(ctx):
    """Cite up to two short structured records and one knowledge chunk the run fetched."""
    records = seen(ctx)
    site = SITE.search(ctx.question)
    wanted = [f"{site.group()}/score", f"{site.group()}/rank"] if site else []
    structured = [records[i] for i in wanted if i in records] or [
        r for r in records.values()
        if not r.id.startswith("kb/") and r.type != "UNKNOWN" and len(r.display) < 24
    ][:2]  # fmt: skip
    statements = [
        stmt(f"The stored record shows {r.display}.", r.type, (r.id,)) for r in structured
    ]
    chunk = next((r for r in records.values() if r.id.startswith("kb/")), None)
    evidence = []
    if chunk is not None:
        quote = " ".join(chunk.display.split()[:8])
        evidence = [stmt("The method is documented in the project knowledge.", "RETRIEVED_FACT",
                         (chunk.id,), (quote,))]  # fmt: skip
    return final(
        *statements,
        evidence=evidence,
        unknowns=[stmt("The requested information is not available.")],
    )


def answered(ctx):
    site = SITE.search(ctx.question)
    if not ctx.transcript:
        return call("get_site", candidate_id=site.group()) if site else call("network_summary")
    if len(ctx.transcript) == 1:
        return call("search_knowledge", query="how the network is chosen and how sites are scored")
    return _answer(ctx)


def rejected(ctx):
    site = SITE.search(ctx.question)
    if not ctx.transcript:
        return call("get_site", candidate_id=site.group()) if site else call("network_summary")
    records = [r for r in seen(ctx).values() if r.type != "UNKNOWN"]
    return final(stmt("The stored record shows 999.9.", records[0].type, (records[0].id,)))


class Failing(Provider):
    def next_step(self, context):
        raise ProviderError("scripted provider failure")


class QAHandler(_Handler):
    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/app/index.html" and self._host_allowed():
            page = (self.server.config.root / "app" / "index.html").read_text(encoding="utf-8")
            page = page.replace("</body>", '<script src="/qa/autorun.js"></script>\n</body>')
            self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/qa/autorun.js" and self._host_allowed():
            self._send(200, AUTORUN.encode("utf-8"), "text/javascript; charset=utf-8")
        else:
            super().do_GET()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    scenarios = ["answered", "fallback", "provider_error", "unavailable"]
    parser.add_argument("scenario", choices=scenarios)
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    configure_logging("WARNING")
    config = load_config()
    processed = config.resolve(config.settings.paths.processed_dir)
    context = AgentContext.load(config, processed)
    provider = {
        "answered": lambda: reactive(answered),
        "fallback": lambda: reactive(rejected),
        "provider_error": Failing,
        "unavailable": lambda: None,
    }[args.scenario]()
    service = InvestigationService(
        context if provider is not None else None, config.settings.agent, provider
    )
    server = SiteScoutServer(config, service, args.port, handler=QAHandler)
    sys.stderr.write(f"QA server ({args.scenario}) at {server.url}\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
