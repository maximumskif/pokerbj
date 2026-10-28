"""Post-game chess review: analyse a finished game (PGN) move by move with a UCI engine.

Deliberately post-game only: input is a PGN of a completed game, not the screen.
Classification uses Lichess-style winning chances (cp -> [-1, 1]) so a 100cp
loss in a won position counts less than in a balanced one.
"""
from __future__ import annotations

import io
import math
import os
import shutil
import threading
import time
import uuid
from pathlib import Path

import chess
import chess.engine
import chess.pgn

ENGINE_DIR = Path(__file__).resolve().parent.parent / "engines"
MATE_CP = 10000

# thresholds on drop in winning chances (scale -1..1), as used by Lichess
THRESHOLDS = [("blunder", 0.3), ("mistake", 0.2), ("inaccuracy", 0.1)]


def find_engine() -> str | None:
    env = os.environ.get("STOCKFISH_PATH")
    if env and Path(env).exists():
        return env
    for name in ("stockfish.exe", "stockfish"):
        p = ENGINE_DIR / name
        if p.exists():
            return str(p)
    return shutil.which("stockfish")


def winning_chances(cp: int) -> float:
    """Lichess formula: 2 / (1 + exp(-0.00368208 * cp)) - 1, in [-1, 1]."""
    cp = max(-MATE_CP, min(MATE_CP, cp))
    return 2 / (1 + math.exp(-0.00368208 * cp)) - 1


def move_accuracy(win_before: float, win_after: float) -> float:
    """Lichess-style per-move accuracy from win% (0-100) for the mover."""
    loss = max(0.0, win_before - win_after)
    return max(0.0, min(100.0, 103.1668 * math.exp(-0.04354 * loss) - 3.1669))


def classify(wc_before: float, wc_after: float) -> str:
    """wc_* are winning chances for the side that moved, before/after its move."""
    drop = wc_before - wc_after
    for label, t in THRESHOLDS:
        if drop >= t:
            return label
    return "good"


def score_cp(score: chess.engine.PovScore, pov: chess.Color) -> tuple[int, int | None]:
    """(centipawns from pov, mate-in-N from pov or None)."""
    s = score.pov(pov)
    mate = s.mate()
    return s.score(mate_score=MATE_CP), mate


def parse_pgn(text: str) -> chess.pgn.Game:
    game = chess.pgn.read_game(io.StringIO(text))
    if game is None or game.errors:
        errs = "; ".join(str(e) for e in (game.errors if game else [])) or "no game found"
        raise ValueError(f"cannot parse PGN: {errs}")
    if not any(True for _ in game.mainline_moves()):
        raise ValueError("PGN contains no moves")
    return game


def review(game: chess.pgn.Game, engine, limit: chess.engine.Limit, progress=None) -> dict:
    """engine: anything with .analyse(board, limit) -> {'score': PovScore, 'pv': [...]} (python-chess API)."""
    board = game.board()
    moves = list(game.mainline_moves())
    evals = []  # analysis of each position before move i (and the final position)
    for i in range(len(moves) + 1):
        if board.is_game_over():
            evals.append(None)
        else:
            evals.append(engine.analyse(board, limit))
        if i < len(moves):
            board.push(moves[i])
        if progress:
            progress(i + 1, len(moves) + 1)

    board = game.board()
    out_moves = []
    acc = {chess.WHITE: [], chess.BLACK: []}
    counts = {c: {"inaccuracy": 0, "mistake": 0, "blunder": 0} for c in ("white", "black")}
    for i, mv in enumerate(moves):
        mover = board.turn
        before = evals[i]
        san = board.san(mv)
        fen_before = board.fen()
        best_uci = before["pv"][0].uci() if before and before.get("pv") else None
        best_san = board.san(before["pv"][0]) if best_uci else None
        pv_san = _pv_san(board, before.get("pv", [])[:6]) if before else []
        board.push(mv)
        after = evals[i + 1]
        cp_before, mate_before = score_cp(before["score"], mover)
        if after is None:  # game over after this move
            if board.is_checkmate():
                cp_after, mate_after = MATE_CP, 0
            else:
                cp_after, mate_after = 0, None
        else:
            cp_after, mate_after = score_cp(after["score"], mover)
        wc_b, wc_a = winning_chances(cp_before), winning_chances(cp_after)
        label = "best" if best_uci == mv.uci() else classify(wc_b, wc_a)
        if label in ("inaccuracy", "mistake", "blunder"):
            counts["white" if mover == chess.WHITE else "black"][label] += 1
        note = None
        if mate_after is not None and mate_after < 0 and not (mate_before is not None and mate_before < 0):
            note = f"allows forced mate in {-mate_after}" + (f"; {best_san} avoids it" if best_san and best_uci != mv.uci() else "")
        elif mate_before is not None and mate_before > 0 and not (mate_after is not None and mate_after > 0):
            note = f"missed a forced mate in {mate_before}" + (f" ({best_san})" if best_san else "")
        a = move_accuracy(50 + 50 * wc_b, 50 + 50 * wc_a)
        acc[mover].append(a)
        white_cp, white_mate = score_cp(after["score"], chess.WHITE) if after else (
            (MATE_CP if mover == chess.WHITE else -MATE_CP, 0) if board.is_checkmate() else (0, None))
        out_moves.append({
            "ply": i + 1, "move_number": i // 2 + 1, "color": "white" if mover == chess.WHITE else "black",
            "san": san, "uci": mv.uci(), "fen_before": fen_before, "fen_after": board.fen(),
            "best_san": best_san, "best_uci": best_uci, "best_line": pv_san,
            "eval_before_cp": cp_before, "mate_before": mate_before,
            "eval_after_cp": cp_after, "mate_after": mate_after,
            "white_eval_cp": white_cp, "white_mate": white_mate,
            "white_win_pct": round(50 + 50 * winning_chances(white_cp), 1),
            "win_chance_drop": round(max(0.0, wc_b - wc_a), 3),
            "classification": label, "accuracy": round(a, 1), "note": note,
        })
    h = game.headers
    return {
        "headers": {k: h.get(k) for k in ("Event", "Site", "Date", "White", "Black", "Result") if h.get(k)},
        "start_fen": game.board().fen(),
        "moves": out_moves,
        "summary": {
            "white": {"accuracy": round(sum(acc[chess.WHITE]) / max(1, len(acc[chess.WHITE])), 1), **counts["white"]},
            "black": {"accuracy": round(sum(acc[chess.BLACK]) / max(1, len(acc[chess.BLACK])), 1), **counts["black"]},
            "final": board.result(claim_draw=True) if board.is_game_over(claim_draw=True) else h.get("Result", "*"),
        },
        "method": {
            "classification": "drop in Lichess-style winning chances: inaccuracy >= 0.1, mistake >= 0.2, blunder >= 0.3 (scale -1..1); 'best' = engine's top move",
            "accuracy": "Lichess-style per-move accuracy from win% loss, averaged per side (approximation)",
            "note": "engine evaluations depend on depth/time; small differences between engines and settings are normal",
        },
    }


def _pv_san(board: chess.Board, pv: list[chess.Move]) -> list[str]:
    b = board.copy()
    out = []
    for m in pv:
        if m not in b.legal_moves:
            break
        out.append(b.san(m))
        b.push(m)
    return out


class ReviewJobs:
    """Runs reviews in a background thread; the API polls progress."""

    def __init__(self):
        self.jobs: dict[str, dict] = {}

    def start(self, pgn_text: str, depth: int = 16, time_per_move: float | None = None) -> str:
        game = parse_pgn(pgn_text)  # validate before starting
        path = find_engine()
        if not path:
            raise RuntimeError("Stockfish not found. Run `python scripts/get_stockfish.py` or set STOCKFISH_PATH in .env.")
        job_id = uuid.uuid4().hex[:12]
        job = {"id": job_id, "status": "running", "progress": 0.0, "started_at": time.time(), "result": None, "error": None,
               "settings": {"depth": depth, "time_per_move": time_per_move}, "engine": Path(path).name}
        self.jobs[job_id] = job
        if len(self.jobs) > 20:
            for old in sorted(self.jobs, key=lambda k: self.jobs[k]["started_at"])[:-20]:
                self.jobs.pop(old, None)

        def run():
            try:
                with chess.engine.SimpleEngine.popen_uci(path) as eng:
                    eng.configure({"Threads": max(1, (os.cpu_count() or 2) // 2), "Hash": 128})
                    job["engine"] = eng.id.get("name", job["engine"])
                    limit = chess.engine.Limit(time=time_per_move) if time_per_move else chess.engine.Limit(depth=depth)
                    job["result"] = review(game, eng, limit, lambda d, n: job.update(progress=round(d / n, 3)))
                job["status"] = "done"
            except Exception as e:  # engine crash, bad binary, etc.
                job["status"], job["error"] = "error", f"{type(e).__name__}: {e}"

        threading.Thread(target=run, daemon=True).start()
        return job_id

    def get(self, job_id: str) -> dict | None:
        return self.jobs.get(job_id)
