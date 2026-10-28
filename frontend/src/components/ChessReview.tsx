import { useEffect, useState } from "react";
import { api, Json } from "../api";

const PIECE: Record<string, string> = { K: "♔", Q: "♕", R: "♖", B: "♗", N: "♘", P: "♙", k: "♚", q: "♛", r: "♜", b: "♝", n: "♞", p: "♟" };
const CLASS_COLOR: Record<string, string> = { best: "#3ddc97", good: "#8b95a7", inaccuracy: "#e8d44d", mistake: "#ffb547", blunder: "#ff5d6c" };
const SAMPLE = `[Event "Sample"]\n[White "White"]\n[Black "Black"]\n[Result "1-0"]\n\n1. e4 e5 2. Bc4 Nc6 3. Qh5 Nf6 4. Qxf7# 1-0`;

function sq(name: string): [number, number] {
  return [name.charCodeAt(0) - 97, 8 - parseInt(name[1], 10)];
}

function Board({ fen, played, best }: { fen: string; played?: string; best?: string | null }) {
  const rows = fen.split(" ")[0].split("/");
  const cells: { piece: string | null; x: number; y: number }[] = [];
  rows.forEach((row, y) => {
    let x = 0;
    for (const ch of row) {
      if (/\d/.test(ch)) {
        for (let k = 0; k < parseInt(ch, 10); k++) cells.push({ piece: null, x: x++, y });
      } else cells.push({ piece: ch, x: x++, y });
    }
  });
  const hl = new Set<string>();
  if (played) [played.slice(0, 2), played.slice(2, 4)].forEach((s) => hl.add(sq(s).join(",")));
  const arrow = best ? [sq(best.slice(0, 2)), sq(best.slice(2, 4))] : null;
  return (
    <svg viewBox="0 0 8 8" className="chessboard">
      {cells.map((c) => (
        <g key={`${c.x}-${c.y}`}>
          <rect x={c.x} y={c.y} width={1} height={1} fill={(c.x + c.y) % 2 ? "#6b8299" : "#c9d3dd"} />
          {hl.has(`${c.x},${c.y}`) && <rect x={c.x} y={c.y} width={1} height={1} fill="rgba(255,213,74,.45)" />}
          {c.piece && (
            <text x={c.x + 0.5} y={c.y + 0.78} textAnchor="middle" fontSize="0.82" fill={c.piece === c.piece.toUpperCase() ? "#fff" : "#111"} stroke={c.piece === c.piece.toUpperCase() ? "#111" : "none"} strokeWidth={0.02}>
              {PIECE[c.piece]}
            </text>
          )}
        </g>
      ))}
      {arrow && (
        <line x1={arrow[0][0] + 0.5} y1={arrow[0][1] + 0.5} x2={arrow[1][0] + 0.5} y2={arrow[1][1] + 0.5} stroke="#3ddc97" strokeWidth={0.13} strokeOpacity={0.8} markerEnd="url(#ah)" />
      )}
      <defs>
        <marker id="ah" markerWidth="3" markerHeight="3" refX="1.5" refY="1.5" orient="auto">
          <path d="M0,0 L3,1.5 L0,3 z" fill="#3ddc97" />
        </marker>
      </defs>
    </svg>
  );
}

function EvalGraph({ moves, ply, onPick }: { moves: Json[]; ply: number; onPick: (p: number) => void }) {
  const n = moves.length;
  const pts = [[0, 50], ...moves.map((m: Json, i: number) => [i + 1, m.white_win_pct])];
  const path = pts.map(([x, y], i) => `${i ? "L" : "M"}${(x / n) * 100},${100 - y}`).join(" ");
  return (
    <svg viewBox="0 0 100 100" preserveAspectRatio="none" className="evalgraph" onClick={(e) => {
      const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
      onPick(Math.max(1, Math.min(n, Math.round(((e.clientX - r.left) / r.width) * n))));
    }}>
      <path d={`${path} L100,100 L0,100 Z`} fill="rgba(230,233,240,.85)" />
      <line x1={0} y1={50} x2={100} y2={50} stroke="#8b95a7" strokeWidth={0.4} />
      {moves.map((m: Json, i: number) =>
        ["mistake", "blunder"].includes(m.classification) ? <circle key={i} cx={((i + 1) / n) * 100} cy={100 - m.white_win_pct} r={1.4} fill={CLASS_COLOR[m.classification]} /> : null,
      )}
      <line x1={(ply / n) * 100} y1={0} x2={(ply / n) * 100} y2={100} stroke="#5aa9ff" strokeWidth={0.6} />
    </svg>
  );
}

function evalText(m: Json): string {
  if (m.white_mate !== null && m.white_mate !== undefined) return m.white_mate === 0 ? "#" : `M${m.white_mate}`;
  return `${m.white_eval_cp >= 0 ? "+" : ""}${(m.white_eval_cp / 100).toFixed(2)}`;
}

export function ChessReview({ notify }: { notify: (m: string, e?: boolean) => void }) {
  const [pgn, setPgn] = useState(SAMPLE);
  const [depth, setDepth] = useState(16);
  const [job, setJob] = useState<Json>(null);
  const [engine, setEngine] = useState<Json>(null);
  const [ply, setPly] = useState(1);

  useEffect(() => {
    api.get("/api/chess/status").then(setEngine);
  }, []);
  useEffect(() => {
    if (!job || job.status !== "running") return;
    const t = setInterval(() => api.get(`/api/chess/review/${job.id}`).then(setJob), 400);
    return () => clearInterval(t);
  }, [job]);

  const start = async () => {
    try {
      const { job_id } = await api.post("/api/chess/review", { pgn, depth });
      setJob({ id: job_id, status: "running", progress: 0 });
      setPly(1);
    } catch (e) {
      notify(String(e), true);
    }
  };

  const r = job?.result;
  const m = r?.moves?.[ply - 1];
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!r || (e.target as HTMLElement).tagName === "TEXTAREA") return;
      if (e.key === "ArrowRight") setPly((p) => Math.min(r.moves.length, p + 1));
      if (e.key === "ArrowLeft") setPly((p) => Math.max(1, p - 1));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [r]);

  return (
    <main className="chess-main">
      <div className="col-left">
        <section className="panel">
          <div className="panel-head">
            <h2>Chess · post-game review</h2>
            <span className={`pill ${engine?.engine_found ? "on" : "warn"}`}>{engine?.engine_found ? "Stockfish ready" : "Stockfish not installed"}</span>
          </div>
          <p className="small muted">Paste the PGN of a finished game (export it from your chess site after the game ends). Analysis runs locally with Stockfish.</p>
          <textarea value={pgn} onChange={(e) => setPgn(e.target.value)} rows={7} className="pgn" spellCheck={false} />
          <div className="row gap">
            <label className="small muted">Depth <input type="number" value={depth} min={6} max={30} onChange={(e) => setDepth(parseInt(e.target.value, 10))} style={{ width: 64 }} /></label>
            <button className="primary" onClick={start} disabled={!engine?.engine_found || job?.status === "running"}>Review game</button>
            {job?.status === "running" && <span className="small muted">analysing… {Math.round((job.progress ?? 0) * 100)}%</span>}
            {job?.status === "error" && <span className="small" style={{ color: "var(--bad)" }}>{job.error}</span>}
          </div>
          {!engine?.engine_found && (
            <div className="warnbox small">Install the engine: <span className="mono">python backend/scripts/get_stockfish.py</span>, or set <span className="mono">STOCKFISH_PATH</span> in .env, then reload.</div>
          )}
        </section>
        {r && (
          <section className="panel">
            <div className="panel-head">
              <h2>{r.headers.White ?? "White"} vs {r.headers.Black ?? "Black"} · {r.summary.final}</h2>
              <span className="muted small">{job.engine} · depth {job.settings.depth}</span>
            </div>
            <div className="stats">
              {(["white", "black"] as const).map((c) => (
                <div key={c}>
                  <span>{c} accuracy</span>
                  <b>{r.summary[c].accuracy}%</b>
                  <span>{r.summary[c].inaccuracy} inacc · {r.summary[c].mistake} mist · {r.summary[c].blunder} blund</span>
                </div>
              ))}
            </div>
            <EvalGraph moves={r.moves} ply={ply} onPick={setPly} />
            <div className="movelist">
              {r.moves.map((mv: Json) => (
                <button key={mv.ply} className={`mv${mv.ply === ply ? " on" : ""}`} onClick={() => setPly(mv.ply)} style={{ borderLeftColor: CLASS_COLOR[mv.classification] }}>
                  {mv.color === "white" ? `${mv.move_number}.` : ""} {mv.san}
                </button>
              ))}
            </div>
            <p className="small muted">{r.method.classification}. {r.method.note}.</p>
          </section>
        )}
      </div>
      <div className="col-right">
        {r && m && (
          <section className="panel">
            <div className="panel-head">
              <h2>Move {m.move_number}{m.color === "black" ? "…" : "."} {m.san}</h2>
              <span className="pill" style={{ color: CLASS_COLOR[m.classification], borderColor: CLASS_COLOR[m.classification] }}>{m.classification}</span>
            </div>
            <Board fen={m.fen_before} played={m.uci} best={m.best_uci !== m.uci ? m.best_uci : null} />
            <div className="row gap" style={{ justifyContent: "space-between", marginTop: 8 }}>
              <button onClick={() => setPly(Math.max(1, ply - 1))}>◀</button>
              <span className="small muted">position before the move · yellow = played · green arrow = engine's best</span>
              <button onClick={() => setPly(Math.min(r.moves.length, ply + 1))}>▶</button>
            </div>
            <table className="kv small" style={{ marginTop: 8 }}>
              <tbody>
                <tr><td>Played</td><td><b>{m.san}</b> → eval {evalText(m)} (White's view)</td></tr>
                <tr><td>Engine's best</td><td><b>{m.best_san ?? "—"}</b>{m.best_line?.length ? <span className="muted"> · line: {m.best_line.join(" ")}</span> : null}</td></tr>
                <tr><td>Win chance lost</td><td>{(m.win_chance_drop * 50).toFixed(1)} percentage points</td></tr>
                <tr><td>White win estimate</td><td>{m.white_win_pct}%</td></tr>
                <tr><td>Move accuracy</td><td>{m.accuracy}%</td></tr>
              </tbody>
            </table>
          </section>
        )}
        {!r && (
          <section className="panel">
            <p className="muted">Review results appear here: board, engine's best move for each position, mistakes and the win-probability graph.</p>
          </section>
        )}
      </div>
    </main>
  );
}
