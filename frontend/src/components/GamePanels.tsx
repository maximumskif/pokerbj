import { useState } from "react";
import { Json, pct, signed } from "../api";
import { CardSlot, CardText } from "./Card";

const ACTION_LABEL: Record<string, string> = { hit: "Hit", stand: "Stand", double: "Double", split: "Split", surrender: "Surrender" };

function Suppressed({ reasons, warnings }: { reasons: string[]; warnings?: string[] }) {
  return (
    <>
      {reasons?.length > 0 && (
        <div className="suppressed">
          <b>Recommendation suppressed</b> — required information is missing, uncertain or changing:
          <ul>{reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>
          <span className="small">Click the region in the preview to correct it manually.</span>
        </div>
      )}
      {warnings && warnings.length > 0 && (
        <div className="warnbox">
          <ul>{warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>
        </div>
      )}
    </>
  );
}

function Expand({ title, children }: { title: string; children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="expand">
      <button className="ghost expand-btn" onClick={() => setOpen(!open)}>
        {open ? "▾" : "▸"} {title}
      </button>
      {open && <div className="expand-body">{children}</div>}
    </div>
  );
}

// ------------------------------------------------------------------ blackjack

export function BlackjackHand({ game, onCard }: { game: Json; onCard: (id: string) => void }) {
  if (!game?.dealer) return <div className="muted">Waiting for frames…</div>;
  const row = (slots: Json[]) =>
    slots.map((s: Json) => <CardSlot key={s.region} value={s.value} status={s.status} confidence={s.confidence} source={s.source} onClick={() => onCard(s.region)} />);
  return (
    <div className="bj-table">
      <div className="hand-row">
        <span className="hand-label">Dealer</span>
        <div className="cards">{row(game.dealer)}</div>
      </div>
      {game.hands.map((h: Json) =>
        h.cards.some((c: Json) => c.value && c.value !== "empty") || h.index === 0 ? (
          <div key={h.index} className={`hand-row${h.active ? " active" : ""}`}>
            <span className="hand-label">
              {game.hands[1].cards.some((c: Json) => c.value && c.value !== "empty") ? `Hand ${h.index + 1}` : "Player"}
              {h.active && <span className="pill on">active</span>}
            </span>
            <div className="cards">{row(h.cards)}</div>
            <span className="bet">bet {h.bet.value ?? "—"}</span>
          </div>
        ) : null,
      )}
      <div className="meta-row">
        <span>Bankroll <b>{game.bankroll?.value ?? "—"}</b></span>
        <span>
          On-screen actions:{" "}
          {Object.entries(game.buttons).filter(([, v]) => v).map(([k]) => ACTION_LABEL[k]).join(", ") || "none lit"}
        </span>
        <span>Hand #{game.hand_id ?? "—"}</span>
      </div>
    </div>
  );
}

export function BlackjackAnalysis({ a }: { a: Json }) {
  if (!a || a.game !== "blackjack") return null;
  const comp = a.composition;
  return (
    <>
      <section className="panel">
        <div className="panel-head">
          <h2>Strategy</h2>
          <span className="muted small">state v{a.state_version}</span>
        </div>
        {a.status === "decision" && a.recommendation ? (
          <>
            <div className="rec">
              <div className="rec-action">{ACTION_LABEL[a.recommendation.action] ?? a.recommendation.action}</div>
              <div className="rec-meta">
                <div>
                  {a.decision.cards.map((c: string) => <CardText key={c} value={c} />)} = {a.decision.soft ? "soft " : ""}
                  {a.decision.total} vs <CardText value={a.decision.dealer_upcard} />
                  {a.decision.from_split && <span className="pill">split hand</span>}
                </div>
                <div className="small muted">
                  Source: {a.recommendation.source === "chart" ? `basic-strategy chart (code ${a.recommendation.code})` : "EV model (chart not applicable)"}
                </div>
              </div>
            </div>
            <p className="caveat">{a.recommendation.caveat}</p>
            {a.recommendation.model_disagrees && (
              <div className="warnbox small">
                The EV model prefers <b>{a.recommendation.model_disagrees.best_ev_action}</b> by {a.recommendation.model_disagrees.ev_difference.toFixed(4)} units under its assumptions.
              </div>
            )}
            <Expand title="Why (audit trail)">
              <ol className="small">{a.recommendation.trail.map((t: string, i: number) => <li key={i}>{t}</li>)}</ol>
              <table className="kv small">
                <tbody>
                  {Object.entries(a.legal).map(([k, v]) => (
                    <tr key={k}><td>{ACTION_LABEL[k]}</td><td className={v === "legal" ? "ok" : "muted"}>{String(v)}</td></tr>
                  ))}
                </tbody>
              </table>
            </Expand>
          </>
        ) : (
          <div className="muted">{a.message ?? (a.status === "suppressed" ? "" : "No decision pending.")}</div>
        )}
        <Suppressed reasons={a.suppressed_reasons} warnings={a.warnings} />
      </section>

      {a.status === "decision" && a.ev && (
        <section className="panel">
          <div className="panel-head">
            <h2>Expected value</h2>
            <span className="pill">{a.ev.model === "composition" ? "shoe composition" : "infinite deck"}</span>
          </div>
          <EvBars ev={a.ev.actions} best={a.recommendation?.action} />
          <p className="small muted">
            {a.ev.units}; {a.ev.basis}.
          </p>
          <h4>Dealer final outcome ({a.dealer_outcomes.basis})</h4>
          <div className="dist">
            {Object.entries(a.dealer_outcomes.distribution).map(([k, v]) => (
              <div key={k} className="dist-col" title={`${k}: ${pct(v as number, 2)}`}>
                <div className="dist-bar" style={{ height: `${(v as number) * 100}%` }} />
                <span>{k}</span>
                <span className="small">{pct(v as number, 1)}</span>
              </div>
            ))}
          </div>
          <Expand title="Model & assumptions">
            <p className="small">{a.ev.model_label}</p>
            <ul className="small">{a.ev.assumptions.map((x: string, i: number) => <li key={i}>{x}</li>)}</ul>
          </Expand>
        </section>
      )}

      {comp && (
        <section className="panel">
          <div className="panel-head">
            <h2>Shoe</h2>
            <span className={`pill ${comp.mode === "known" ? "on" : comp.mode === "unknown" ? "warn" : ""}`}>{comp.mode}</span>
          </div>
          <p className="small">{comp.label}</p>
          {comp.mode === "known" && (
            <>
              <div className="shoe-grid">
                {[1, 2, 3, 4, 5, 6, 7, 8, 9, 10].map((v) => (
                  <div key={v}>
                    <b>{v === 1 ? "A" : v === 10 ? "T" : v}</b>
                    <span>{comp.remaining[v]}</span>
                  </div>
                ))}
              </div>
              <p className="small muted">
                Cards seen {comp.cards_seen} · Hi-Lo running {comp.hi_lo_running} · true {comp.hi_lo_true} (informational)
              </p>
            </>
          )}
          {comp.reason && <p className="small muted">Reason: {comp.reason}</p>}
        </section>
      )}
    </>
  );
}

function EvBars({ ev, best }: { ev: Record<string, number>; best?: string }) {
  const max = Math.max(1, ...Object.values(ev).map((v) => Math.abs(v)));
  return (
    <div className="evbars">
      {Object.entries(ev)
        .sort((a, b) => b[1] - a[1])
        .map(([k, v]) => (
          <div key={k} className={`evrow${k === best ? " best" : ""}`}>
            <span className="evlabel">{ACTION_LABEL[k]}</span>
            <div className="evtrack">
              <div className="evzero" />
              <div className={`evfill ${v >= 0 ? "pos" : "neg"}`} style={{ width: `${(Math.abs(v) / max) * 50}%`, left: v >= 0 ? "50%" : `${50 - (Math.abs(v) / max) * 50}%` }} />
            </div>
            <span className="evval mono">{signed(v)}</span>
          </div>
        ))}
    </div>
  );
}

// ------------------------------------------------------------------ hold'em

export function HoldemHand({ game, onCard }: { game: Json; onCard: (id: string) => void }) {
  if (!game?.hero) return <div className="muted">Waiting for frames…</div>;
  const row = (slots: Json[]) =>
    slots.map((s: Json) => <CardSlot key={s.region} value={s.value} status={s.status} confidence={s.confidence} source={s.source} onClick={() => onCard(s.region)} />);
  return (
    <div className="bj-table">
      <div className="hand-row">
        <span className="hand-label">Board <span className="pill">{game.street ?? "—"}</span></span>
        <div className="cards">{row(game.board)}</div>
        <span className="bet">pot {game.pot?.value ?? "—"}</span>
      </div>
      <div className="hand-row active">
        <span className="hand-label">Hero {game.position?.label && <span className="pill on">{game.position.label}</span>}</span>
        <div className="cards">{row(game.hero)}</div>
        <span className="bet">stack {game.hero_stack?.value ?? "—"}</span>
      </div>
      <div className="seats">
        {game.seats.map((s: Json) => (
          <div key={s.seat} className={`seat${s.has_cards ? " live" : ""}`}>
            <b>Seat {s.seat}</b>
            {s.dealer && <span className="pill">D</span>}
            <span className="small">{s.has_cards ? "in hand" : "no cards"}</span>
            <span className="small">stack {s.stack.value ?? "—"}</span>
            <span className="small">bet {s.bet.value ?? "—"}</span>
          </div>
        ))}
      </div>
      <div className="meta-row">
        <span>To call <b>{game.buttons?.call ? game.to_call?.value ?? "?" : 0}</b></span>
        <span>On-screen: {Object.entries(game.buttons ?? {}).filter(([, v]) => v).map(([k]) => k).join(", ") || "none lit"}</span>
        <span>Hand #{game.hand_id ?? "—"}</span>
      </div>
    </div>
  );
}

export function HoldemAnalysis({ a, game }: { a: Json; game: Json }) {
  if (!a || a.game !== "holdem") return null;
  const e = a.equity;
  return (
    <>
      <section className="panel">
        <div className="panel-head">
          <h2>Equity</h2>
          <span className="muted small">state v{a.state_version}</span>
        </div>
        {a.status === "analysis" && e ? (
          <>
            <div className="rec">
              <div className="rec-action">{pct(e.equity)}</div>
              <div className="rec-meta">
                <div>showdown equity vs {e.opponents.length} opponent{e.opponents.length > 1 ? "s" : ""}</div>
                <div className="small muted">
                  {e.method === "exact" ? `exact enumeration of ${e.samples.toLocaleString()} outcomes` : `Monte Carlo, ${e.samples.toLocaleString()} samples, 95% CI ${pct(e.ci95[0])}–${pct(e.ci95[1])} (±${pct(1.96 * e.std_error)})`}
                </div>
              </div>
            </div>
            <div className="stats">
              <div><span>Made hand</span><b>{a.hand_strength.made_hand}</b></div>
              <div><span>Win</span><b>{pct(e.win)}</b></div>
              <div><span>Tie</span><b>{pct(e.tie)}</b></div>
              <div><span>Position</span><b>{a.position?.label ?? "—"}</b></div>
            </div>
            <Expand title="Opponent ranges (your assumptions, not observations)">
              <ul className="small">
                {e.opponents.map((o: Json, i: number) => (
                  <li key={o.seat}>Seat {o.seat}: <span className="mono">{o.range}</span> — share {pct(e.villain_equity[i])}</li>
                ))}
              </ul>
              <ul className="small muted">{e.assumptions.map((x: string, i: number) => <li key={i}>{x}</li>)}</ul>
            </Expand>
          </>
        ) : (
          <div className="muted">{a.message ?? ""}</div>
        )}
        <Suppressed reasons={a.suppressed_reasons} warnings={a.warnings} />
      </section>
      {a.pot_odds && (
        <section className="panel">
          <div className="panel-head"><h2>Pot odds</h2></div>
          <div className="stats">
            <div><span>To call</span><b>{a.pot_odds.to_call}</b></div>
            <div><span>Pot before call</span><b>{a.pot_odds.pot_before_call}</b></div>
            <div><span>Odds</span><b>{a.pot_odds.odds_ratio}</b></div>
            <div><span>Break-even equity</span><b>{pct(a.pot_odds.break_even_equity)}</b></div>
          </div>
          <div className="compare">
            <div className="cmp-track">
              <div className="cmp-eq" style={{ width: `${a.comparison.equity * 100}%` }} />
              <div className="cmp-be" style={{ left: `${a.comparison.break_even_equity * 100}%` }} title="break-even" />
            </div>
            <p className="small">{a.comparison.statement}.</p>
            <p className="caveat">{a.comparison.caveat}</p>
            {a.pot_odds.warning && <div className="warnbox small">{a.pot_odds.warning}</div>}
          </div>
          <Expand title="Pot-odds assumptions">
            <p className="small">Displayed pot {a.pot_odds.pot_displayed} + current bets {a.pot_odds.current_bets_added}.</p>
            <ul className="small">{a.pot_odds.assumptions.map((x: string, i: number) => <li key={i}>{x}</li>)}</ul>
          </Expand>
          <p className="small muted">No bet sizing or action EV is shown: that would need a model of future betting, opponent behaviour, rake and side pots.</p>
        </section>
      )}
      {game?.history?.length > 0 && (
        <section className="panel">
          <div className="panel-head"><h2>Observed betting</h2></div>
          <ul className="history small">
            {game.history.map((h: Json, i: number) => (
              <li key={i}><span className="pill">{h.street}</span> {h.text}</li>
            ))}
          </ul>
        </section>
      )}
    </>
  );
}
