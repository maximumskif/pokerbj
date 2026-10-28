import { useState } from "react";
import { api, Json } from "../api";

interface Props {
  regionId: string;
  layout: Json;
  state: Json;
  onClose: () => void;
  notify: (msg: string, err?: boolean) => void;
}

export function RegionInspector({ regionId, layout, state, onClose, notify }: Props) {
  const reg = layout?.regions?.find((r: Json) => r.id === regionId);
  const st = state?.regions?.[regionId];
  const [value, setValue] = useState<string>(st?.value && st.value !== "empty" ? st.value : "");
  const [suggestion, setSuggestion] = useState<Json>(null);
  if (!reg) return null;

  const apply = async (v: string | null) => {
    try {
      await api.post("/api/correction", { region_id: regionId, value: v });
      notify(v === null ? "Correction cleared; recognition resumes." : `Corrected ${reg.label} → ${v}`);
      onClose();
    } catch (e) {
      notify(String(e), true);
    }
  };

  const ask = async () => {
    try {
      const r = await api.post("/api/vision/suggest", { region_id: regionId });
      setSuggestion(r);
    } catch (e) {
      notify(String(e), true);
    }
  };

  const hint =
    reg.kind === "card" ? "e.g. Ah, 10d, Ks, hidden, empty" : reg.kind === "number" ? "digits, or empty" : "on / off";

  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="panel-head">
          <h3>{reg.label}</h3>
          <button className="ghost" onClick={onClose}>
            ✕
          </button>
        </div>
        <div className="inspect">
          <img src={`/api/region/${regionId}.png?t=${state?.status?.frame_id}`} alt="region crop" className="crop" />
          <table className="kv">
            <tbody>
              <tr><td>Committed value</td><td><b>{st?.value ?? "—"}</b></td></tr>
              <tr><td>Status</td><td><span className={`pill st-${st?.status}`}>{st?.status ?? "pending"}</span> {st?.source === "manual" && "(manual)"}</td></tr>
              <tr><td>Latest raw reading</td><td>{st?.last_raw ?? "unreadable"}</td></tr>
              <tr><td>Recognition confidence</td><td>{Math.round((st?.last_raw_confidence ?? 0) * 100)}% <span className="muted small">(match quality, not a probability of the game)</span></td></tr>
              <tr><td>Region</td><td className="mono small">{regionId} · {reg.kind} · {reg.rect.x},{reg.rect.y} {reg.rect.w}×{reg.rect.h}</td></tr>
            </tbody>
          </table>
        </div>
        <div className="row gap">
          <input value={value} onChange={(e) => setValue(e.target.value)} placeholder={hint} className="grow" />
          <button className="primary" onClick={() => apply(value.trim().replace(/^10/, "T"))} disabled={!value.trim()}>
            Set value
          </button>
          {st?.source === "manual" && <button onClick={() => apply(null)}>Clear correction</button>}
        </div>
        <p className="muted small">Manual values override recognition until cleared and are marked “manual” everywhere. Shoe counts adjust automatically.</p>
        {reg.kind === "card" && state?.claude?.vision_fallback && (
          <div className="vision">
            <button onClick={ask}>Ask Claude about this crop</button>
            <span className="muted small"> Sends only this {reg.rect.w}×{reg.rect.h} crop. Result is a suggestion you must confirm.</span>
            {suggestion && (
              <div className="hint">
                {suggestion.status === "ok" ? (
                  <>
                    Suggestion: <b>{suggestion.suggestion ?? "no card visible"}</b> ({suggestion.confidence}) — {suggestion.notes}{" "}
                    {suggestion.suggestion && <button onClick={() => apply(suggestion.suggestion)}>Accept</button>}
                  </>
                ) : (
                  suggestion.reason
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
