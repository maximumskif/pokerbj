# Table Assistant

A local desktop assistant that watches a **selected** game window or screen region and analyses
blackjack and Texas Hold'em: local screen recognition → validated, version-stamped state →
deterministic maths → optional plain-language explanations from Claude. It also includes a live
always-on-top overlay and a Stockfish-based chess post-game review.

This is **probability analysis**, not prediction. It never clicks, bets, reads accounts, or evades
anti-cheat. **Many online casinos and poker rooms prohibit real-time assistance software. Check the
platform's rules before using live capture.** Replay analysis of saved screenshots works on its own.

## Project status (pick up here)

_Last updated 2026-10-28._

**Done and verified**

| Feature | State |
|---|---|
| Blackjack engine (rules, legal actions, basic strategy, EV, insurance/even money, shoe tracking) | ✅ tested against published values + independent simulator |
| Hold'em engine (evaluator, ranges, exact/Monte Carlo equity, pot odds) | ✅ exhaustive evaluator check, hand-counted equities |
| Capture → recognition → stabilization → state versioning | ✅ synthetic replays end to end; Windows capture smoke-tested in CI |
| Dashboard (preview + overlays, corrections, calibration, timeline, settings, privacy) | ✅ driven in headless Chromium |
| Live overlay window | ✅ rendered and unit-tested; not yet used on a real Windows desktop |
| Chess post-game review | ✅ real Stockfish 19 review of the Opera Game |
| Claude explanations | ⚠️ implemented + mocked tests; **no live API call made yet** (needs `ANTHROPIC_API_KEY`) |
| CI (Ubuntu + Windows + frontend build) | ✅ green |

**Next steps**

1. On the Windows PC: `.\start.ps1`, then `python backend\scripts\get_stockfish.py` (the engine binary is not in git).
2. Calibrate a real table: Settings → Calibration → copy a layout → select the game window → draw regions → label a few cards/numbers.
3. Try the overlay on the real desktop and check placement and DPI scaling.
4. Add `ANTHROPIC_API_KEY` to `.env` and confirm a live explanation passes validation.
5. Possible improvements: resplit-aware split EV, decimal/currency amount parsing (currently digits only), a Claude explanation for chess reviews.

**Scope decisions:** live overlay for blackjack/hold'em is a visible window with no detection evasion; chess is post-game review only (no live board reading).

## Quick start

Requirements: Python 3.11+ (3.12 tested), Node 18+ (22 tested).

**Windows (PowerShell)**

```powershell
.\start.ps1
```

**Linux/macOS**

```bash
./start.sh
```

Then open <http://127.0.0.1:8765>. The first launch generates the synthetic demo replays.
Pick **Replay → [SYNTHETIC] synthetic_blackjack**, click **Select**, then **Start** or **Step**.
No API key is needed.

Manual steps, equivalent to the scripts:

```bash
cd backend && python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/make_replays.py          # synthetic layouts + replays
cd ../frontend && npm install && npm run build    # dashboard served by the backend
cd ../backend && .venv/bin/python run.py          # http://127.0.0.1:8765
```

Front-end development with hot reload: run `run.py`, then `cd frontend && npm run dev`
(<http://127.0.0.1:5173>, proxies to the backend).

Tests: `cd backend && .venv/bin/python -m pytest -q` (≈20 s, includes the exhaustive 5-card evaluator check).
Headless demo: `.venv/bin/python scripts/replay_headless.py synthetic_blackjack` (or `synthetic_holdem`).

### Live overlay

Click **▣ Overlay** in the dashboard, or run `python backend/overlay.py`. A small always-on-top window shows
the current recommendation or equity, the flags (UNCERTAIN, STALE, PAUSED, SYNTHETIC) and the analysis age, updated
every 300 ms from the local service. Drag it outside the captured region: if it overlaps, it is captured too and
would confuse recognition, so it warns "OVERLAPS CAPTURE". It only displays information and never clicks or sends input.
It is an ordinary visible window and is not hidden from screen recording.

### Chess post-game review

Dashboard → **Chess review**. Paste the PGN of a finished game. Stockfish analyses every position locally,
and the review shows the engine's best move and line, the classification (best / good / inaccuracy / mistake /
blunder, using Lichess-style winning-chance thresholds), per-side accuracy and a win-probability graph.
Install the engine with `python backend/scripts/get_stockfish.py` (official GPLv3 release into `backend/engines/`),
or set `STOCKFISH_PATH` in `.env`. Chess is post-game only: there is no live board reading.

### Optional: Claude

Copy `.env.example` to `.env` and set `ANTHROPIC_API_KEY`. The model is set by `CLAUDE_MODEL`
(default `claude-opus-5-5`). The key stays on the backend and is never sent to the browser or logged.

## Architecture

```
selected window/region (mss + Win32)  ─┐
replay folder (replay.json + frames)  ─┴─► change detection ─► local recognizer (OpenCV templates)
      ─► Observation{region, value, confidence, timestamp}
      ─► Stabilizer (N identical confident frames to commit; flicker → "uncertain")
      ─► BlackjackTracker / PokerTracker (hand lifecycle, shoe continuity, streets, betting history)
      ─► state version (hash of committed state)
      ─► analysis.py (deterministic engines; suppression with reasons)
      ─► Claude explainer (optional; schema + fact validation; discarded if the version moved on)
      ─► FastAPI on 127.0.0.1 ─► WebSocket ─► React dashboard
```

| Path | Purpose |
|---|---|
| `backend/app/engines/bj_rules.py` | rule config, totals, legal actions with reasons |
| `backend/app/engines/bj_strategy.py` | 4–8 deck basic-strategy chart (S17 + H17 deviations), auditable resolution |
| `backend/app/engines/bj_ev.py` | dealer outcome distributions, stand/hit/double/split/surrender EV |
| `backend/app/engines/poker_eval.py` | 5–7 card evaluator |
| `backend/app/engines/poker_equity.py` | range parser, exact/Monte Carlo equity, pot odds |
| `backend/app/vision/recognize.py` | local recognition (template matching; calibration templates) |
| `backend/app/vision/synth.py` | synthetic table renderer + built-in layouts |
| `backend/app/tracking/*` | stabilizer and game trackers |
| `backend/app/claude_client.py` | Claude calls, output validation, rate limits, cache, image fallback |
| `backend/app/service.py` | capture loop, versioning, staleness, corrections |
| `backend/overlay.py` | always-on-top live overlay window (Tkinter, reads the local API) |
| `backend/app/chess_review.py` | PGN review with a UCI engine (python-chess) |
| `backend/scripts/windows_smoke.py` | live-capture smoke test run by CI on a Windows desktop |
| `frontend/src` | dashboard |

## Using it on a real table (Windows)

1. Settings → Calibration: **Copy as new layout** (start from the synthetic layout for the right game).
2. Capture panel: **Screen / window**, choose the game window, **Select**. The preview shows exactly what is captured.
3. **Use current frame size**, then for each region click **draw** and drag a box on the preview. Save.
4. Label a few crops (**Teach recognition**: e.g. region `p0_0`, value `Qh`). This stores table-specific
   glyph templates under `backend/data/layouts/<name>/templates`. Indicators (buttons, markers) use a brightness threshold.
5. Start. Misreads can be corrected by clicking a region, and corrections are marked "manual".

Region ids drive the game logic:

* Blackjack: `dealer_0..5`, `p0_0..4` / `p1_0..4` (hand 1/2 after a split), `bet0`, `bet1`, `active0`, `active1`,
  `bankroll`, `btn_hit|stand|double|split|surrender`, `shuffle` (shuffle notice → fresh shoe).
* Hold'em: `hero_0..1`, `board_0..4`, `pot`, `to_call`, `hero_stack`, `hero_bet`, `hero_dealer`,
  `seatN_cards|stack|bet|dealer` (seats numbered clockwise from hero), `btn_fold|check|call|raise`.

## What was verified

* **Blackjack maths** against published infinite-deck references: dealer bust rates for every upcard
  (e.g. 2 → 35.36%, 6 → 42.32%, 10 → 21.21%), 16 v 10 stand −0.5404 / hit −0.5398, 11 v 6 double +0.6674,
  20 v 10 stand +0.5545. It is also checked against an independent Monte Carlo simulator.
* **Strategy chart audit**: every hard/soft cell is compared with the EV engine. The only differences are
  A4 v 4 and A2 v 5 (the multi-deck chart doubles, the infinite-deck model hits, < 0.01 units). They are pinned in tests.
  No pair cell differs by more than 0.01. Recommendations are never illegal across all 1,000 two-card hand × upcard combinations under 3 rule sets.
* **Poker evaluator**: exhaustive enumeration of all 2,598,960 five-card hands reproduces the standard category
  counts and the 7,462 distinct ranks.
* **Equity**: hand-counted exact cases (flush draw vs set = 7/44, a 3-way turn = 2/42 · 36/42 · 4/42),
  board-plays 3-way tie, 2-of-3 split pot, card removal, AA v KK ≈ 82% by Monte Carlo with CI.
* **State handling**: one-frame misreads ignored; low confidence leads to "uncertain" and suppression;
  cards counted once across frames and splits; manual corrections adjust counts; new-hand detection;
  shoe unknown until a shuffle, after a gap, or when counts become impossible; CSM; duplicate-card rejection;
  bit-identical frames skipped; stale flag.
* **Claude**: invented numbers, contradicting actions, guarantee language and wrong state versions are rejected.
  Late responses are discarded. Failures, no-key operation, rate limits, usage caps and cache are covered.
  These are tested with a mocked client: **no live API call was made during development** (no key available).
* **Insurance / even money**: −1/13 per unit insured in the infinite-deck model; positive only when tens exceed
  1/3 of the remaining shoe; even money is declined at 3:2 and taken at 6:5. This is verified arithmetically in tests.
* **Overlay**: the display text is derived from real pipeline states, and suppressed or stale states never show
  an action. The window was rendered under WSLg (Linux font fallback; Windows uses Segoe UI).
* **Chess review**: classification and the pipeline are checked on Scholar's and Fool's mate with an independent
  mate-in-1 test engine. A real-Stockfish test runs whenever an engine is installed. **Not run here:** downloading
  Stockfish was not permitted in the development sandbox, so live-engine review is unverified until you install it.
* **CI** (`.github/workflows/ci.yml`): tests on Ubuntu and Windows, a Windows live-capture smoke test
  (window enumeration, region and window capture, Tk window), and the frontend build.
* **End to end**: both synthetic replays run through the real recognizer and pipeline. A test asserts the exact
  blackjack decision sequence. The dashboard was driven in headless Chromium (including the manual-correction flow).

## Limitations (read these)

* **Live capture was not exercised on Windows here.** It was built and tested under WSL, which has no display, so
  everything was demonstrated with replays. The Win32 window listing and `mss` capture code is standard but
  untested on a real desktop. DPI scaling and multi-monitor offsets may need adjustment.
* **Recognition is template matching, not general OCR.** It is reliable on the synthetic table. A real table needs
  calibration (regions + labelled glyphs) and will be less robust to animations, skins and scaling. No Tesseract is used.
  Decimal amounts and currency symbols are not parsed (digits only).
* **Blackjack EV** uses an infinite-deck model, or probabilities frozen at the observed shoe composition (no removal
  within the hand). Split EV ignores resplits. The fixed chart covers
  4–8 deck peek games. Other configurations fall back to the EV-maximising action and are labelled.
* **Shoe tracking** relies on seeing every hand at the layout's regions. Other players' cards outside the regions
  are treated as unseen random cards (valid by exchangeability, but a misread that is committed and never
  corrected will bias counts).
* **Hold'em** gives showdown equity versus *your* ranges and immediate pot odds only. There is no action EV, bet
  sizing, side-pot or rake model, and betting history is inferred from visible bet amounts. Seat/position
  mapping assumes up to 6 players.
* The synthetic replays are fabricated demo data and are labelled as such everywhere.

## Privacy

Everything stays local by default: SQLite history in `backend/data/history.db`, settings and layouts in
`backend/data/`. Screenshot retention is **off** by default; when enabled, frames are saved under
`backend/data/captures/` and appear as replays. Delete either from Settings → Privacy. Claude receives only
validated state and calculator facts, plus a single region crop if you enable and use the image fallback.
Text recognized on screen is passed as data, never as instructions.
