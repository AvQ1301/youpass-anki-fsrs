from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .anki import AnkiConnect
from .config import Settings
from .crawler import YouPassCrawler
from .database import Database
from .models import ScoreEvent
from .service import SyncService


class ScoreRequest(BaseModel):
    event_id: str = Field(min_length=1, max_length=200)
    exercise_id: str = Field(min_length=1, max_length=100)
    correct: int | None = Field(default=None, ge=0)
    total: int | None = Field(default=None, gt=0)
    band: float | None = Field(default=None, ge=0, le=9)
    attempted_at: str | None = None
    source: str = "youpass-official-response"
    payload: dict[str, Any] = Field(default_factory=dict)


class ThresholdRequest(BaseModel):
    easy_min: float = Field(ge=0, le=100)
    good_min: float = Field(ge=0, le=100)
    hard_min: float = Field(ge=0, le=100)


def create_app(settings: Settings | None = None, database: Database | None = None,
               anki: AnkiConnect | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    database = database or Database(settings.database_path)
    database.initialize()
    anki = anki or AnkiConnect(settings)
    service = SyncService(settings, database, anki)
    app = FastAPI(title="YouPass Anki FSRS", version="0.1.0")
    app.add_middleware(CORSMiddleware, allow_origins=["https://youpass.vn", "https://e-learning.youpass.vn"],
                       allow_methods=["GET", "POST", "PUT"], allow_headers=["Content-Type"])
    app.add_middleware(GZipMiddleware, minimum_size=500, compresslevel=5)
    static_dir = Path(__file__).parent.parent / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    app.state.settings = settings
    app.state.database = database
    app.state.anki = anki
    app.state.service = service

    @app.get("/", response_class=HTMLResponse)
    def index() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @app.get("/api/stats")
    def stats() -> dict[str, Any]:
        return {**database.dashboard_stats(), "thresholds": asdict(service.thresholds()),
                "band_thresholds": asdict(service.band_thresholds())}

    @app.get("/api/exercises")
    def exercises(page: int = Query(1, ge=1), page_size: int = Query(50, ge=10, le=100),
                  search: str = Query("", max_length=200),
                  skill: str | None = Query(None, pattern="^(reading|listening|writing|speaking)$")) -> dict[str, Any]:
        return database.exercise_page(page, page_size, search.strip(), skill)

    @app.get("/api/errors/summary")
    def error_summary() -> dict[str, Any]:
        return database.error_summary()

    @app.get("/api/exercises/{exercise_id:path}/errors")
    def exercise_errors(exercise_id: str) -> list[dict[str, Any]]:
        return database.exercise_errors(exercise_id)

    @app.get("/api/export.csv", response_class=PlainTextResponse)
    def export_csv() -> PlainTextResponse:
        return PlainTextResponse(database.export_csv(), media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=youpass-ielts.csv"})

    def synchronize(only_errors: bool = False) -> None:
        run_id = database.start_run("retry" if only_errors else "full")
        discovered = synchronized = errors = 0
        try:
            if not only_errors:
                crawl_result = asyncio.run(YouPassCrawler(settings, database).crawl())
                discovered = crawl_result.discovered
            synchronized, errors = service.sync_all_to_anki(only_errors=only_errors)
            retried, review_errors = service.retry_pending_reviews()
            synchronized += retried
            errors += review_errors
            database.finish_run(run_id, "completed", discovered, synchronized, errors)
        except Exception as exc:
            database.finish_run(run_id, "failed", discovered, synchronized, errors + 1, str(exc))

    @app.post("/api/sync", status_code=202)
    def sync(background: BackgroundTasks) -> dict[str, str]:
        background.add_task(synchronize, False)
        return {"status": "started"}

    @app.post("/api/sync/retry", status_code=202)
    def retry(background: BackgroundTasks) -> dict[str, str]:
        background.add_task(synchronize, True)
        return {"status": "started"}

    @app.post("/api/results")
    def result(value: ScoreRequest) -> dict[str, Any]:
        if value.correct is not None and value.total is not None and value.correct > value.total:
            raise HTTPException(422, "correct cannot exceed total")
        event = ScoreEvent(event_id=value.event_id, exercise_id=value.exercise_id,
                           correct=value.correct, total=value.total, band=value.band,
                           attempted_at=value.attempted_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
                           source=value.source, payload=value.payload)
        try:
            return asdict(service.process_score(event))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.put("/api/settings/thresholds")
    def thresholds(value: ThresholdRequest) -> dict[str, Any]:
        if not 0 <= value.hard_min <= value.good_min <= value.easy_min <= 100:
            raise HTTPException(422, "thresholds must satisfy hard <= good <= easy")
        database.set_settings({"easy_min": str(value.easy_min), "good_min": str(value.good_min),
                               "hard_min": str(value.hard_min)})
        return asdict(service.thresholds())

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
