from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from youpass_anki.anki import AnkiConnect
from youpass_anki.config import Settings
from youpass_anki.crawler import YouPassCrawler
from youpass_anki.database import Database
from youpass_anki.evidence import normalized_result
from youpass_anki.models import Exercise, ScoreEvent
from youpass_anki.parsing import count_questions, parse_exercise, parse_score_payload
from youpass_anki.scoring import BandThresholds, RatingThresholds
from youpass_anki.service import SyncService

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def db(tmp_path: Path) -> Database:
    value = Database(tmp_path / "test.db")
    value.initialize()
    return value


def sample_exercise(exercise_id: str = "11274") -> Exercise:
    source_id = exercise_id.split(":")[-1]
    return Exercise(exercise_id=exercise_id, title="On art and artists",
                    url=f"https://e-learning.youpass.vn/practice/reading/{source_id}",
                    passage_number=3, question_types=["Gap Filling", "Matching Information"],
                    question_count=13)


def test_fixture_parsing_uses_stable_id_and_metadata() -> None:
    payload = json.loads((FIXTURES / "listing_page.json").read_text())
    item = payload["data"]["items"][0]
    exercise = parse_exercise(item, "https://e-learning.youpass.vn/practice", "reading")
    assert exercise.exercise_id == "11274"
    assert exercise.url.endswith("/11274")
    assert exercise.passage_number == 3
    assert "Matching Information" in exercise.question_types


def test_other_skill_fixtures_parse_to_distinct_ids_and_urls() -> None:
    listening = json.loads((FIXTURES / "listening_page.json").read_text())["data"]["items"][0]
    writing = json.loads((FIXTURES / "writing_page.json").read_text())["data"]["items"][0]
    speaking_topic = json.loads((FIXTURES / "speaking_page.json").read_text())["data"]["topics"][0]
    speaking = speaking_topic["quizzes"][0]
    values = [
        parse_exercise(listening, "https://e-learning.youpass.vn/practice", "listening"),
        parse_exercise(writing, "https://e-learning.youpass.vn/practice", "writing", part_number=1),
        parse_exercise(speaking, "https://e-learning.youpass.vn/practice", "speaking",
                       part_number=2, topic_title=speaking_topic["title"]),
    ]
    assert [value.skill for value in values] == ["listening", "writing", "speaking"]
    assert values[0].exercise_id.startswith("listening:") and "/listening/" in values[0].url
    assert values[0].section_number == 1
    assert values[1].part_number == 1 and "/writing-task-1/" in values[1].url
    assert values[2].part_number == 2 and "/speaking-part-2/" in values[2].url
    assert values[2].question_types == [speaking_topic["title"]]


def test_listening_answer_normalizes_score_and_errors() -> None:
    answer = json.loads((FIXTURES / "answer_result.json").read_text())
    quiz = json.loads((FIXTURES / "quiz_questions.json").read_text())
    event = normalized_result(answer, quiz, "listening", "10435")
    assert event["event_id"] == "youpass-listening-answer-900001"
    assert event["exercise_id"] == "listening:10435"
    assert (event["correct"], event["total"]) == (4, 10)
    errors = event["payload"]["observations"]
    assert len(errors) == 6
    assert sum(value["outcome"] == "incorrect" for value in errors) == 5
    assert sum(value["outcome"] == "skipped" for value in errors) == 1
    by_order = {value["metadata"]["order"]: value for value in errors}
    assert by_order[2]["selected_answer"] == "21"
    assert by_order[2]["correct_answer"] == "21.50"
    assert by_order[4]["correct_answer"] == "parking"
    assert by_order[5]["category"] == "SUMMARY_COMPLETION"


def test_question_count_from_live_fixture() -> None:
    payload = json.loads((FIXTURES / "quiz_detail.json").read_text())
    assert count_questions(payload) == 14


def test_score_payload_parsing() -> None:
    assert parse_score_payload({"data": {"answer": {"summary": {"correct": 13, "total": 13}}}}) == (13, 13)
    with pytest.raises(ValueError):
        parse_score_payload({"message": "success"})


@pytest.mark.parametrize(("correct", "total", "rating"), [(13, 13, 4), (12, 13, 3), (8, 13, 2), (6, 13, 1)])
def test_score_mapping(correct: int, total: int, rating: int) -> None:
    assert RatingThresholds().rating(correct, total) == rating


@pytest.mark.parametrize(("band", "rating"), [(8.0, 4), (7.5, 3), (6.0, 2), (5.5, 1)])
def test_band_mapping(band: float, rating: int) -> None:
    assert BandThresholds().rating(band) == rating


def test_database_upsert_prevents_duplicates_and_preserves_progress(db: Database) -> None:
    assert db.upsert_exercise(sample_exercise()) is True
    event = ScoreEvent("answer-1", "11274", 10, 13)
    assert db.insert_attempt(event, 2) is True
    changed = sample_exercise()
    changed.title = "Updated title"
    assert db.upsert_exercise(changed) is False
    row = db.get_exercise("11274")
    assert row["title"] == "Updated title"
    assert row["attempt_count"] == 1
    assert row["last_score"] == 10


def test_attempt_is_idempotent(db: Database) -> None:
    db.upsert_exercise(sample_exercise())
    event = ScoreEvent("same-result", "11274", 13, 13)
    assert db.insert_attempt(event, 4) is True
    assert db.insert_attempt(event, 4) is False
    assert db.get_exercise("11274")["attempt_count"] == 1


def test_error_evidence_merges_idempotently(db: Database) -> None:
    db.upsert_exercise(sample_exercise())
    event = ScoreEvent("evidence-1", "11274", 10, 13, payload={"observations": [
        {"kind": "question_error", "item_key": "31", "category": "YES_NO",
         "outcome": "incorrect", "selected_answer": "YES", "correct_answer": "NO"}
    ]})
    assert db.insert_attempt(event, 2)
    assert db.merge_attempt_evidence(event) == {"inserted": 1, "total": 1}
    assert db.merge_attempt_evidence(event) == {"inserted": 0, "total": 1}
    summary = db.error_summary()
    assert summary["question_errors"] == 1
    assert summary["categories"][0]["category"] == "YES_NO"
    assert db.exercise_errors("11274")[0]["selected_answer"] == "YES"


@pytest.mark.asyncio
async def test_pagination_discovers_until_reported_total(db: Database) -> None:
    fixture = json.loads((FIXTURES / "listing_page.json").read_text())
    items = fixture["data"]["items"][:2]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/v1/public/tag-positions"):
            return httpx.Response(200, json={"code": 0, "data": []})
        if "/speaking-topics/" in request.url.path:
            return httpx.Response(200, json={"code": 0, "data": {"total": 0, "page": 1,
                                                                    "page_size": 20, "topics": []}})
        page = int(request.url.path.split("/")[-6])
        body = {"code": 0, "data": {"total": 3, "page": page, "page_size": 2,
                                     "items": items if page == 1 else [dict(items[0], id=99999, title="Third")]}}
        if "/v1/public/quizzes/" in request.url.path and "/list/" not in request.url.path:
            body = {"code": 0, "data": {"parts": []}}
        return httpx.Response(200, json=body)

    settings = Settings(database_path=db.path, crawl_delay=0, crawl_enrich_details=False)
    client = httpx.AsyncClient(base_url=settings.api_url, transport=httpx.MockTransport(handler))
    result = await YouPassCrawler(settings, db, client).crawl(resume=False)
    await client.aclose()
    assert result.pages == 8
    assert result.discovered == 9
    rows = db.list_exercises()
    assert len(rows) == 9
    assert {row["skill"] for row in rows} == {"reading", "listening", "writing"}


def test_anki_creation_then_update_does_not_recreate_note() -> None:
    settings = Settings()
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body["action"])
        results = {"findNotes": [101], "updateNoteFields": None, "findCards": [202]}
        return httpx.Response(200, json={"result": results[body["action"]], "error": None})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    note_id, card_id = AnkiConnect(settings, client).sync_exercise({**vars_for_anki(), "exercise_id": "11274"})
    assert (note_id, card_id) == (101, 202)
    assert "addNote" not in calls
    assert calls == ["findNotes", "updateNoteFields", "findCards"]


def test_native_review_falls_back_when_gui_reviewer_is_inactive() -> None:
    settings = Settings()
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        action = body["action"]
        calls.append(action)
        if action == "guiCurrentCard":
            return httpx.Response(200, json={"result": None, "error": "Gui review is not currently active."})
        if action == "getReviewsOfCards":
            reviews = [] if calls.count(action) == 1 else [{"id": 777}]
            return httpx.Response(200, json={"result": {"20": reviews}, "error": None})
        if action == "answerCards":
            return httpx.Response(200, json={"result": [True], "error": None})
        raise AssertionError(action)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert AnkiConnect(settings, client).submit_review(20, 4) == "777"
    assert calls == ["getReviewsOfCards", "guiCurrentCard", "answerCards", "getReviewsOfCards"]


def vars_for_anki() -> dict:
    return {"skill": "reading", "title": "Title", "url": "https://example.test/1", "passage_number": 1,
            "section_number": None, "part_number": None, "last_band": None, "best_band": None,
            "question_types": ["Gap Filling"], "question_count": 13,
            "last_score": None, "last_total": None, "best_score": None,
            "best_total": None, "last_attempt_at": None}


class FakeAnki:
    def __init__(self, due: bool = True):
        self.due = due
        self.reviews = 0

    def sync_exercise(self, exercise):
        return 10, 20

    def is_due(self, card_id):
        return self.due

    def submit_review(self, card_id, rating):
        self.reviews += 1
        return "review-1"


def test_service_submits_one_native_review_and_is_idempotent(db: Database) -> None:
    db.upsert_exercise(sample_exercise())
    anki = FakeAnki()
    service = SyncService(Settings(database_path=db.path), db, anki)
    event = ScoreEvent("answer-77", "11274", 13, 13)
    first = service.process_score(event)
    second = service.process_score(event)
    assert first.review_status == "completed"
    assert first.rating == 4
    assert second.duplicate is True
    assert anki.reviews == 1


def test_duplicate_result_enriches_errors_without_second_review(db: Database) -> None:
    db.upsert_exercise(sample_exercise())
    anki = FakeAnki()
    service = SyncService(Settings(database_path=db.path), db, anki)
    first = ScoreEvent("answer-evidence", "11274", 9, 13)
    second = ScoreEvent("answer-evidence", "11274", 9, 13, payload={"observations": [
        {"kind": "question_error", "item_key": "4", "category": "Gap Filling", "outcome": "incorrect"}
    ]})
    assert service.process_score(first).duplicate is False
    enriched = service.process_score(second)
    assert enriched.duplicate is True
    assert enriched.observations_saved == 1
    assert anki.reviews == 1


def test_writing_band_submits_configured_rating(db: Database) -> None:
    exercise = Exercise("writing:900", "Essay", "https://example.test/writing/900",
                        skill="writing", part_number=2)
    db.upsert_exercise(exercise)
    anki = FakeAnki()
    service = SyncService(Settings(database_path=db.path), db, anki)
    result = service.process_score(ScoreEvent("writing-answer", "writing:900", band=7.5))
    assert result.rating == 3
    assert db.get_exercise("writing:900")["last_band"] == 7.5
    assert anki.reviews == 1


def test_writing_rejects_accuracy_instead_of_band(db: Database) -> None:
    db.upsert_exercise(Exercise("writing:901", "Essay", "https://example.test/writing/901",
                                skill="writing", part_number=1))
    service = SyncService(Settings(database_path=db.path), db, FakeAnki())
    with pytest.raises(ValueError, match="requires an official IELTS band"):
        service.process_score(ScoreEvent("writing-invalid", "writing:901", 8, 10))


def test_not_due_card_is_deferred_without_review(db: Database) -> None:
    db.upsert_exercise(sample_exercise())
    anki = FakeAnki(due=False)
    service = SyncService(Settings(database_path=db.path, review_policy="due_only"), db, anki)
    result = service.process_score(ScoreEvent("answer-weak", "11274", 6, 13))
    assert result.review_status == "deferred"
    assert anki.reviews == 0


def test_pending_attempt_is_retried_once_anki_is_available(db: Database) -> None:
    db.upsert_exercise(sample_exercise())
    event = ScoreEvent("pending-1", "11274", 12, 13)
    assert db.insert_attempt(event, 3)
    anki = FakeAnki()
    service = SyncService(Settings(database_path=db.path), db, anki)
    completed, errors = service.retry_pending_reviews()
    assert (completed, errors) == (1, 0)
    assert db.get_attempt("pending-1")["review_status"] == "completed"
    assert anki.reviews == 1
