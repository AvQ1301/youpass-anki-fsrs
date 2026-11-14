from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(slots=True)
class Exercise:
    exercise_id: str
    title: str
    url: str
    skill: str = "reading"
    passage_number: int | None = None
    section_number: int | None = None
    part_number: int | None = None
    question_types: list[str] = field(default_factory=list)
    question_count: int | None = None
    completion_status: str = "unknown"
    last_score: int | None = None
    last_total: int | None = None
    best_score: int | None = None
    best_total: int | None = None
    last_band: float | None = None
    best_band: float | None = None
    attempt_count: int = 0
    last_attempt_at: str | None = None
    anki_note_id: int | None = None
    anki_card_id: int | None = None
    source_updated_at: str | None = None
    discovered_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)


@dataclass(slots=True)
class ScoreEvent:
    event_id: str
    exercise_id: str
    correct: int | None = None
    total: int | None = None
    band: float | None = None
    attempted_at: str = field(default_factory=utc_now)
    source: str = "youpass-official-response"
    payload: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.event_id.strip():
            raise ValueError("event_id is required")
        if not self.exercise_id.strip():
            raise ValueError("exercise_id is required")
        has_accuracy = self.correct is not None or self.total is not None
        has_band = self.band is not None
        if has_accuracy == has_band:
            raise ValueError("provide either correct/total or band")
        if has_accuracy and (self.correct is None or self.total is None or self.total <= 0
                             or self.correct < 0 or self.correct > self.total):
            raise ValueError("score must satisfy 0 <= correct <= total and total > 0")
        if has_band and not (0 <= self.band <= 9):
            raise ValueError("band must be between 0 and 9")

    @property
    def score_kind(self) -> str:
        return "band" if self.band is not None else "accuracy"

    @property
    def accuracy(self) -> float:
        if self.band is not None:
            return self.band * 100.0 / 9.0
        assert self.correct is not None and self.total is not None
        return self.correct * 100.0 / self.total
