from __future__ import annotations

import re
from typing import Any

from .models import Exercise

QUESTION_TAG_CODES = {
    "MATCHING_HEADING", "TRUE_FALSE", "YES_NO", "MULTIPLE_CHOICE_ONE",
    "MATCHING_INFO", "MATCHING_FEATURE", "MATCHING_FEATURES", "MULTIPLE_CHOICE_MANY",
    "MAP_DIAGRAM", "FILL_BLANK", "OTHERS",
}


def parse_exercise(item: dict[str, Any], exercise_base: str, skill: str = "reading",
                   part_number: int | None = None, topic_title: str | None = None) -> Exercise:
    # Keep legacy Reading IDs unchanged so existing SQLite/Anki scheduling
    # mappings survive this multi-skill upgrade. Other skills are namespaced.
    exercise_id = str(item["id"]) if skill == "reading" else f"{skill}:{item['id']}"
    tags = item.get("tags") or []
    passage = None
    question_types: list[str] = []
    for tag in tags:
        code = str(tag.get("code") or "")
        title = str(tag.get("title") or "").strip()
        match = re.fullmatch(r"(?:passage|section)_(\d)", code, re.IGNORECASE)
        if match:
            passage = int(match.group(1))
        elif code in QUESTION_TAG_CODES and title and title not in question_types:
            question_types.append(title)
    return Exercise(
        exercise_id=exercise_id,
        title=str(item.get("title") or f"YouPass {skill.title()} {item['id']}").strip(),
        url=f"{exercise_base.rstrip('/')}/{_skill_path(skill, part_number)}/{item['id']}",
        skill=skill,
        passage_number=passage if skill == "reading" else None,
        section_number=passage if skill == "listening" else None,
        part_number=part_number or item.get("writing_task_type"),
        question_types=question_types or ([topic_title] if topic_title else []),
        question_count=item.get("total_questions") or (len(item.get("questions") or []) or None),
        completion_status=_completion_status(item),
        source_updated_at=item.get("date_updated"),
    )


def _skill_path(skill: str, part_number: int | None) -> str:
    if skill == "writing":
        return f"writing-task-{part_number or 1}"
    if skill == "speaking":
        return f"speaking-part-{part_number or 1}"
    return skill


def _completion_status(item: dict[str, Any]) -> str:
    status = item.get("completion_status") or item.get("user_status")
    if status is None:
        return "unknown"
    text = str(status).lower()
    return "finished" if text in {"finished", "completed", "reviewed", "true", "1"} else "unfinished"


def count_questions(detail: dict[str, Any]) -> int | None:
    root = detail.get("data", detail)
    if isinstance(root, dict) and isinstance(root.get("data"), dict):
        root = root["data"]
    parts = root.get("parts") if isinstance(root, dict) else None
    if not parts:
        return None
    ids: set[str] = set()

    def visit(value: Any, parent_key: str | None = None) -> None:
        if isinstance(value, dict):
            # Only objects inside a `questions` array are individual IELTS
            # questions. Question-set containers also have id/question_type.
            if parent_key == "questions" and "question_type" in value and "id" in value:
                ids.add(str(value["id"]))
            for key, child in value.items():
                visit(child, key)
        elif isinstance(value, list):
            for child in value:
                visit(child, parent_key)

    visit(parts)
    return len(ids) or None


def parse_score_payload(payload: dict[str, Any]) -> tuple[int, int]:
    def visit(value: Any, depth: int = 0) -> tuple[int, int] | None:
        if not isinstance(value, dict) or depth > 6:
            return None
        correct, total = value.get("correct"), value.get("total")
        if isinstance(correct, (int, float)) and isinstance(total, (int, float)):
            correct_i, total_i = int(correct), int(total)
            if total_i > 0 and 0 <= correct_i <= total_i:
                return correct_i, total_i
        for child in value.values():
            found = visit(child, depth + 1)
            if found:
                return found
        return None

    result = visit(payload)
    if result:
        return result
    raise ValueError("official payload does not contain a valid correct/total score")
