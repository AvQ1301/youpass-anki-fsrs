from __future__ import annotations

from dataclasses import dataclass


RATING_NAMES = {1: "Again", 2: "Hard", 3: "Good", 4: "Easy"}


@dataclass(frozen=True, slots=True)
class RatingThresholds:
    easy_min: float = 100.0
    good_min: float = 85.0
    hard_min: float = 60.0

    def __post_init__(self) -> None:
        if not (0 <= self.hard_min <= self.good_min <= self.easy_min <= 100):
            raise ValueError("thresholds must satisfy 0 <= hard <= good <= easy <= 100")

    def rating(self, correct: int, total: int) -> int:
        if total <= 0 or correct < 0 or correct > total:
            raise ValueError("invalid score")
        accuracy = correct * 100.0 / total
        if accuracy >= self.easy_min:
            return 4
        if accuracy >= self.good_min:
            return 3
        if accuracy >= self.hard_min:
            return 2
        return 1


@dataclass(frozen=True, slots=True)
class BandThresholds:
    easy_min: float = 8.0
    good_min: float = 7.0
    hard_min: float = 6.0

    def __post_init__(self) -> None:
        if not (0 <= self.hard_min <= self.good_min <= self.easy_min <= 9):
            raise ValueError("band thresholds must satisfy 0 <= hard <= good <= easy <= 9")

    def rating(self, band: float) -> int:
        if not 0 <= band <= 9:
            raise ValueError("invalid IELTS band")
        if band >= self.easy_min:
            return 4
        if band >= self.good_min:
            return 3
        if band >= self.hard_min:
            return 2
        return 1
