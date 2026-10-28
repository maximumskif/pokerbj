import { useEffect, useState } from "react";
import { api, Json } from "../api";

interface Props {
  state: Json;
  onClose: () => void;
  notify: (m: string, e?: boolean) => void;
  draft: Json | null;
  setDraft: (l: Json | null) => void;
  drawing: string | null;
  setDrawing: (id: string | null) => void;
}

const TABS = ["Rules", "Poker", "Capture", "Calibration", "Claude", "Privacy"] as const;

export function SettingsDrawer({ state, onClose, notify, draft, setDraft, drawing, setDrawing }: Props) {
  const [tab, setTab] = useState<(typeof TABS)[number]>("Rules");
  const [s, setS] = useState<Json>(null);
  useEffect(() => {
    api.get("/api/settings").then(setS);
  }, []);
  if (!s) return null;

  const save = async (next: Json) => {
    try {
      const r = await api.put("/api/settings", next);
      setS(r);
      notify("Settings saved");
    } catch (e) {
      notify(String(e), true);
    }
  };
  const upd = (path: string[], value: unknown) => {
    const next = structuredClone(s);
    let o = next;
    for (const k of path.slice(0, -1)) o = o[k];
    o[path[path.length - 1]] = value;
    setS(next);
  };

  return (
    <aside className="drawer">
      <div className="panel-head">
        <h2>Settings</h2>
        <button className="ghost" onClick={onClose}>✕</button>
      </div>
      <div className="tabs">
        {TABS.map((t) => (
          <button key={t} className={t === tab ? "tab on" : "tab"} onClick={() => setTab(t)}>{t}</button>
        ))}
      </div>
      <div className="drawer-body">
        {tab === "Rules" && <RulesTab s={s} upd={upd} save={save} />}
        {tab === "Poker" && <PokerTab s={s} upd={upd} save={save} state={state} />}
        {tab === "Capture" && <CaptureTab s={s} upd={upd} save={save} />}
        {tab === "Calibration" && (
          <CalibrationTab s={s} save={save} state={state} notify={notify} draft={draft} setDraft={setDraft} drawing={drawing} setDrawing={setDrawing} />
        )}
        {tab === "Claude" && <ClaudeTab state={state} notify={notify} />}
        {tab === "Privacy" && <PrivacyTab s={s} upd={upd} save={save} notify={notify} />}
      </div>
    </aside>
  );
}

type TabProps = { s: Json; upd: (p: string[], v: unknown) => void; save: (n: Json) => void };

function Num({ label, value, onChange, step = 1, min, max, hint }: { label: string; value: number; onChange: (v: number) => void; step?: number; min?: number; max?: number; hint?: string }) {
  return (
    <label className="field">
      <span>{label}</span>
      <input type="number" value={value} step={step} min={min} max={max} onChange={(e) => onChange(parseFloat(e.target.value))} />
      {hint && <em>{hint}</em>}
    </label>
  );
}

function Check({ label, value, onChange, hint }: { label: string; value: boolean; onChange: (v: boolean) => void; hint?: string }) {
  return (
    <label className="field chk">
      <input type="checkbox" checked={value} onChange={(e) => onChange(e.target.checked)} />
      <span>{label}</span>
      {hint && <em>{hint}</em>}
    </label>
  );
}

function Sel({ label, value, options, onChange }: { label: string; value: string; options: [string, string][]; onChange: (v: string) => void }) {
  return (
    <label className="field">
      <span>{label}</span>
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        {options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
    </label>
  );
}

function RulesTab({ s, upd, save }: TabProps) {
  const r = s.rules;
  const set = (k: string) => (v: unknown) => upd(["rules", k], v);
  return (
    <div className="form">
      <p className="small muted">Match these to the table. If the on-screen buttons disagree with these rules, the dashboard warns you.</p>
      <Num label="Decks" value={r.decks} min={1} max={8} onChange={set("decks")} />
      <Check label="Dealer hits soft 17 (H17)" value={r.dealer_hits_soft_17} onChange={set("dealer_hits_soft_17")} />
      <Sel label="Blackjack pays" value={String(r.blackjack_payout)} options={[["1.5", "3:2"], ["1.2", "6:5"], ["1", "1:1"]]} onChange={(v) => set("blackjack_payout")(parseFloat(v))} />
      <Sel label="Double down on" value={r.double_on} options={[["any_two", "any two cards"], ["9_11", "hard 9–11"], ["10_11", "hard 10–11"]]} onChange={set("double_on")} />
      <Check label="Double after split (DAS)" value={r.double_after_split} onChange={set("double_after_split")} />
      <Sel label="Surrender" value={r.surrender} options={[["late", "late surrender"], ["none", "not offered"]]} onChange={set("surrender")} />
      <Num label="Max hands after splitting" value={r.max_split_hands} min={1} max={4} onChange={set("max_split_hands")} />
      <Check label="Resplit aces" value={r.resplit_aces} onChange={set("resplit_aces")} />
      <Check label="Hit split aces" value={r.hit_split_aces} onChange={set("hit_split_aces")} />
      <Check label="Split unlike ten-value cards (e.g. K+Q)" value={r.split_tens_by_value} onChange={set("split_tens_by_value")} />
      <Check label="Dealer peeks for blackjack (hole card)" value={r.dealer_peeks} onChange={set("dealer_peeks")} hint="Off = European no-hole-card; EVs become unconditional and the chart is marked unsupported." />
      {!r.dealer_peeks && <Check label="No-hole-card: lose only original bet to dealer blackjack (OBO)" value={r.enhc_original_bets_only} onChange={set("enhc_original_bets_only")} />}
      <Check label="Continuous shuffling machine" value={r.continuous_shuffle} onChange={set("continuous_shuffle")} hint="History is ignored; every hand uses a full-shoe model." />
      <button className="primary" onClick={() => save(s)}>Apply rules</button>
      <p className="small muted">The bundled chart covers 4–8 decks with dealer peek. Other configurations use the EV model and are labelled as such. Shoe history is reset with a shuffle notice or the button below.</p>
      <button onClick={() => api.post("/api/shoe/new")}>Mark fresh shoe now</button>
    </div>
  );
}

function PokerTab({ s, upd, save, state }: TabProps & { state: Json }) {
  const p = s.poker;
  const seats: number[] = state?.game?.seats?.map((x: Json) => x.seat) ?? [1, 2, 3, 4, 5];
  return (
    <div className="form">
      <p className="small muted">Opponent ranges are your assumptions. Syntax: <span className="mono">QQ+, AKs, AQo+, 77-99, A5s-A2s, KQ, random</span></p>
      <label className="field">
        <span>Default range for every active opponent</span>
        <input value={p.default_range} onChange={(e) => upd(["poker", "default_range"], e.target.value)} />
      </label>
      {seats.map((seat) => (
        <label key={seat} className="field">
          <span>Seat {seat} override</span>
          <input
            value={p.seat_ranges[String(seat)] ?? ""}
            placeholder="(use default)"
            onChange={(e) => {
              const next = { ...p.seat_ranges };
              if (e.target.value.trim()) next[String(seat)] = e.target.value;
              else delete next[String(seat)];
              upd(["poker", "seat_ranges"], next);
            }}
          />
        </label>
      ))}
      <Num label="Monte Carlo samples" value={p.mc_samples} step={1000} min={1000} max={500000} onChange={(v) => upd(["poker", "mc_samples"], v)} />
      <Num label="Time budget per calculation (s)" value={p.time_budget_s} step={0.5} min={0.2} max={20} onChange={(v) => upd(["poker", "time_budget_s"], v)} />
      <Check label="Displayed pot already includes this street's bets" value={p.pot_includes_current_bets} onChange={(v) => upd(["poker", "pot_includes_current_bets"], v)} />
      <button className="primary" onClick={() => save(s)}>Apply</button>
    </div>
  );
}

function CaptureTab({ s, upd, save }: TabProps) {
  const c = s.capture;
  const set = (k: string) => (v: unknown) => upd(["capture", k], v);
  return (
    <div className="form">
      <Num label="Capture interval (s)" value={c.interval_s} step={0.1} min={0.05} max={10} onChange={set("interval_s")} />
      <Num label="Replay speed (×)" value={c.replay_speed} step={0.5} min={0.1} max={20} onChange={set("replay_speed")} />
      <Num label="Change threshold (fraction of pixels)" value={c.change_threshold} step={0.0005} min={0} max={0.5} onChange={set("change_threshold")} hint="Unchanged frames are skipped once all regions are settled." />
      <Num label="Stable frames required" value={c.stable_frames} min={1} max={10} onChange={set("stable_frames")} hint="Consecutive identical confident readings before a value is committed." />
      <Num label="Minimum recognition confidence" value={c.min_confidence} step={0.05} min={0.1} max={0.99} onChange={set("min_confidence")} />
      <Num label="Flag uncertain after N unsettled frames" value={c.uncertain_after} min={2} max={50} onChange={set("uncertain_after")} />
      <Num label="Stale after (s without frames)" value={c.stale_after_s} step={0.5} onChange={set("stale_after_s")} />
      <Num label="Observation gap that invalidates shoe history (s)" value={c.gap_break_s} onChange={set("gap_break_s")} />
      <button className="primary" onClick={() => save(s)}>Apply</button>
      <p className="small muted">Changing recognition parameters resets the pipeline (and shoe history).</p>
    </div>
  );
}

function CalibrationTab({ s, save, state, notify, draft, setDraft, drawing, setDrawing }: { s: Json; save: (n: Json) => void; state: Json; notify: (m: string, e?: boolean) => void; draft: Json | null; setDraft: (l: Json | null) => void; drawing: string | null; setDrawing: (id: string | null) => void }) {
  const [layouts, setLayouts] = useState<Json[]>([]);
  const [copyName, setCopyName] = useState("my_table");
  const [newId, setNewId] = useState("");
  const [newKind, setNewKind] = useState("card");
  const [tplRegion, setTplRegion] = useState("");
  const [tplValue, setTplValue] = useState("");
  const load = () => api.get("/api/layouts").then(setLayouts);
  useEffect(() => {
    load();
  }, []);
  const current = s.layout;
  const startEdit = async () => setDraft(await api.get(`/api/layouts/${current}`));

  const copy = async () => {
    try {
      const base = draft ?? (await api.get(`/api/layouts/${current}`));
      const lay = { ...base, name: copyName, synthetic: false };
      await api.put(`/api/layouts/${copyName}`, lay);
      await save({ ...s, layout: copyName, game: lay.game });
      setDraft(lay);
      load();
      notify(`Layout copied to ${copyName} and selected`);
    } catch (e) {
      notify(String(e), true);
    }
  };

  const saveDraft = async () => {
    try {
      await api.put(`/api/layouts/${draft.name}`, draft);
      notify("Layout saved. Pipeline reset to apply it.");
      setDrawing(null);
    } catch (e) {
      notify(String(e), true);
    }
  };

  const label = async () => {
    try {
      const r = await api.post("/api/calibrate/template", { region_id: tplRegion, value: tplValue.replace(/^10/, "T") });
      notify(`Saved ${r.saved.join(", ")}`);
    } catch (e) {
      notify(String(e), true);
    }
  };

  return (
    <div className="form">
      <Sel label="Active table layout" value={current} options={layouts.map((l) => [l.name, `${l.name} (${l.game}${l.synthetic ? ", synthetic" : ""})`])} onChange={(v) => save({ ...s, layout: v, game: layouts.find((l) => l.name === v)?.game ?? s.game })} />
      <div className="row gap">
        <input value={copyName} onChange={(e) => setCopyName(e.target.value)} className="grow" />
        <button onClick={copy}>Copy as new layout</button>
      </div>
      <p className="small muted">
        Workflow for a real table: select the window as source, capture one frame, copy a layout for the same game, use the frame size, then draw each region on the preview (pick a region, drag a box). Save, then label a few card/number crops so recognition learns the table's glyphs.
      </p>
      {!draft && <button onClick={startEdit}>Edit regions of “{current}”</button>}
      {draft && (
        <>
          {draft.synthetic && <div className="warnbox small">Synthetic layouts are read-only; copy first to save changes.</div>}
          <div className="row gap small">
            <span>Size {draft.width}×{draft.height}</span>
            <button
              onClick={() => {
                const img = document.querySelector(".preview img") as HTMLImageElement | null;
                if (img?.naturalWidth) setDraft({ ...draft, width: img.naturalWidth, height: img.naturalHeight });
              }}
            >
              Use current frame size
            </button>
          </div>
          <div className="region-list">
            {draft.regions.map((r: Json, i: number) => (
              <div key={r.id} className={`region-item${drawing === r.id ? " on" : ""}`}>
                <span className="mono">{r.id}</span>
                <span className="muted small">{r.kind}</span>
                <span className="mono small">{r.rect.x},{r.rect.y} {r.rect.w}×{r.rect.h}</span>
                <button onClick={() => setDrawing(drawing === r.id ? null : r.id)}>{drawing === r.id ? "done" : "draw"}</button>
                <button className="ghost" onClick={() => setDraft({ ...draft, regions: draft.regions.filter((_: Json, j: number) => j !== i) })}>✕</button>
              </div>
            ))}
          </div>
          <div className="row gap">
            <input placeholder="new_region_id" value={newId} onChange={(e) => setNewId(e.target.value)} className="grow" />
            <select value={newKind} onChange={(e) => setNewKind(e.target.value)}>
              <option value="card">card</option>
              <option value="number">number</option>
              <option value="indicator">indicator</option>
            </select>
            <button
              onClick={() => {
                if (!/^[a-z0-9_]+$/.test(newId)) return notify("ids: lowercase letters, digits, _", true);
                setDraft({ ...draft, regions: [...draft.regions, { id: newId, kind: newKind, label: newId, rect: { x: 10, y: 10, w: 60, h: 80 }, threshold: 110, polarity: "light" }] });
                setDrawing(newId);
                setNewId("");
              }}
            >
              Add
            </button>
          </div>
          <p className="small muted">Region ids drive the game logic (e.g. dealer_0, p0_0, bet0, btn_hit, shuffle; hero_0, board_0, pot, to_call, seat1_stack). See README.</p>
          <div className="row gap">
            <button className="primary" onClick={saveDraft} disabled={draft.synthetic}>Save layout</button>
            <button onClick={() => { setDraft(null); setDrawing(null); }}>Close editor</button>
          </div>
        </>
      )}
      <h4>Teach recognition (label a crop from the current frame)</h4>
      <div className="row gap">
        <select value={tplRegion} onChange={(e) => setTplRegion(e.target.value)} className="grow">
          <option value="">region…</option>
          {Object.keys(state?.regions ?? {}).filter((id) => !id.startsWith("btn_")).map((id) => <option key={id} value={id}>{id}</option>)}
        </select>
        <input placeholder="e.g. Qh or 125" value={tplValue} onChange={(e) => setTplValue(e.target.value)} />
        <button onClick={label} disabled={!tplRegion || !tplValue}>Save template</button>
      </div>
    </div>
  );
}

function ClaudeTab({ state, notify }: { state: Json; notify: (m: string, e?: boolean) => void }) {
  const c = state?.claude ?? {};
  const set = async (body: Json) => {
    try {
      await api.put("/api/claude", body);
    } catch (e) {
      notify(String(e), true);
    }
  };
  return (
    <div className="form">
      <p className="small">
        Status: <b>{c.configured ? `configured (${c.model})` : "no API key"}</b>. The key is read from <span className="mono">.env</span> on the backend and never sent to the browser. Model: <span className="mono">CLAUDE_MODEL</span>.
      </p>
      <Check label="Enable Claude explanations" value={!!c.enabled} onChange={(v) => set({ enabled: v })} />
      <Check label="Explain automatically at each new validated decision" value={!!c.auto_explain} onChange={(v) => set({ auto_explain: v })} hint="Rate-limited and cached; never sends images." />
      <Check label="Allow image fallback for ambiguous card crops" value={!!c.vision_fallback} onChange={(v) => set({ vision_fallback: v })} hint="Only the single region crop you choose is sent, on demand." />
      <Num label="Max requests this session" value={c.max_requests ?? 60} onChange={(v) => set({ max_requests: v })} />
      <Num label="Min seconds between requests" value={c.min_interval_s ?? 8} onChange={(v) => set({ min_interval_s: v })} />
      <p className="small muted">Requests made: {c.requests_made} · timeout {c.timeout_s}s · cache entries {c.cache_entries}</p>
    </div>
  );
}

function PrivacyTab({ s, upd, save, notify }: TabProps & { notify: (m: string, e?: boolean) => void }) {
  const [hist, setHist] = useState<Json[]>([]);
  const load = () => api.get("/api/history").then((h) => setHist(h.sessions));
  useEffect(() => {
    load();
  }, []);
  return (
    <div className="form">
      <p className="small">All data stays on this computer. Nothing is uploaded except optional Claude requests (validated state and numbers; image crops only if you enable and use the fallback).</p>
      <Check label="Keep screenshots of processed frames (opt-in)" value={s.privacy.retain_screenshots} onChange={(v) => upd(["privacy", "retain_screenshots"], v)} hint="Saved under backend/data/captures; recorded sessions appear as replays." />
      <Check label="Record session timeline to local SQLite" value={s.privacy.record_history} onChange={(v) => upd(["privacy", "record_history"], v)} />
      <button className="primary" onClick={() => save(s)}>Apply</button>
      <h4>Saved sessions</h4>
      <ul className="small">
        {hist.map((h) => (
          <li key={h.id}>#{h.id} {new Date(h.started_at * 1000).toLocaleString()} · {h.game} · {h.events} events · {h.captures} screenshots {h.synthetic ? "· synthetic" : ""}</li>
        ))}
        {!hist.length && <li className="muted">none</li>}
      </ul>
      <div className="row gap">
        <button className="danger" onClick={async () => { const r = await api.del("/api/captures"); notify(`Deleted ${r.deleted_frames} saved screenshots`); load(); }}>Delete saved screenshots</button>
        <button className="danger" onClick={async () => { await api.del("/api/history"); notify("Session history deleted"); load(); }}>Delete session history</button>
      </div>
    </div>
  );
}
