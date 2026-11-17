import { useEffect, useRef, useState } from "react";
import { api, Json } from "../api";

// General live screen assistant: pick a window or monitor, then chat about it.
export function Assistant({ notify }: { notify: (m: string, err?: boolean) => void }) {
  const [st, setSt] = useState<Json>(null);
  const [sources, setSources] = useState<Json>(null);
  const [pick, setPick] = useState("");
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [interval, setIntervalS] = useState(20);
  const [focus, setFocus] = useState("");
  const [frameKey, setFrameKey] = useState(0);
  const endRef = useRef<HTMLDivElement>(null);

  const refresh = () => api.get("/api/assistant").then(setSt).catch(() => {});
  const loadSources = () => api.get("/api/sources").then(setSources).catch(() => {});

  useEffect(() => {
    refresh();
    loadSources();
    const t = setInterval(refresh, 2000); // picks up watch-mode notes
    return () => clearInterval(t);
  }, []);
  const count = st?.messages?.length ?? 0;
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
    setFrameKey((k) => k + 1);
  }, [count]);

  const choose = async (value: string) => {
    setPick(value);
    if (!value) return;
    let target: Json;
    if (value.startsWith("m:")) {
      const m = sources?.monitors?.find((x: Json) => String(x.index) === value.slice(2));
      target = { kind: "region", rect: m.rect, title: `Monitor ${m.index}` };
    } else {
      const w = sources?.windows?.find((x: Json) => String(x.hwnd) === value.slice(2));
      target = { kind: "window", hwnd: w.hwnd, title: w.title };
    }
    try {
      setSt(await api.post("/api/assistant/target", { target }));
    } catch (e) {
      notify(String(e), true);
    }
  };

  const send = async (quick = false) => {
    const msg = quick ? "" : text.trim();
    if ((!msg && !quick) || sending) return;
    setSending(true);
    try {
      setSt(await api.post("/api/assistant/ask", { text: msg }));
      if (!quick) setText("");
    } catch (e) {
      notify(String(e), true);
    } finally {
      setSending(false);
    }
  };

  const watch = async (on: boolean) => {
    try {
      setSt(await api.post("/api/assistant/watch", { on, interval_s: interval, focus }));
    } catch (e) {
      notify(String(e), true);
    }
  };

  const msgs: Json[] = st?.messages ?? [];
  return (
    <main className="assistant">
      <div className="col-left">
        <section className="panel">
          <div className="panel-head">
            <h2>What it sees</h2>
            <button onClick={loadSources}>↻</button>
          </div>
          <select value={pick} onChange={(e) => choose(e.target.value)} onFocus={loadSources} className="grow">
            <option value="">{st?.target ? `Current: ${st.target.title}` : "Pick a window or monitor…"}</option>
            {sources?.monitors?.map((m: Json) => (
              <option key={`m${m.index}`} value={`m:${m.index}`}>Whole monitor {m.index} ({m.rect[2]}×{m.rect[3]})</option>
            ))}
            {sources?.windows?.map((w: Json) => (
              <option key={w.hwnd} value={`w:${w.hwnd}`}>{w.title}</option>
            ))}
          </select>
          {st?.target && <img className="as-frame" src={`/api/assistant/frame.jpg?k=${frameKey}`} alt="" onError={(e) => ((e.target as HTMLImageElement).style.display = "none")} onLoad={(e) => ((e.target as HTMLImageElement).style.display = "")} />}
          <p className="muted small">A screenshot is taken only when you send a message, or on each watch check. It goes to Claude and nowhere else.</p>
        </section>
        <section className="panel">
          <div className="panel-head">
            <h2>Watch mode</h2>
            <span className={st?.watching ? "pill ok" : "pill"}>{st?.watching ? "on" : "off"}</span>
          </div>
          <p className="muted small">Looks every N seconds. Only asks Claude if the screen changed, and only posts when there's something worth saying.</p>
          <div className="row gap">
            <label className="small">every <input type="number" min={10} max={600} value={interval} onChange={(e) => setIntervalS(Number(e.target.value))} style={{ width: 64 }} /> s</label>
          </div>
          <input placeholder="Watch for… (optional, e.g. errors in my code)" value={focus} onChange={(e) => setFocus(e.target.value)} style={{ width: "100%", marginTop: 8 }} />
          <div className="row gap" style={{ marginTop: 8 }}>
            {st?.watching ? <button onClick={() => watch(false)}>Stop watching</button> : <button onClick={() => watch(true)}>Start watching</button>}
          </div>
        </section>
        <p className="muted small">
          {st?.configured ? `Model ${st.model} · ${st.requests} requests this run` : "No ANTHROPIC_API_KEY in .env yet. Add it and restart to chat."}
          {st?.last_error && <><br /><span className="bad">{st.last_error}</span></>}
        </p>
      </div>
      <div className="col-right">
        <section className="panel as-chat">
          <div className="panel-head">
            <h2>Chat</h2>
            <button onClick={() => api.del("/api/assistant").then(setSt)}>Clear</button>
          </div>
          <div className="as-log">
            {msgs.length === 0 && <p className="muted">Pick what it should look at, then hit ⚡ Quick look or ask anything.</p>}
            {msgs.map((m, i) => (
              <div key={i} className={`as-msg ${m.role}${m.watch ? " watch" : ""}`}>
                {m.watch && <span className="pill small">watch</span>}
                {m.text}
              </div>
            ))}
            {(sending || st?.busy) && <div className="as-msg assistant muted">Looking…</div>}
            <div ref={endRef} />
          </div>
          <div className="as-input">
            <textarea
              rows={2}
              placeholder="Ask about what's on screen… (Enter to send, Shift+Enter for a new line)"
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  send();
                }
              }}
            />
            <div className="as-btns">
              <button onClick={() => send()} disabled={sending || !text.trim()}>Send</button>
              <button className="primary" onClick={() => send(true)} disabled={sending} title="Quick insight on what's on screen, no typing needed">⚡ Quick look</button>
            </div>
          </div>
        </section>
      </div>
    </main>
  );
}
