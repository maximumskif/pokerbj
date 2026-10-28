"""Claude integration: output validation, late responses, failures, no-key behaviour (all mocked)."""
import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.claude_client import ClaudeService, Explanation, validate_explanation
from app.service import AppService

ROOT = Path(__file__).resolve().parent.parent

ANALYSIS = {
    "game": "blackjack", "state_version": 7, "status": "decision",
    "facts": [{"name": "player total", "value": 16}, {"name": "dealer upcard", "value": "Kd"},
              {"name": "recommended action", "value": "surrender"},
              {"name": "EV stand", "value": -0.5404}, {"name": "EV hit", "value": -0.5398},
              {"name": "EV surrender", "value": -0.5},
              {"name": "dealer final bust probability", "value": 0.2121}],
    "ev": {"assumptions": ["x"], "model_label": "infinite deck"},
    "recommendation": {"caveat": "chart"},
}


def exp(**kw):
    base = dict(state_version=7, headline="Surrendering loses half the bet.",
                explanation="Against a king, 16 stands at -0.5404 and hits at -0.5398 per unit; surrender is -0.5. "
                            "The dealer busts about 21.2% of the time.",
                key_points=["Surrender has the highest EV here"], uncertainty=[], action_referenced="surrender")
    base.update(kw)
    return Explanation(**base)


def test_valid_explanation_accepted():
    assert validate_explanation(exp(), ANALYSIS, []) == []


def test_invented_number_rejected():
    p = validate_explanation(exp(explanation="You win 63% of the time when hitting."), ANALYSIS, [])
    assert any("unsupported number" in x for x in p)


def test_contradicting_action_rejected():
    p = validate_explanation(exp(action_referenced="hit"), ANALYSIS, [])
    assert any("contradicts" in x for x in p)


def test_wrong_version_rejected():
    assert validate_explanation(exp(state_version=6), ANALYSIS, [])


def test_guarantee_language_rejected():
    p = validate_explanation(exp(headline="This is a guaranteed edge."), ANALYSIS, [])
    assert any("guaranteed" in x for x in p)


def test_poker_action_reference_rejected():
    poker = {"game": "holdem", "state_version": 3, "facts": [{"name": "showdown equity", "value": 0.4119}]}
    e = exp(state_version=3, headline="Equity is 41.2%.", explanation="Equity is 41.2%.", key_points=[],
            action_referenced="call")
    assert any("no action recommendation" in x for x in validate_explanation(e, poker, []))


def _fake_client(parsed=None, delay=0.0, exc=None):
    async def parse(**kwargs):
        if delay:
            await asyncio.sleep(delay)
        if exc:
            raise exc
        return SimpleNamespace(parsed_output=parsed, stop_reason="end_turn")
    return SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(parse=parse)))


def test_no_api_key_is_graceful(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    res = asyncio.run(ClaudeService().explain(ANALYSIS, []))
    assert res["status"] == "skipped" and "ANTHROPIC_API_KEY" in res["reason"]


def test_api_failure_is_reported(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    c = ClaudeService()
    c._client = _fake_client(exc=TimeoutError("timed out"))
    res = asyncio.run(c.explain(ANALYSIS, []))
    assert res["status"] == "error" and "TimeoutError" in res["reason"]


def test_rate_limit_and_usage_cap(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    c = ClaudeService()
    c._client = _fake_client(parsed=exp())
    assert asyncio.run(c.explain(ANALYSIS, []))["status"] == "ok"
    other = dict(ANALYSIS, facts=ANALYSIS["facts"] + [{"name": "x", "value": 1}])
    assert "rate limited" in asyncio.run(c.explain(other, []))["reason"]
    # identical request is served from cache, no new API call
    assert asyncio.run(c.explain(ANALYSIS, []))["cached"]
    assert c.requests_made == 1
    c.max_requests = 1
    assert "usage limit" in asyncio.run(c.explain(other, [], force=True))["reason"]


def test_late_response_discarded(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    async def run():
        svc = AppService(ROOT, Path(tempfile.mkdtemp()))
        svc.claude._client = _fake_client(parsed=exp(), delay=0.2)
        svc.analysis = dict(ANALYSIS)
        svc.version = 7
        task = asyncio.create_task(svc.request_explanation(force=True))
        await asyncio.sleep(0.05)
        svc.version = 8  # state changed while Claude was thinking
        res = await task
        return res, svc
    res, svc = asyncio.run(run())
    assert res["status"] == "discarded_stale"
    assert svc.explanation is None
    assert any(e["kind"] == "discard" for e in svc.timeline)


def test_rejected_output_not_displayed(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    c = ClaudeService()
    c._client = _fake_client(parsed=exp(explanation="Hitting wins 80% of the time."))
    res = asyncio.run(c.explain(ANALYSIS, []))
    assert res["status"] == "rejected" and "explanation" not in res
