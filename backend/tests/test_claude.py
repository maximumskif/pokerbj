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


# ---- chess summaries ---------------------------------------------------------

from app.chess_review import parse_pgn, review  # noqa: E402
from app.claude_client import ChessMoment, ChessSummary, chess_payload, validate_chess_summary  # noqa: E402
from tests.test_chess_review import SCHOLAR, FakeEngine  # noqa: E402


@pytest.fixture(scope="module")
def scholar_review():
    return review(parse_pgn(SCHOLAR), FakeEngine(), None)


def _summary(**kw):
    base = dict(headline="Black missed the mate threat on f7.",
                summary="White aimed the queen and bishop at f7. Black developed with Nf6, which allowed Qxf7# at once.",
                key_moments=[ChessMoment(ply=6, san="Nf6", comment="Allows forced mate in 1; the engine preferred a defence.")],
                lessons=["Before developing, check what the opponent's last move threatens."])
    base.update(kw)
    return ChessSummary(**base)


def test_chess_payload_lists_the_blunder(scholar_review):
    p = chess_payload(scholar_review)
    nf6 = next(m for m in p["notable_moves"] if m["san"] == "Nf6")
    assert nf6["ply"] == 6 and nf6["classification"] == "blunder" and "forced mate in 1" in nf6["note"]


def test_valid_chess_summary_accepted(scholar_review):
    p = chess_payload(scholar_review)
    assert validate_chess_summary(_summary(), scholar_review, p) == []


@pytest.mark.parametrize("kw,needle", [
    ({"summary": "Black had a 73% chance before Nf6."}, "unsupported number"),
    ({"summary": "Nc3 would have changed nothing; Black should have played Ng4."}, "not in the game"),
    ({"key_moments": [ChessMoment(ply=6, san="Qxf7#", comment="x")]}, "was 'Nf6'"),
    ({"key_moments": [ChessMoment(ply=40, san="Nf6", comment="x")]}, "not one of the notable moves"),
])
def test_chess_summary_rejections(scholar_review, kw, needle):
    p = chess_payload(scholar_review)
    problems = validate_chess_summary(_summary(**kw), scholar_review, p)
    assert any(needle in x for x in problems), problems


def test_chess_summary_round_trip(monkeypatch, scholar_review):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    c = ClaudeService()
    c._client = _fake_client(parsed=_summary())
    res = asyncio.run(c.explain_chess(scholar_review, force=True))
    assert res["status"] == "ok" and res["summary"]["key_moments"][0]["san"] == "Nf6"
    assert asyncio.run(c.explain_chess(scholar_review))["cached"] and c.requests_made == 1
    c2 = ClaudeService()
    c2._client = _fake_client(parsed=_summary(summary="Ng4 would have held."))
    assert asyncio.run(c2.explain_chess(scholar_review, force=True))["status"] == "rejected"
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert ClaudeService().has_key is False
    assert asyncio.run(ClaudeService().explain_chess(scholar_review))["status"] == "skipped"
