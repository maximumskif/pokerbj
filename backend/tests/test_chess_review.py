"""Chess post-game review. A tiny independent fake engine (mate-in-1 + material) verifies the
review/classification pipeline on famous miniatures; a real Stockfish run is tested when installed."""
import chess
import chess.engine
import pytest

from app.chess_review import classify, find_engine, parse_pgn, review, winning_chances

VALUES = {chess.PAWN: 100, chess.KNIGHT: 300, chess.BISHOP: 300, chess.ROOK: 500, chess.QUEEN: 900, chess.KING: 0}


class FakeEngine:
    """Side-to-move POV: mate in 1 if available, else material balance; pv = mating move or best capture."""

    def analyse(self, board, limit):
        for m in board.legal_moves:
            board.push(m)
            mate = board.is_checkmate()
            board.pop()
            if mate:
                return {"score": chess.engine.PovScore(chess.engine.Mate(1), board.turn), "pv": [m]}

        def material(b, color):
            return sum(VALUES[p.piece_type] * (1 if p.color == color else -1) for p in b.piece_map().values())

        best = max(board.legal_moves, key=lambda m: (VALUES[board.piece_at(m.to_square).piece_type] if board.piece_at(m.to_square) else 0, -m.from_square))
        return {"score": chess.engine.PovScore(chess.engine.Cp(material(board, board.turn)), board.turn), "pv": [best]}


SCHOLAR = "[White \"A\"]\n[Black \"B\"]\n[Result \"1-0\"]\n\n1. e4 e5 2. Bc4 Nc6 3. Qh5 Nf6 4. Qxf7# 1-0"
FOOL = "[Result \"0-1\"]\n\n1. f3 e5 2. g4 Qh4# 0-1"


def test_scholars_mate_blunder_and_best():
    r = review(parse_pgn(SCHOLAR), FakeEngine(), chess.engine.Limit(depth=1))
    by_san = {m["san"]: m for m in r["moves"]}
    assert by_san["Nf6"]["classification"] == "blunder"   # allows Qxf7# (mate in 1)
    assert by_san["Nf6"]["mate_after"] == -1               # from Black's view: mated next move
    assert by_san["Nf6"]["note"].startswith("allows forced mate in 1")
    assert by_san["Qxf7#"]["classification"] == "best"
    assert by_san["Qxf7#"]["note"] is None                 # delivering mate is not "missing" it
    assert r["summary"]["final"] == "1-0"
    assert r["summary"]["black"]["blunder"] == 1 and r["summary"]["white"]["blunder"] == 0
    assert r["moves"][-1]["white_win_pct"] == 100.0


def test_fools_mate():
    r = review(parse_pgn(FOOL), FakeEngine(), chess.engine.Limit(depth=1))
    g4 = r["moves"][2]
    assert g4["san"] == "g4" and g4["classification"] == "blunder"
    assert r["moves"][3]["best_san"] == "Qh4#" and r["moves"][3]["classification"] == "best"
    assert r["summary"]["final"] == "0-1"


class ShallowEngine(FakeEngine):
    """Mimics a horizon effect: before 3...Nf6 it recommends Nf6 at 0.00, missing the reply Qxf7#."""

    def analyse(self, board, limit):
        if board.fullmove_number == 3 and board.turn == chess.BLACK:
            return {"score": chess.engine.PovScore(chess.engine.Cp(0), board.turn), "pv": [board.parse_san("Nf6")]}
        return super().analyse(board, limit)


def test_engine_top_move_still_flagged_when_post_move_eval_collapses():
    r = review(parse_pgn(SCHOLAR), ShallowEngine(), chess.engine.Limit(depth=1))
    nf6 = next(m for m in r["moves"] if m["san"] == "Nf6")
    assert nf6["best_san"] == "Nf6"
    assert nf6["classification"] == "blunder"
    assert nf6["note"] == "allows forced mate in 1"        # no "Nf6 avoids it" when Nf6 was the suggestion
    assert r["summary"]["black"]["blunder"] == 1


class NoisyEngine(FakeEngine):
    """Before 3.Qh5 it recommends Qh5 at +5.00; the post-move eval is only material (0.00)."""

    def analyse(self, board, limit):
        if board.fullmove_number == 3 and board.turn == chess.WHITE:
            return {"score": chess.engine.PovScore(chess.engine.Cp(500), board.turn), "pv": [board.parse_san("Qh5")]}
        return super().analyse(board, limit)


def test_engine_top_move_not_demoted_by_centipawn_noise():
    r = review(parse_pgn(SCHOLAR), NoisyEngine(), chess.engine.Limit(depth=1))
    qh5 = next(m for m in r["moves"] if m["san"] == "Qh5")
    assert qh5["win_chance_drop"] >= 0.3 and qh5["classification"] == "best"


def test_winning_chances_and_thresholds():
    assert winning_chances(0) == 0
    assert winning_chances(300) == pytest.approx(-winning_chances(-300))
    assert classify(0.0, -0.31) == "blunder"
    assert classify(0.0, -0.2) == "mistake"
    assert classify(0.0, -0.1) == "inaccuracy"
    assert classify(0.0, -0.05) == "good"
    # the same 150cp loss matters less when already completely winning
    assert classify(winning_chances(900), winning_chances(750)) == "good"
    assert classify(winning_chances(0), winning_chances(-150)) != "good"


def test_bad_pgn_rejected():
    with pytest.raises(ValueError):
        parse_pgn("not a game")
    with pytest.raises(ValueError):
        parse_pgn("1. e4 e5 2. Ke3 Ke6 3. Qxx9")


@pytest.mark.skipif(find_engine() is None, reason="Stockfish not installed")
def test_real_stockfish_scholars_mate():
    with chess.engine.SimpleEngine.popen_uci(find_engine()) as eng:
        r = review(parse_pgn(SCHOLAR), eng, chess.engine.Limit(depth=12))
    by_san = {m["san"]: m for m in r["moves"]}
    assert by_san["Nf6"]["classification"] == "blunder"
    assert by_san["Nf6"]["mate_after"] == -1
    assert by_san["Qxf7#"]["classification"] == "best"


OPERA = ("[White \"Morphy\"]\n[Black \"Duke Karl / Count Isouard\"]\n[Result \"1-0\"]\n\n"
         "1. e4 e5 2. Nf3 d6 3. d4 Bg4 4. dxe5 Bxf3 5. Qxf3 dxe5 6. Bc4 Nf6 7. Qb3 Qe7 8. Nc3 c6 "
         "9. Bg5 b5 10. Nxb5 cxb5 11. Bxb5+ Nbd7 12. O-O-O Rd8 13. Rxd7 Rxd7 14. Rd1 Qe6 "
         "15. Bxd7+ Nxd7 16. Qb8+ Nxb8 17. Rd8# 1-0")


@pytest.mark.skipif(find_engine() is None, reason="Stockfish not installed")
def test_depth_review_is_reproducible():
    """Depth-limited jobs run Stockfish single-threaded, so the same PGN gives the same review every time."""
    import time

    from app.chess_review import ReviewJobs

    jobs = ReviewJobs()

    def run():
        jid = jobs.start(OPERA, depth=12)
        deadline = time.time() + 120
        while jobs.get(jid)["status"] == "running" and time.time() < deadline:
            time.sleep(0.05)
        job = jobs.get(jid)
        assert job["status"] == "done", job["error"]
        assert job["settings"]["threads"] == 1
        return job["result"]["moves"]

    assert run() == run()
