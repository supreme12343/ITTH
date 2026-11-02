import pytest

from teacher_tools.learning_arcade import GAME_CATALOG, evaluate_answer


def test_arcade_has_five_distinct_learning_games():
    assert len(GAME_CATALOG) == 5
    assert len({game["id"] for game in GAME_CATALOG}) == 5
    assert all(game["questions"] for game in GAME_CATALOG)


def test_fraction_forge_gives_explanation_for_correct_and_incorrect_answers():
    right = evaluate_answer("fraction-forge", 0, "0.75")
    wrong = evaluate_answer("fraction-forge", 0, "0.5")
    assert right["correct"] is True
    assert "three quarters" in right["explanation"].lower()
    assert wrong["correct"] is False
    assert wrong["answer"].startswith("0.75")
    assert evaluate_answer("fraction-forge", 1, "1.50")["correct"] is True


def test_timeline_tactician_requires_the_full_event_order():
    assert evaluate_answer("timeline-tactician", 0, "B, A, C")["correct"] is False
    assert evaluate_answer("timeline-tactician", 0, "A, B, C")["correct"] is True


def test_unknown_game_and_question_are_rejected():
    with pytest.raises(ValueError):
        evaluate_answer("made-up-game", 0, "answer")
    with pytest.raises(ValueError):
        evaluate_answer("fraction-forge", 999, "answer")


def test_every_authored_game_answer_has_feedback():
    for game in GAME_CATALOG:
        for index, question in enumerate(game["questions"]):
            answer = question["answer"]
            if isinstance(answer, list):
                answer = ",".join(answer)
            result = evaluate_answer(game["id"], index, str(answer))
            assert result["correct"] is True
            assert result["explanation"]
