import { useState } from "react";
import { Assistant } from "./components/Assistant";

// Screen Assistant only. The old card-game / chess views are switched off (their code is
// still in components/ if they're ever wanted again).
export default function App() {
  const [toast, setToast] = useState<{ msg: string; err: boolean } | null>(null);

  const notify = (msg: string, err = false) => {
    setToast({ msg, err });
    setTimeout(() => setToast(null), 4000);
  };

  return (
    <div className="app">
      <header>
        <div className="brand">
          <span className="logo">◆</span> Screen Assistant
          <span className="muted small"> quick insight on whatever you're doing</span>
        </div>
      </header>
      <Assistant notify={notify} />
      {toast && <div className={`toast${toast.err ? " err" : ""}`}>{toast.msg}</div>}
    </div>
  );
}
