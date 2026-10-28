import { useEffect, useState } from "react";
import { api, connectState, Json } from "./api";
import { CapturePanel } from "./components/CapturePanel";
import { BlackjackAnalysis, BlackjackHand, HoldemAnalysis, HoldemHand } from "./components/GamePanels";
import { RegionInspector } from "./components/RegionInspector";
import { SettingsDrawer } from "./components/Settings";
import { ExplanationPanel, StatusBar, Timeline } from "./components/Side";

export default function App() {
  const [state, setState] = useState<Json>(null);
  const [connected, setConnected] = useState(false);
  const [now, setNow] = useState(Date.now() / 1000);
  const [inspect, setInspect] = useState<string | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [layout, setLayout] = useState<Json>(null);
  const [draft, setDraft] = useState<Json>(null);
  const [drawing, setDrawing] = useState<string | null>(null);
  const [toast, setToast] = useState<{ msg: string; err: boolean } | null>(null);
  const [ack, setAck] = useState<boolean>(() => {
    try {
      return localStorage.getItem("ta-ack-rules") === "1";
    } catch {
      return false;
    }
  });

  useEffect(() => connectState(setState, setConnected), []);
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now() / 1000), 500);
    return () => clearInterval(t);
  }, []);
  const layoutName = state?.status?.layout?.name;
  useEffect(() => {
    if (layoutName) api.get(`/api/layouts/${layoutName}`).then(setLayout).catch(() => setLayout(null));
  }, [layoutName]);

  const notify = (msg: string, err = false) => {
    setToast({ msg, err });
    setTimeout(() => setToast(null), 4000);
  };

  const game = state?.status?.layout?.game;
  const shownLayout = draft ?? layout;

  return (
    <div className="app">
      <header>
        <div className="brand">
          <span className="logo">◆</span> Table Assistant
          <span className="muted small"> probability analysis · manual control only</span>
        </div>
        <button onClick={() => setSettingsOpen(!settingsOpen)}>⚙ Settings</button>
      </header>
      <StatusBar state={state} connected={connected} now={now} />
      {!ack && (
        <div className="notice">
          <b>Before using this live:</b> many online casinos and poker sites prohibit real-time assistance software. Check your platform's terms first; replay analysis of saved
          screenshots works independently. This tool only observes the region you select. It never clicks, bets, accesses accounts or evades anti-cheat, and it cannot predict
          hidden cards or random outcomes.
          <button
            onClick={() => {
              setAck(true);
              try {
                localStorage.setItem("ta-ack-rules", "1");
              } catch {
                /* storage unavailable: banner returns next visit */
              }
            }}
          >
            Understood
          </button>
        </div>
      )}
      <main>
        <div className="col-left">
          <CapturePanel
            state={state}
            layout={shownLayout}
            calib={drawing && draft ? {
              regionId: drawing,
              onRect: (r) => setDraft({ ...draft, regions: draft.regions.map((x: Json) => (x.id === drawing ? { ...x, rect: r } : x)) }),
            } : null}
            onSelectRegion={setInspect}
            notify={notify}
          />
          <Timeline events={state?.timeline ?? []} synthetic={state?.status?.source?.type === "replay"} />
        </div>
        <div className="col-right">
          <section className={`panel hand-panel${state?.analysis?.status === "suppressed" ? " uncertain" : ""}`}>
            <div className="panel-head">
              <h2>Current hand · {game === "holdem" ? "Texas Hold'em" : "Blackjack"}</h2>
              {state?.status?.synthetic && <span className="pill demo">synthetic</span>}
            </div>
            {game === "holdem" ? <HoldemHand game={state?.game} onCard={setInspect} /> : <BlackjackHand game={state?.game} onCard={setInspect} />}
          </section>
          {game === "holdem" ? <HoldemAnalysis a={state?.analysis} game={state?.game} /> : <BlackjackAnalysis a={state?.analysis} />}
          <ExplanationPanel state={state} notify={notify} />
        </div>
      </main>
      {settingsOpen && (
        <SettingsDrawer state={state} onClose={() => setSettingsOpen(false)} notify={notify} draft={draft} setDraft={setDraft} drawing={drawing} setDrawing={setDrawing} />
      )}
      {inspect && shownLayout && <RegionInspector regionId={inspect} layout={shownLayout} state={state} onClose={() => setInspect(null)} notify={notify} />}
      {toast && <div className={`toast${toast.err ? " err" : ""}`}>{toast.msg}</div>}
    </div>
  );
}
