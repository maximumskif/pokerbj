"""Deterministic analysis from tracked state. Authoritative numbers live here."""
from __future__ import annotations

import time

from pydantic import BaseModel, Field

from .cards import Card
from .engines.bj_ev import INFINITE_DECK, EVModel, probs_from_counts
from .engines.bj_rules import BlackjackRules, HandContext, hand_total, legal_actions
from .engines.bj_strategy import chart_supported, recommend
from .engines.poker_equity import equity, parse_range, pot_odds
from .engines.poker_eval import category, evaluate
from .tracking.poker_tracker import PokerTracker


class PokerSettings(BaseModel):
    default_range: str = Field("random", description="range assumed for every active opponent")
    seat_ranges: dict[str, str] = Field(default_factory=dict)  # "1": "QQ+, AK"
    mc_samples: int = Field(20000, ge=1000, le=500000)
    time_budget_s: float = Field(1.5, ge=0.2, le=20)
    pot_includes_current_bets: bool = False


def _fmt_rank(v: int) -> str:
    return "A" if v == 1 else str(v)


def _problem_slots(slots: list[dict], label: str) -> tuple[list[str], list[str]]:
    """Return (committed card values in order, problems).

    A slot whose latest raw reading disagrees with its committed value (card
    arriving, animation, occlusion) is a problem: we never compute on a hand
    that may be changing.
    """
    vals, problems = [], []
    for s in slots:
        st, v, raw = s["status"], s["value"], s.get("raw")
        if st == "uncertain":
            problems.append(f"{label}: {s['region']} is uncertain (recognition unstable; correct it manually)")
            break
        if st == "pending" and v is None:
            if raw != "empty":
                problems.append(f"{label}: {s['region']} not yet stable")
            break
        if st != "manual" and raw != v:
            problems.append(f"{label}: {s['region']} reading changing ({v} -> {raw or 'unreadable'})")
            break
        if v == "empty":
            break
        vals.append(v)
    return vals, problems


def analyze_blackjack(snap: dict, rules: BlackjackRules, version: int) -> dict:
    out: dict = {"game": "blackjack", "state_version": version, "computed_at": time.time(),
                 "status": "idle", "suppressed_reasons": [], "warnings": [], "facts": []}
    if not snap["in_hand"]:
        out["message"] = "No hand in progress."
        return out
    dealer_vals, dprob = _problem_slots(snap["dealer"], "Dealer")
    hands = []
    for h in snap["hands"]:
        vals, prob = _problem_slots(h["cards"], f"Hand {h['index'] + 1}")
        hands.append((h, vals, prob))
    played = [x for x in hands if x[1]]
    deciding = any(snap["buttons"].values())
    out["status"] = "decision" if deciding else "observing"

    up = dealer_vals[0] if dealer_vals else None
    up_card = Card.parse(up) if up and up != "hidden" else None
    out["dealer"] = {"cards": dealer_vals, "upcard": up}
    if up_card is None:
        out["suppressed_reasons"].append("dealer upcard not recognized")
    out["suppressed_reasons"] += dprob[:1]

    # which hand is being decided?
    active = [x for x in played if x[0]["active"]]
    if len(played) == 1 and not active:
        active = played
    if not played:
        out["suppressed_reasons"].append("no player cards recognized")
    elif len(active) != 1:
        out["suppressed_reasons"].append("cannot tell which split hand is active")
    out["hands"] = [{"index": h["index"], "cards": vals, "bet": h["bet"]["value"],
                     "total": hand_total([Card.parse(c).bj_value for c in vals])[0] if vals and "hidden" not in vals else None,
                     "soft": hand_total([Card.parse(c).bj_value for c in vals])[1] if vals and "hidden" not in vals else None,
                     "problems": prob} for h, vals, prob in played]
    out["composition"] = snap["composition"]
    if not deciding:
        out["message"] = "Waiting for a player decision (no action buttons lit)."
        return out
    if active:
        h, vals, prob = active[0]
        out["suppressed_reasons"] += prob
        if len(vals) < 2:
            out["suppressed_reasons"].append(f"hand {h['index'] + 1} has fewer than two recognized cards")
    if out["suppressed_reasons"]:
        out["status"] = "suppressed"
        return out

    h, vals, _ = active[0]
    cards = [Card.parse(c) for c in vals]
    values = [c.bj_value for c in cards]
    num_hands = len(played)
    from_split = num_hands > 1
    ctx = HandContext(values=values, ranks=[c.rank for c in cards], num_hands=num_hands,
                      from_split=from_split, split_aces=from_split and values[0] == 1)
    total, soft = hand_total(values)
    legal = legal_actions(ctx, rules)
    upv = up_card.bj_value
    out["decision"] = {"hand_index": h["index"], "cards": vals, "total": total, "soft": soft,
                       "dealer_upcard": up, "from_split": from_split}
    out["legal"] = legal

    # screen vs rules consistency
    for a, lit in snap["buttons"].items():
        if lit and legal.get(a) != "legal":
            out["warnings"].append(f"'{a}' button is lit but rules say: {legal.get(a)}. Check the rule settings.")
        if not lit and legal.get(a) == "legal":
            out["warnings"].append(f"rules allow '{a}' but its button is not lit; treating it as unavailable.")
    available = {a: ("legal" if legal.get(a) == "legal" and snap["buttons"].get(a) else
                     (legal.get(a) if legal.get(a) != "legal" else "not offered on screen")) for a in legal}

    # EV model
    comp = snap["composition"]
    if comp["mode"] == "known":
        p = probs_from_counts(comp["remaining"])
        model = "composition"
        model_label = "draw probabilities frozen at the observed remaining-shoe composition (approximation: no further removal within the hand)"
    else:
        p = INFINITE_DECK
        model = "infinite_deck"
        model_label = ("continuous shuffler: infinite-deck model" if comp["mode"] == "csm"
                       else "shoe composition unknown: infinite-deck model (each rank 1/13)")
    m = EVModel(upv, p, rules)
    evr = m.evaluate(values, available)
    ev = {a: round(v, 4) for a, v in evr["ev"].items()}
    best_ev = max(ev, key=ev.get) if ev else None
    dist = {str(k): round(v, 4) for k, v in m.dist.items() if k in (17, 18, 19, 20, 21, "bust")}

    supported, why = chart_supported(rules)
    if supported:
        rec = recommend(ctx, upv, rules)
        action = rec["action"]
        if available.get(action) != "legal":
            # chart action not offered on screen: fall back to best available by EV
            out["warnings"].append(f"chart action '{action}' is not available on screen; showing best available action by EV")
            action, source = best_ev, "ev_model"
        else:
            source = "chart"
        recommendation = {"action": action, "source": source, "code": rec["code"], "trail": rec["trail"],
                          "caveat": "Fixed basic-strategy chart for 4-8 decks; good for typical rules, not optimal for every composition."}
    else:
        recommendation = {"action": best_ev, "source": "ev_model", "code": None,
                          "trail": [f"chart not applicable: {why}", f"highest EV action under the {model} model"],
                          "caveat": "Rule configuration not covered by the chart; recommendation is the EV-maximising action under the stated model."}
    if best_ev and recommendation["action"] != best_ev and ev:
        diff = ev[best_ev] - ev[recommendation["action"]]
        recommendation["model_disagrees"] = {"best_ev_action": best_ev, "ev_difference": round(diff, 4)}
    out["recommendation"] = recommendation
    out["ev"] = {
        "actions": ev, "units": "expected net result per 1 unit initial wager (doubles/splits include the extra stake)",
        "basis": evr["basis"], "model": model, "model_label": model_label,
        "assumptions": [
            "after this decision, play continues with the total-dependent optimal strategy under the same model",
            "split EV = 2 x one-hand EV; resplits not modeled" + (" (lower bound: resplitting allowed)" if rules.max_split_hands > 2 else ""),
            "insurance / even money not evaluated",
            "values are long-run averages; any single hand can win or lose",
        ],
    }
    out["dealer_outcomes"] = {"distribution": dist, "basis": "conditional on no dealer blackjack" if m.p_bj else "upcard cannot make blackjack"}
    out["status"] = "decision"
    out["facts"] = _bj_facts(out)
    return out


def _bj_facts(a: dict) -> list[dict]:
    d = a["decision"]
    facts = [{"name": "player total", "value": d["total"]},
             {"name": "dealer upcard", "value": d["dealer_upcard"]},
             {"name": "recommended action", "value": a["recommendation"]["action"]}]
    for act, v in a["ev"]["actions"].items():
        facts.append({"name": f"EV {act}", "value": v})
    for k, v in a["dealer_outcomes"]["distribution"].items():
        facts.append({"name": f"dealer final {k} probability", "value": v})
    for c in d["cards"]:
        facts.append({"name": "player card", "value": c})
    comp = a.get("composition", {})
    if comp.get("mode") == "known":
        facts.append({"name": "Hi-Lo true count", "value": comp["hi_lo_true"]})
        facts.append({"name": "cards seen this shoe", "value": comp["cards_seen"]})
    return facts


# --------------------------------------------------------------------- poker

def poker_inputs(snap: dict, settings: PokerSettings) -> dict:
    """Validate state and gather equity inputs. Returns dict with 'problems' if unusable."""
    hero_vals, hp = _problem_slots(snap["hero"], "Hero")
    board_vals, bp = [], []
    for s in snap["board"]:
        v, raw = s["value"], s.get("raw")
        if s["status"] == "uncertain":
            bp.append(f"Board: {s['region']} is uncertain (correct it manually)")
        elif s["status"] != "manual" and raw != v and not (v is None and raw == "empty"):
            bp.append(f"Board: {s['region']} reading changing ({v} -> {raw or 'unreadable'})")
        elif v not in (None, "empty"):
            board_vals.append(v)
    problems = hp + bp
    if len(hero_vals) != 2:
        problems.append("hero hole cards not both recognized")
    problems += PokerTracker.validate_cards(hero_vals, board_vals)
    opps = [s for s in snap["seats"] if s["has_cards"]]
    if not opps:
        problems.append("no active opponents detected")
    ranges = {}
    for s in opps:
        ranges[s["seat"]] = settings.seat_ranges.get(str(s["seat"]), settings.default_range)
    return {"hero": hero_vals, "board": board_vals, "ranges": ranges, "problems": problems}


_EQUITY_CACHE: dict[tuple, object] = {}


def _cached_equity(hero: tuple, board: tuple, ranges: tuple, samples: int, budget: float):
    """Same inputs -> same estimate, so identical states don't show Monte Carlo jitter."""
    key = (hero, board, ranges, samples)
    if key not in _EQUITY_CACHE:
        if len(_EQUITY_CACHE) > 256:
            _EQUITY_CACHE.clear()
        _EQUITY_CACHE[key] = equity([Card.parse(c) for c in hero], [Card.parse(c) for c in board],
                                    [parse_range(r) for r in ranges], samples=samples, time_budget_s=budget)
    return _EQUITY_CACHE[key]


def analyze_holdem(snap: dict, settings: PokerSettings, version: int) -> dict:
    out: dict = {"game": "holdem", "state_version": version, "computed_at": time.time(),
                 "status": "idle", "suppressed_reasons": [], "warnings": [], "facts": []}
    if not snap["in_hand"]:
        out["message"] = "No hand in progress."
        return out
    inp = poker_inputs(snap, settings)
    out["inputs"] = {k: v for k, v in inp.items() if k != "problems"}
    out["street"] = snap["street"]
    out["position"] = snap["position"]
    if inp["problems"]:
        out["status"] = "suppressed"
        out["suppressed_reasons"] = inp["problems"]
        return out
    hero = [Card.parse(c) for c in inp["hero"]]
    board = [Card.parse(c) for c in inp["board"]]
    if board:
        out["hand_strength"] = {"made_hand": category(evaluate(hero + board)), "board_cards": len(board)}
    else:
        out["hand_strength"] = {"made_hand": "Pair" if hero[0].rank == hero[1].rank else "High card (preflop)",
                                "board_cards": 0}
    try:
        res = _cached_equity(tuple(inp["hero"]), tuple(inp["board"]), tuple(inp["ranges"].values()),
                             settings.mc_samples, settings.time_budget_s)
    except ValueError as e:
        out["status"] = "suppressed"
        out["suppressed_reasons"] = [f"equity not computable: {e}"]
        return out
    out["equity"] = res.as_dict() | {
        "opponents": [{"seat": s, "range": r} for s, r in inp["ranges"].items()],
        "assumptions": [
            "showdown equity: every active opponent sees all remaining cards, no folds and no further betting",
            "opponent hands are uniformly distributed over the configured ranges (user assumption, not observed)",
            "card removal applied for hero and board cards",
            "this is not a betting strategy and not the EV of any action",
        ],
    }
    pot = snap["pot"]["value"]
    to_call = snap["to_call"]["value"] if snap["buttons"].get("call") else 0
    amounts = [snap["pot"], snap["to_call"], snap["hero_bet"]] + [s["bet"] for s in snap["seats"]]
    shaky = [a["region"] for a in amounts if a["status"] == "uncertain" or a.get("changing")]
    if snap["buttons"].get("call") and snap["to_call"]["value"] is None:
        shaky.append("to_call")
    if shaky:
        out["warnings"].append(f"pot odds not computed: amounts uncertain or changing in {', '.join(sorted(set(shaky)))}")
    elif pot is not None and to_call:
        extra = 0 if settings.pot_includes_current_bets else sum(
            (s["bet"]["value"] or 0) for s in snap["seats"]) + (snap["hero_bet"]["value"] or 0)
        po = pot_odds(pot + extra, to_call)
        po["pot_displayed"] = pot
        po["current_bets_added"] = extra
        all_in = [s["seat"] for s in snap["seats"] if s["has_cards"] and s["stack"]["value"] == 0]
        if all_in and len(inp["ranges"]) > 1:
            po["warning"] = f"seat(s) {all_in} appear all-in: side pots likely; pot odds above ignore them"
        out["pot_odds"] = po
        out["comparison"] = {
            "equity": res.equity, "break_even_equity": po["break_even_equity"],
            "statement": ("Estimated showdown equity is above the break-even equity for this call"
                          if res.equity > po["break_even_equity"] else
                          "Estimated showdown equity is below the break-even equity for this call"),
            "caveat": "Immediate pot odds only. Ignores future betting, implied odds, fold equity, rake and range accuracy; not a recommendation to call.",
        }
    elif pot is None:
        out["warnings"].append("pot amount not recognized; pot odds not computed")
    out["status"] = "analysis"
    out["facts"] = _poker_facts(out, inp)
    return out


def _poker_facts(a: dict, inp: dict) -> list[dict]:
    e = a["equity"]
    facts = [{"name": "hero cards", "value": " ".join(inp["hero"])},
             {"name": "board", "value": " ".join(inp["board"]) or "none"},
             {"name": "street", "value": a["street"]},
             {"name": "made hand", "value": a["hand_strength"]["made_hand"]},
             {"name": "showdown equity", "value": round(e["equity"], 4)},
             {"name": "win probability", "value": round(e["win"], 4)},
             {"name": "tie probability", "value": round(e["tie"], 4)},
             {"name": "equity method", "value": e["method"]},
             {"name": "samples", "value": e["samples"]},
             {"name": "standard error", "value": round(e["std_error"], 4)},
             {"name": "95% interval low", "value": round(e["ci95"][0], 4)},
             {"name": "95% interval high", "value": round(e["ci95"][1], 4)},
             {"name": "active opponents", "value": len(inp["ranges"])}]
    for s, r in inp["ranges"].items():
        facts.append({"name": f"seat {s} assumed range", "value": r})
    if "pot_odds" in a:
        po = a["pot_odds"]
        facts += [{"name": "amount to call", "value": po["to_call"]},
                  {"name": "pot before call", "value": po["pot_before_call"]},
                  {"name": "break-even equity", "value": round(po["break_even_equity"], 4)}]
    if a["position"].get("label"):
        facts.append({"name": "hero position", "value": a["position"]["label"]})
    return facts
