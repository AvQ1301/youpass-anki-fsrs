from __future__ import annotations

from typing import Any


CRITERIA = {
    "TR": "Task Response",
    "TA": "Task Achievement",
    "CC": "Coherence & Cohesion",
    "LR": "Lexical Resource",
    "GR": "Grammatical Range & Accuracy",
    "GRA": "Grammatical Range & Accuracy",
    "FC": "Fluency & Coherence",
    "PR": "Pronunciation",
}


def unwrap(payload: dict[str, Any]) -> dict[str, Any]:
    value: Any = payload
    while isinstance(value, dict) and isinstance(value.get("data"), dict):
        value = value["data"]
    return value if isinstance(value, dict) else {}


def normalized_result(answer_payload: dict[str, Any], quiz_payload: dict[str, Any] | None,
                      skill: str, source_id: str) -> dict[str, Any]:
    answer = unwrap(answer_payload)
    quiz = unwrap(quiz_payload or {})
    answer_id = answer.get("id")
    if answer_id is None:
        raise ValueError("official answer response has no answer ID")
    result: dict[str, Any] = {
        "event_id": f"youpass-{skill}-answer-{answer_id}",
        "exercise_id": source_id if skill == "reading" else f"{skill}:{source_id}",
        "source": "youpass-official-result",
        "attempted_at": answer.get("date_created"),
        "payload": {"answer_id": str(answer_id), "observations": observations(answer, quiz)},
    }
    if skill in {"reading", "listening"}:
        summary = answer.get("summary") or {}
        correct, total = summary.get("correct"), summary.get("total")
        if not isinstance(correct, (int, float)) or not isinstance(total, (int, float)) or total <= 0:
            raise ValueError("official answer response has no valid correct/total")
        result.update(correct=int(correct), total=int(total))
    else:
        band = _find_band(answer)
        if band is None:
            raise ValueError("official answer response has no overall band yet")
        result["band"] = band
    return result


def observations(answer: dict[str, Any], quiz: dict[str, Any]) -> list[dict[str, Any]]:
    questions = _question_map(quiz)
    details = answer.get("detail") if isinstance(answer.get("detail"), list) else []
    output: list[dict[str, Any]] = []
    answered: set[str] = set()
    for detail in details:
        if not isinstance(detail, dict):
            continue
        item_key = detail.get("question_id") or detail.get("id_question")
        if item_key is None:
            continue
        item_key = str(item_key)
        answered.add(item_key)
        question = questions.get(item_key, {})
        is_correct = detail.get("is_correct")
        skipped = detail.get("is_skipped") is True
        if is_correct is False or skipped:
            output.append(_question_observation(item_key, question, detail, "skipped" if skipped else "incorrect"))
    # YouPass omits unanswered questions from `detail` in observed Listening responses.
    for item_key, question in questions.items():
        if item_key not in answered:
            output.append(_question_observation(item_key, question, {}, "skipped"))
    output.extend(_criterion_observations(answer))
    return output


def _question_observation(item_key: str, question: dict[str, Any], detail: dict[str, Any],
                          outcome: str) -> dict[str, Any]:
    correct = question.get("correct_answer") or question.get("correct_answers")
    return {
        "kind": "question_error",
        "item_key": item_key,
        "category": question.get("question_type"),
        "outcome": outcome,
        "selected_answer": _clean(detail.get("answer")),
        "correct_answer": _clean(correct),
        "source": "youpass-official-result",
        "metadata": {"order": question.get("order")},
    }


def _question_map(quiz: dict[str, Any]) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}

    def visit(value: Any, parent: str | None = None) -> None:
        if isinstance(value, dict):
            if parent == "questions" and value.get("id") is not None and value.get("question_type") is not None:
                found[str(value["id"])] = value
            for key, child in value.items():
                visit(child, key)
        elif isinstance(value, list):
            for child in value:
                visit(child, parent)

    visit(quiz.get("parts") or quiz)
    return found


def _criterion_observations(answer: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = [answer.get("band_score_detail"), answer.get("review_detail")]
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        for key, label in CRITERIA.items():
            value = candidate.get(key)
            if value is None:
                value = candidate.get(f"{key.lower()}_score")
            if isinstance(value, (int, float)) and 0 <= value <= 9 and key not in seen:
                seen.add(key)
                output.append({"kind": "criterion_score", "item_key": key, "category": label,
                               "outcome": "weak" if value < 7 else "measured", "score": value,
                               "source": "youpass-official-result"})
    return output


def _find_band(value: Any, depth: int = 0) -> float | None:
    if not isinstance(value, dict) or depth > 7:
        return None
    for key in ("overall_band_score", "band_score", "overall"):
        band = value.get(key)
        if isinstance(band, (int, float)) and 0 <= band <= 9:
            return float(band)
    for child in value.values():
        found = _find_band(child, depth + 1)
        if found is not None:
            return found
    return None


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        cleaned = [_clean(item) for item in value]
        return " | ".join(item for item in cleaned if item)[:1000] or None
    if isinstance(value, dict):
        for key in ("answer", "answers", "title", "text", "option", "value"):
            if key in value:
                return _clean(value[key])
        return None
    return str(value)[:1000]
