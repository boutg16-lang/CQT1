from scripts import elo_tournament


def _item(index, score=50.0):
    return {"title": "clip-%d" % index, "score": score,
            "transcript_text": "نص المقطع رقم %d" % index}


def test_expected_and_updated_are_symmetric():
    expected = elo_tournament._expected(1500.0, 1500.0)
    assert expected == 0.5
    new_a, new_b = elo_tournament._updated(1500.0, 1500.0, 1.0)
    assert round(new_a, 4) == 1516.0
    assert round(new_b, 4) == 1484.0


def test_stronger_judge_produces_correct_order():
    items = [_item(0, 10.0), _item(1, 90.0), _item(2, 50.0), _item(3, 70.0)]
    result = elo_tournament.run_tournament(items, elo_tournament.score_judge, seed=7)
    ranking = result["ranking"]
    # the 90-point clip must finish first, the 10-point clip last
    assert ranking[0]["index"] == 1
    assert ranking[-1]["index"] == 0
    assert ranking[0]["elo"] > ranking[-1]["elo"]
    assert result["pairings"] > 0


def test_tournament_is_deterministic_with_same_seed():
    items = [_item(index, float(index * 13 % 50)) for index in range(8)]
    first = elo_tournament.run_tournament(items, elo_tournament.score_judge, seed=123)
    second = elo_tournament.run_tournament(items, elo_tournament.score_judge, seed=123)
    assert [(r["index"], r["elo"]) for r in first["ranking"]] == [
        (r["index"], r["elo"]) for r in second["ranking"]]


def test_no_rematches_within_tournament():
    seen = []

    def spy_judge(a, b):
        seen.append((a["title"], b["title"]))
        return "a"

    items = [_item(index, float(index)) for index in range(6)]
    result = elo_tournament.run_tournament(items, spy_judge, seed=3)
    assert len(seen) == len(set(seen))  # every pairing unique
    assert result["pairings"] == len(seen)


def test_odd_field_gives_someone_a_rest():
    items = [_item(index) for index in range(5)]
    result = elo_tournament.run_tournament(items, elo_tournament.score_judge, seed=1)
    total_games = sum(r["wins"] + r["losses"] + r["draws"] for r in result["ranking"])
    assert total_games == result["pairings"] * 2


def test_single_candidate_and_empty_input():
    assert elo_tournament.run_tournament([], elo_tournament.score_judge)["ranking"] == []
    one = elo_tournament.run_tournament([_item(0)], elo_tournament.score_judge)
    assert one["pairings"] == 0
    assert one["ranking"][0]["rank"] == 1


def test_items_are_not_mutated():
    items = [_item(0, 60.0), _item(1, 70.0)]
    elo_tournament.run_tournament(items, elo_tournament.score_judge, seed=9)
    assert items[0] == {"title": "clip-0", "score": 60.0, "transcript_text": "نص المقطع رقم 0"}


def test_score_judge_verdicts():
    assert elo_tournament.score_judge({"score": 5}, {"score": 3}) == "a"
    assert elo_tournament.score_judge({"score": 1}, {"score": 9}) == "b"
    assert elo_tournament.score_judge({"score": 4}, {"score": 4}) == "draw"
    assert elo_tournament.score_judge({}, {}) == "draw"


def test_llm_judge_parses_answers():
    verdicts = []

    def make_llm(answer):
        def llm(prompt):
            verdicts.append(prompt)
            return answer
        return llm

    assert elo_tournament.llm_judge(make_llm("A"))(_item(0), _item(1)) == "a"
    assert elo_tournament.llm_judge(make_llm("b."))(_item(0), _item(1)) == "b"
    assert elo_tournament.llm_judge(make_llm("DRAW"))(_item(0), _item(1)) == "draw"
    assert elo_tournament.llm_judge(make_llm("إجابة غير مفهومة"))(_item(0), _item(1)) == "draw"
    assert "المقطع A" in verdicts[0] and "المقطع B" in verdicts[0]


def test_llm_judge_falls_back_to_draw_on_backend_failure():
    def broken(prompt):
        raise RuntimeError("boom")

    judge = elo_tournament.llm_judge(broken)
    assert judge(_item(0), _item(1)) == "draw"
