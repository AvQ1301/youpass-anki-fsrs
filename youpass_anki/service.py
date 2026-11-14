from __future__ import annotations

from dataclasses import dataclass

from .anki import AnkiConnect, AnkiError
from .config import Settings
from .database import Database
from .models import ScoreEvent
from .scoring import BandThresholds, RatingThresholds


@dataclass(slots=True)
class ResultStatus:
    event_id: str
    duplicate: bool
    review_status: str
    rating: int
    message: str
    review_id: str | None = None
    observations_saved: int = 0


class SyncService:
    def __init__(self, settings: Settings, database: Database, anki: AnkiConnect):
        self.settings = settings
        self.database = database
        self.anki = anki

    def thresholds(self) -> RatingThresholds:
        values = self.database.get_settings()
        return RatingThresholds(
            easy_min=float(values.get("easy_min", self.settings.easy_min)),
            good_min=float(values.get("good_min", self.settings.good_min)),
            hard_min=float(values.get("hard_min", self.settings.hard_min)),
        )

    def band_thresholds(self) -> BandThresholds:
        values = self.database.get_settings()
        return BandThresholds(
            easy_min=float(values.get("band_easy_min", "8")),
            good_min=float(values.get("band_good_min", "7")),
            hard_min=float(values.get("band_hard_min", "6")),
        )

    def sync_all_to_anki(self, only_errors: bool = False) -> tuple[int, int]:
        self.anki.setup()
        synchronized = errors = 0
        for exercise in self.database.list_exercises():
            if only_errors and not exercise.get("last_sync_error"):
                continue
            if not only_errors and exercise.get("anki_note_id") and not exercise.get("last_sync_error"):
                continue
            try:
                note_id, card_id = self.anki.sync_exercise(exercise)
                self.database.update_anki_mapping(exercise["exercise_id"], note_id, card_id)
                synchronized += 1
            except Exception as exc:
                self.database.set_sync_error(exercise["exercise_id"], str(exc))
                errors += 1
        return synchronized, errors

    def retry_pending_reviews(self) -> tuple[int, int]:
        completed = errors = 0
        for attempt in self.database.pending_attempts():
            exercise = self.database.get_exercise(attempt["exercise_id"])
            if not exercise:
                self.database.mark_attempt(attempt["event_id"], "failed", error="exercise was deleted")
                errors += 1
                continue
            try:
                note_id, card_id = self.anki.sync_exercise(exercise)
                self.database.update_anki_mapping(exercise["exercise_id"], note_id, card_id)
                if self.settings.review_policy == "due_only" and not self.anki.is_due(card_id):
                    self.database.mark_attempt(attempt["event_id"], "deferred", error="card is not due")
                    continue
                review_id = self.anki.submit_review(card_id, int(attempt["rating"]))
                self.database.mark_attempt(attempt["event_id"], "completed", review_id=review_id)
                completed += 1
            except AnkiError as exc:
                # A scheduler write may have succeeded before transport failed.
                # Never retry this event automatically after an ambiguous error.
                self.database.mark_attempt(attempt["event_id"], "manual_check", error=str(exc))
                errors += 1
            except Exception as exc:
                self.database.set_sync_error(exercise["exercise_id"], str(exc))
                self.database.mark_attempt(attempt["event_id"], "pending", error=str(exc))
                errors += 1
        return completed, errors

    def process_score(self, event: ScoreEvent) -> ResultStatus:
        event.validate()
        exercise = self.database.get_exercise(event.exercise_id)
        if not exercise:
            raise ValueError(f"unknown exercise {event.exercise_id}; synchronize exercises first")
        if exercise["skill"] in {"writing", "speaking"}:
            if event.band is None:
                raise ValueError(f"{exercise['skill']} requires an official IELTS band")
            rating = self.band_thresholds().rating(event.band)
        else:
            if event.correct is None or event.total is None:
                raise ValueError(f"{exercise['skill']} requires correct and total")
            rating = self.thresholds().rating(event.correct, event.total)
        if not self.database.insert_attempt(event, rating):
            evidence = self.database.merge_attempt_evidence(event)
            attempt = self.database.get_attempt(event.event_id)
            return ResultStatus(event.event_id, True, attempt["review_status"], attempt["rating"],
                                "Result already reviewed; evidence was merged without another Anki review.",
                                attempt.get("anki_review_id"), evidence["inserted"])
        evidence = self.database.merge_attempt_evidence(event)

        # Update visible score fields without recreating the note or card.
        exercise = self.database.get_exercise(event.exercise_id)
        try:
            note_id, card_id = self.anki.sync_exercise(exercise)
            self.database.update_anki_mapping(event.exercise_id, note_id, card_id)
        except Exception as exc:
            self.database.set_sync_error(event.exercise_id, str(exc))
            self.database.mark_attempt(event.event_id, "pending", error=f"metadata sync failed: {exc}")
            return ResultStatus(event.event_id, False, "pending", rating,
                                "Score saved. Start Anki and retry synchronization before review submission.",
                                observations_saved=evidence["inserted"])

        if self.settings.review_policy == "due_only" and not self.anki.is_due(card_id):
            self.database.mark_attempt(event.event_id, "deferred", error="card is not due")
            return ResultStatus(event.event_id, False, "deferred", rating,
                                "Score saved, but no Anki review was added because the card is not due.",
                                observations_saved=evidence["inserted"])

        try:
            review_id = self.anki.submit_review(card_id, rating)
        except AnkiError as exc:
            # A dropped HTTP response can be ambiguous. Never auto-retry and risk a
            # second revlog entry; require the user to inspect Anki/retry manually.
            self.database.mark_attempt(event.event_id, "manual_check", error=str(exc))
            return ResultStatus(event.event_id, False, "manual_check", rating,
                                "Score saved. Anki response was ambiguous; inspect card history before retrying.",
                                observations_saved=evidence["inserted"])
        self.database.mark_attempt(event.event_id, "completed", review_id=review_id)
        return ResultStatus(event.event_id, False, "completed", rating,
                            "Anki accepted a native review; FSRS selected the next schedule.", review_id,
                            evidence["inserted"])
