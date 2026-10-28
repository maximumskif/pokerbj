import { useEffect, useRef, useState } from "react";
import { api, Json } from "../api";

interface Props {
  state: Json;
  layout: Json | null;
  calib: { regionId: string | null; onRect: (r: { x: number; y: number; w: number; h: number }) => void } | null;
  onSelectRegion: (id: string) => void;
  notify: (msg: string, err?: boolean) => void;
}

const STATUS_COLOR: Record<string, string> = {
  stable: "#3ddc97",
  pending: "#8b95a7",
  uncertain: "#ffb547",
  manual: "#5aa9ff",
};

export function CapturePanel({ state, layout, calib, onSelectRegion, notify }: Props) {
  const status = state?.status;
  const [sources, setSources] = useState<Json>(null);
  const [srcKind, setSrcKind] = useState<"replay" | "screen">("replay");
  const [replay, setReplay] = useState("");
  const [win, setWin] = useState("");
  const [rect, setRect] = useState("0,0,960,600");
  const [showOverlay, setShowOverlay] = useState(true);
  const [drag, setDrag] = useState<{ x0: number; y0: number; x1: number; y1: number } | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);

  useEffect(() => {
    api.get("/api/sources").then((s) => {
      setSources(s);
      if (s.replays.length && !replay) setReplay(s.replays[0].name);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const activeReplay = status?.source?.type === "replay" ? status.source.name : null;
  useEffect(() => {
    if (activeReplay) {
      setSrcKind("replay");
      setReplay(activeReplay);
    }
  }, [activeReplay]);

  const refreshSources = () => api.get("/api/sources").then(setSources);

  const choose = async () => {
    try {
      if (srcKind === "replay") {
        await api.post("/api/source", { type: "replay", name: replay });
      } else {
        const w = sources?.windows?.find((x: Json) => String(x.hwnd) === win);
        const target = w
          ? { kind: "window", hwnd: w.hwnd, title: w.title }
          : { kind: "region", rect: rect.split(",").map((v) => parseInt(v.trim(), 10)) };
        await api.post("/api/source", { type: "screen", target });
      }
      await api.post("/api/control", { action: "capture" });
      notify("Source selected. The preview shows exactly what will be captured.");
    } catch (e) {
      notify(String(e), true);
    }
  };

  const control = async (action: string) => {
    try {
      await api.post("/api/control", { action });
    } catch (e) {
      notify(String(e), true);
    }
  };

  const frameId = status?.frame_id ?? 0;
  const lastWall = status?.last_capture_wall ?? 0;
  const imgSrc = lastWall ? `/api/preview.jpg?f=${frameId}-${Math.floor(lastWall * 10)}` : null;
  const W = layout?.width ?? status?.layout?.width ?? 960;
  const H = layout?.height ?? status?.layout?.height ?? 600;
  const regions = state?.regions ?? {};

  const toLocal = (e: React.MouseEvent) => {
    const r = svgRef.current!.getBoundingClientRect();
    return { x: Math.round(((e.clientX - r.left) / r.width) * W), y: Math.round(((e.clientY - r.top) / r.height) * H) };
  };

  const rp = status?.replay_position;
  return (
    <section className="panel capture">
      <div className="panel-head">
        <h2>Capture</h2>
        <div className="row gap">
          <label className="chk">
            <input type="checkbox" checked={showOverlay} onChange={(e) => setShowOverlay(e.target.checked)} /> regions
          </label>
        </div>
      </div>

      <div className="source-row">
        <select value={srcKind} onChange={(e) => setSrcKind(e.target.value as "replay" | "screen")}>
          <option value="replay">Replay</option>
          <option value="screen">Screen / window</option>
        </select>
        {srcKind === "replay" ? (
          <select value={replay} onChange={(e) => setReplay(e.target.value)} className="grow">
            {sources?.replays?.map((r: Json) => (
              <option key={r.name} value={r.name}>
                {r.synthetic ? "[SYNTHETIC] " : ""}
                {r.name} · {r.game} · {r.frames} frames
              </option>
            ))}
          </select>
        ) : (
          <>
            <select value={win} onChange={(e) => setWin(e.target.value)} className="grow" onFocus={refreshSources}>
              <option value="">Screen region (x,y,w,h) →</option>
              {sources?.windows?.map((w: Json) => (
                <option key={w.hwnd} value={String(w.hwnd)}>
                  {w.title.slice(0, 60)} ({w.rect[2]}×{w.rect[3]})
                </option>
              ))}
            </select>
            {!win && <input value={rect} onChange={(e) => setRect(e.target.value)} className="rect-in" title="x,y,width,height in screen pixels" />}
          </>
        )}
        <button onClick={choose}>Select</button>
      </div>
      {srcKind === "screen" && sources && !sources.screen_capture_available && (
        <div className="hint warn">Screen capture is not available in this environment (no display). Run the backend natively on Windows, or use replay mode.</div>
      )}
      {srcKind === "screen" && sources?.monitors?.length > 0 && (
        <div className="hint">
          Monitors: {sources.monitors.map((m: Json) => `#${m.index} ${m.rect.join(",")}`).join(" · ")}. Only the selected window/region is captured.
        </div>
      )}

      <div className="controls">
        <button className="primary" disabled={!status?.source?.type || status?.mode === "running"} onClick={() => control("start")}>
          ▶ Start
        </button>
        <button disabled={status?.mode !== "running"} onClick={() => control("pause")}>
          ❚❚ Pause
        </button>
        <button disabled={status?.mode === "stopped"} onClick={() => control("stop")}>
          ■ Stop
        </button>
        <button disabled={!status?.source?.type || status?.mode === "running"} onClick={() => control("capture")}>
          {status?.source?.type === "replay" ? "Step" : "Capture once"}
        </button>
        <span className="muted small">
          {rp ? `frame ${rp[0]}/${rp[1]} · ` : ""}processed {status?.frames_processed ?? 0} · duplicates skipped {status?.frames_skipped_duplicate ?? 0}
        </span>
      </div>

      <div className="preview" style={{ aspectRatio: `${W} / ${H}` }}>
        {imgSrc ? <img src={imgSrc} alt="captured region" draggable={false} /> : <div className="preview-empty">No frame captured yet. Select a source, then Start or Step.</div>}
        <svg
          ref={svgRef}
          viewBox={`0 0 ${W} ${H}`}
          className={calib?.regionId ? "drawing" : ""}
          onMouseDown={(e) => {
            if (!calib?.regionId) return;
            const p = toLocal(e);
            setDrag({ x0: p.x, y0: p.y, x1: p.x, y1: p.y });
          }}
          onMouseMove={(e) => {
            if (!drag) return;
            const p = toLocal(e);
            setDrag({ ...drag, x1: p.x, y1: p.y });
          }}
          onMouseUp={() => {
            if (drag && calib) {
              const r = { x: Math.min(drag.x0, drag.x1), y: Math.min(drag.y0, drag.y1), w: Math.abs(drag.x1 - drag.x0), h: Math.abs(drag.y1 - drag.y0) };
              if (r.w > 3 && r.h > 3) calib.onRect(r);
            }
            setDrag(null);
          }}
        >
          {showOverlay &&
            layout?.regions?.map((r: Json) => {
              const st = regions[r.id];
              const color = calib?.regionId === r.id ? "#ff5ad1" : STATUS_COLOR[st?.status ?? "pending"];
              return (
                <g key={r.id} onClick={() => !calib?.regionId && onSelectRegion(r.id)} className="region">
                  <rect x={r.rect.x} y={r.rect.y} width={r.rect.w} height={r.rect.h} fill="transparent" stroke={color} strokeWidth={calib?.regionId === r.id ? 3 : 1.5} rx={2} />
                  <title>
                    {r.label}: {st?.value ?? "—"} ({st?.status ?? "pending"}, recognition {Math.round((st?.confidence ?? 0) * 100)}%)
                  </title>
                </g>
              );
            })}
          {drag && <rect x={Math.min(drag.x0, drag.x1)} y={Math.min(drag.y0, drag.y1)} width={Math.abs(drag.x1 - drag.x0)} height={Math.abs(drag.y1 - drag.y0)} fill="rgba(255,90,209,.15)" stroke="#ff5ad1" strokeDasharray="4 3" />}
        </svg>
        {status?.synthetic && <div className="wm">SYNTHETIC DEMO DATA</div>}
      </div>
      <div className="legend">
        {Object.entries(STATUS_COLOR).map(([k, c]) => (
          <span key={k}>
            <i style={{ background: c }} /> {k}
          </span>
        ))}
        <span className="muted">Click a region to inspect or correct it.</span>
      </div>
    </section>
  );
}
