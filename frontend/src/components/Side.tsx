import { ago, api, Json } from "../api";

export function ExplanationPanel({ state, notify }: { state: Json; notify: (m: string, e?: boolean) => void }) {
  const exp = state?.explanation;
  const claude = state?.claude;
  const a = state?.analysis;
  const ask = async () => {
    try {
      await api.post("/api/explain");
    } catch (e) {
      notify(String(e), true);
    }
  };
  return (
    <section className="panel">
      <div className="panel-head">
        <h2>Explanation</h2>
        <div className="row gap">
          <span className={`pill ${claude?.configured ? "on" : ""}`}>{claude?.configured ? claude.model : "Claude off"}</span>
          <button onClick={ask} disabled={!claude?.configured || !a?.facts?.length}>Explain</button>
        </div>
      </div>
      {!claude?.configured && (
        <p className="small muted">No API key configured. All calculations run locally; add ANTHROPIC_API_KEY to .env for plain-language explanations.</p>
      )}
      {exp?.status === "pending" && <p className="muted">Asking Claude about state v{exp.state_version}…</p>}
      {exp?.status === "ok" && exp.state_version === a?.state_version && (
        <div className="explain">
          <p className="headline">{exp.explanation.headline}</p>
          <p>{exp.explanation.explanation}</p>
          <ul>{exp.explanation.key_points.map((k: string, i: number) => <li key={i}>{k}</li>)}</ul>
          {exp.explanation.uncertainty.length > 0 && (
            <div className="warnbox small"><ul>{exp.explanation.uncertainty.map((k: string, i: number) => <li key={i}>{k}</li>)}</ul></div>
          )}
          <p className="small muted">
            Validated against calculator facts for state v{exp.state_version}{exp.cached ? " (cached)" : ""}. Claude explains; the numbers come from the local calculator.
          </p>
        </div>
      )}
      {exp?.status === "rejected" && (
        <div className="warnbox small">Claude's explanation was rejected ({exp.reason}). Showing calculator results only.</div>
      )}
      {(exp?.status === "error" || exp?.status === "skipped") && <p className="small muted">{exp.reason}</p>}
      {claude?.configured && (
        <p className="small muted">
          Requests {claude.requests_made}/{claude.max_requests} · min interval {claude.min_interval_s}s · auto {claude.auto_explain ? "on" : "off"}
          {claude.last_error && ` · last error: ${claude.last_error}`}
        </p>
      )}
    </section>
  );
}

const KIND_ICON: Record<string, string> = {
  hand: "🂡", shoe: "⟳", warning: "⚠", error: "✖", correction: "✎", discard: "⌛", claude: "✦", control: "▸", source: "◉", street: "▤", settings: "⚙", calibration: "⌖",
};

export function Timeline({ events, synthetic }: { events: Json[]; synthetic: boolean }) {
  return (
    <section className="panel timeline">
      <div className="panel-head">
        <h2>Session timeline</h2>
        <span className="muted small">{synthetic ? "replay time" : "capture time"}</span>
      </div>
      <ol>
        {[...(events ?? [])].reverse().map((e: Json, i: number) => (
          <li key={i} className={`tl-${e.kind}`}>
            <span className="tl-icon">{KIND_ICON[e.kind] ?? "•"}</span>
            <span className="tl-t mono">{e.capture_t !== undefined ? `${Number(e.capture_t).toFixed(1)}s` : new Date(e.t * 1000).toLocaleTimeString()}</span>
            <span className="tl-msg">{e.message}</span>
            <span className="tl-v mono">v{e.state_version}</span>
          </li>
        ))}
      </ol>
    </section>
  );
}

export function StatusBar({ state, connected, now }: { state: Json; connected: boolean; now: number }) {
  const s = state?.status;
  const a = state?.analysis;
  const capAge = s?.last_capture_wall ? now - s.last_capture_wall : null;
  const anaAge = a?.computed_at ? now - a.computed_at : null;
  return (
    <div className="statusbar">
      <span className={`badge mode-${s?.mode}`}>{(s?.mode ?? "…").toUpperCase()}</span>
      {!connected && <span className="badge bad">DISCONNECTED FROM BACKEND</span>}
      {s?.source_error && <span className="badge bad">SOURCE: {s.source_error}</span>}
      {s?.stale && <span className="badge warn">STALE — no new frames</span>}
      {a?.status === "suppressed" && <span className="badge warn">UNCERTAIN</span>}
      {s?.synthetic && <span className="badge demo">SYNTHETIC DEMO DATA</span>}
      <span className="sb-item">source <b>{s?.source?.name ?? s?.source?.target?.title ?? s?.source?.type ?? "none"}</b></span>
      <span className="sb-item">layout <b>{s?.layout?.name}</b></span>
      <span className="sb-item">capture {s?.last_capture_t !== null && s?.last_capture_t !== undefined && s?.source?.type === "replay" ? `t=${Number(s.last_capture_t).toFixed(1)}s` : ""} <b>{ago(capAge)}</b> ago</span>
      <span className="sb-item">analysis age <b>{ago(anaAge)}</b></span>
      <span className="sb-item">state <b>v{s?.state_version ?? 0}</b></span>
    </div>
  );
}
