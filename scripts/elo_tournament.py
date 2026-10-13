"""Swiss-system Elo tournament for final clip ranking.

Point-based scoring compresses candidates at the top of the board — when
every finalist is «8/10», a score cannot pick the actual best moment.
Pairwise comparison forces discrimination: «which of THESE TWO clips is
the stronger Short?» is a question any judge answers reliably.

This module runs a deterministic Swiss-style tournament: each round,
candidates with similar standings meet, nobody fights the same opponent
twice, and Elo ratings update after every judgement. The judge itself is
*injected* — an LLM judge for production (``llm_judge``), any heuristic
for tests and offline runs — so the mechanics are fully reproducible:
same items, same judge answers, same seed → same final ranking.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

INITIAL_RATING = 1500.0
K_FACTOR = 32.0
RANKING_FILENAME = "elo_ranking.json"

Judge = Callable[[Dict[str, Any], Dict[str, Any]], str]  # -> "a" | "b" | "draw"


def _expected(rating_a: float, rating_b: float) -> float:
    return 1.0 / (1.0 + math.pow(10.0, (rating_b - rating_a) / 400.0))


def _updated(rating_a: float, rating_b: float, score_a: float) -> Tuple[float, float]:
    expected_a = _expected(rating_a, rating_b)
    delta = K_FACTOR * (score_a - expected_a)
    return rating_a + delta, rating_b - delta


def _default_rounds(count: int) -> int:
    if count < 2:
        return 0
    return min(count - 1, max(2, math.ceil(math.log2(count))))


def _pair_round(
    entries: List[Dict[str, Any]],
    played: set,
) -> List[Tuple[int, int]]:
    """Swiss pairing: order by standing, pair neighbours, skip rematches."""
    order = sorted(
        range(len(entries)),
        key=lambda i: (-entries[i]["wins"], -entries[i]["rating"], i),
    )
    pairs: List[Tuple[int, int]] = []
    used = set()
    for index in order:
        if index in used:
            continue
        opponent = None
        for other in order:
            if other == index or other in used:
                continue
            if (index, other) in played or (other, index) in played:
                continue
            opponent = other
            break
        if opponent is None:
            continue
        used.add(index)
        used.add(opponent)
        pairs.append((index, opponent))
    return pairs


def run_tournament(
    items: Sequence[Dict[str, Any]],
    judge: Judge,
    *,
    rounds: Optional[int] = None,
    seed: int = 42,
) -> Dict[str, Any]:
    """Run the tournament. Deterministic given the same judge answers.

    ``judge(a, b)`` returns "a" (a wins), "b", or "draw". Items are never
    mutated; the ranking carries copies annotated with Elo stats.
    """
    count = len(items)
    entries = [{
        "index": index,
        "item": items[index],
        "rating": INITIAL_RATING,
        "wins": 0, "losses": 0, "draws": 0,
    } for index in range(count)]
    total_rounds = _default_rounds(count) if rounds is None else max(0, int(rounds))

    rng = random.Random(seed)
    played: set = set()
    pairings = 0
    for _ in range(total_rounds):
        pairs = _pair_round(entries, played)
        if not pairs:
            break
        # deterministic match order inside the round
        rng.shuffle(pairs)
        for index_a, index_b in pairs:
            played.add((index_a, index_b))
            entry_a, entry_b = entries[index_a], entries[index_b]
            verdict = judge(entry_a["item"], entry_b["item"])
            verdict = verdict if verdict in ("a", "b", "draw") else "draw"
            score_a = {"a": 1.0, "b": 0.0, "draw": 0.5}[verdict]
            entry_a["rating"], entry_b["rating"] = _updated(
                entry_a["rating"], entry_b["rating"], score_a)
            if verdict == "a":
                entry_a["wins"] += 1
                entry_b["losses"] += 1
            elif verdict == "b":
                entry_b["wins"] += 1
                entry_a["losses"] += 1
            else:
                entry_a["draws"] += 1
                entry_b["draws"] += 1
            pairings += 1

    ranking = sorted(entries, key=lambda e: (-e["rating"], e["index"]))
    return {
        "rounds": total_rounds,
        "pairings": pairings,
        "seed": seed,
        "ranking": [{
            "rank": position + 1,
            "index": entry["index"],
            "elo": round(entry["rating"], 1),
            "wins": entry["wins"],
            "losses": entry["losses"],
            "draws": entry["draws"],
            "item": entry["item"],
        } for position, entry in enumerate(ranking)],
    }


_JUDGE_PROMPT = """أمامك مقطعان مرشحان من نفس الفيديو. أيّهما أقوى كافتتاحية Short عربي — خطافاً ولحظةً واكتمالاً؟

المقطع A — «{title_a}»
{text_a}

المقطع B — «{title_b}»
{text_b}

أجب بحرف واحد فقط: A أو B أو DRAW (تعادل)."""


def _brief(segment: Dict[str, Any], bound: int) -> str:
    text = str(segment.get("transcript_text") or segment.get("caption") or "").strip()
    return text[:bound] if text else "(بلا نص)"


def llm_judge(
    llm_call: Callable[[str], str],
    *,
    transcript_bound: int = 500,
) -> Judge:
    """Build a pairwise judge backed by an LLM. Unreadable answers are
    scored as a draw — a confused judge must not corrupt the bracket."""
    def judge(a: Dict[str, Any], b: Dict[str, Any]) -> str:
        prompt = _JUDGE_PROMPT.format(
            title_a=a.get("recommended_title") or a.get("title") or "",
            text_a=_brief(a, transcript_bound),
            title_b=b.get("recommended_title") or b.get("title") or "",
            text_b=_brief(b, transcript_bound),
        )
        try:
            answer = str(llm_call(prompt) or "").strip().upper()
        except Exception:  # noqa: BLE001 - judge outage => draw, never crash
            return "draw"
        if "DRAW" in answer:
            return "draw"
        for token in ("A", "B"):
            if answer.startswith(token) or answer == token:
                return token.lower()
        if "A" in answer and "B" not in answer:
            return "a"
        if "B" in answer and "A" not in answer:
            return "b"
        return "draw"
    return judge


def score_judge(a: Dict[str, Any], b: Dict[str, Any]) -> str:
    """Offline judge: higher score wins, ties are draws. Deterministic."""
    score_a = float(a.get("score") or 0.0)
    score_b = float(b.get("score") or 0.0)
    if score_a > score_b:
        return "a"
    if score_b > score_a:
        return "b"
    return "draw"


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Swiss Elo tournament over a project's candidate clips.")
    parser.add_argument("--project", required=True, help="Project folder with viral_segments.txt")
    parser.add_argument("--backend", default="score", choices=["score", "gemini", "g4f"],
                        help="judge: offline score comparison or an LLM backend")
    parser.add_argument("--top", type=int, default=10, help="candidates entering the bracket")
    parser.add_argument("--rounds", type=int, default=None, help="override Swiss round count")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)

    from webui import segments_review
    segments = segments_review.load_segments(args.project)
    if not segments:
        print("[elo] no segments found in {}".format(args.project))
        return 2

    candidates = sorted(
        segments, key=lambda item: float(item.get("score") or 0.0), reverse=True
    )[: max(2, int(args.top))]

    if args.backend == "score":
        judge: Judge = score_judge
    else:
        from scripts.editor_review import _load_llm
        judge = llm_judge(_load_llm(args.backend))

    result = run_tournament(candidates, judge, rounds=args.rounds, seed=args.seed)
    for row in result["ranking"]:
        title = row["item"].get("recommended_title") or row["item"].get("title") or ""
        print("#{rank}  Elo {elo:>6}  W{wins}/L{losses}/D{draws}  {title}".format(
            title=str(title)[:60], **row))

    report_path = os.path.join(args.project, RANKING_FILENAME)
    serialisable = dict(result)
    serialisable["ranking"] = [
        {key: value for key, value in row.items() if key != "item"}
        | {"title": row["item"].get("recommended_title") or row["item"].get("title")}
        for row in result["ranking"]
    ]
    with open(report_path, "w", encoding="utf-8") as handle:
        json.dump(serialisable, handle, ensure_ascii=False, indent=2)
    print("[elo] report: {}".format(report_path))
    return 0


__all__ = [
    "INITIAL_RATING", "K_FACTOR", "RANKING_FILENAME", "llm_judge",
    "main", "run_tournament", "score_judge",
]


if __name__ == "__main__":
    raise SystemExit(main())
