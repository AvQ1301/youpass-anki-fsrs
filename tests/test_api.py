from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from youpass_anki.app import create_app
from youpass_anki.config import Settings
from youpass_anki.database import Database
from youpass_anki.models import Exercise


class FakeAnki:
    def sync_exercise(self, exercise):
        return 100, 200

    def is_due(self, card_id):
        return True

    def submit_review(self, card_id, rating):
        return "300"


def test_result_api_and_duplicate_response(tmp_path: Path) -> None:
    settings = Settings(database_path=tmp_path / "api.db")
    db = Database(settings.database_path)
    db.initialize()
    db.upsert_exercise(Exercise("42", "Fixture", "https://e-learning.youpass.vn/practice/reading/42"))
    client = TestClient(create_app(settings, db, FakeAnki()))
    body = {"event_id": "yp-123", "exercise_id": "42", "correct": 10, "total": 10}
    first = client.post("/api/results", json=body)
    second = client.post("/api/results", json=body)
    assert first.status_code == 200
    assert first.json()["rating"] == 4
    assert first.json()["review_status"] == "completed"
    assert second.json()["duplicate"] is True


def test_exercise_api_is_paginated_and_searchable(tmp_path: Path) -> None:
    settings = Settings(database_path=tmp_path / "page.db")
    db = Database(settings.database_path)
    db.initialize()
    for number in range(75):
        skill = "listening" if number % 2 else "reading"
        db.upsert_exercise(Exercise(f"{skill}:{number}", f"Exercise {number}",
                                    f"https://example.test/{skill}/{number}", skill=skill))
    client = TestClient(create_app(settings, db, FakeAnki()))
    first = client.get("/api/exercises?page=1&page_size=20").json()
    assert len(first["items"]) == 20
    assert first["total"] == 75
    assert first["total_pages"] == 4
    filtered = client.get("/api/exercises?page=1&page_size=20&skill=listening&search=Exercise%201").json()
    assert filtered["items"]
    assert all(item["skill"] == "listening" and "Exercise 1" in item["title"] for item in filtered["items"])


def test_error_summary_api(tmp_path: Path) -> None:
    settings = Settings(database_path=tmp_path / "errors.db")
    client = TestClient(create_app(settings, Database(settings.database_path), FakeAnki()))
    response = client.get("/api/errors/summary")
    assert response.status_code == 200
    assert response.json()["question_errors"] == 0


def test_invalid_score_and_thresholds(tmp_path: Path) -> None:
    settings = Settings(database_path=tmp_path / "api.db")
    client = TestClient(create_app(settings, Database(settings.database_path), FakeAnki()))
    assert client.post("/api/results", json={"event_id": "x", "exercise_id": "1", "correct": 4, "total": 3}).status_code == 422
    assert client.put("/api/settings/thresholds", json={"hard_min": 90, "good_min": 80, "easy_min": 100}).status_code == 422


def test_csv_export_persists_across_database_reopen(tmp_path: Path) -> None:
    path = tmp_path / "persist.db"
    first = Database(path)
    first.initialize()
    first.upsert_exercise(Exercise("7", "Persistent", "https://example.test/7"))
    second = Database(path)
    assert "Persistent" in second.export_csv()
