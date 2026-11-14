from __future__ import annotations

import csv
import io
import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterator

from .models import Exercise, ScoreEvent, utc_now


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
        with self.connect() as connection:
            connection.executescript(schema)
            columns = {row[1] for row in connection.execute("PRAGMA table_info(exercises)")}
            for name, definition in {
                "skill": "TEXT NOT NULL DEFAULT 'reading'", "section_number": "INTEGER", "part_number": "INTEGER",
                "last_band": "REAL", "best_band": "REAL",
            }.items():
                if name not in columns:
                    connection.execute(f"ALTER TABLE exercises ADD COLUMN {name} {definition}")
            attempt_columns = {row[1] for row in connection.execute("PRAGMA table_info(attempts)")}
            for name, definition in {"score_kind": "TEXT NOT NULL DEFAULT 'accuracy'", "band": "REAL"}.items():
                if name not in attempt_columns:
                    connection.execute(f"ALTER TABLE attempts ADD COLUMN {name} {definition}")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_exercises_skill ON exercises(skill)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_exercises_updated ON exercises(updated_at DESC)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_exercises_skill_updated ON exercises(skill, updated_at DESC)")

    def upsert_exercise(self, exercise: Exercise) -> bool:
        values = asdict(exercise)
        values["question_types"] = json.dumps(exercise.question_types, ensure_ascii=False)
        with self.connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM exercises WHERE exercise_id = ?", (exercise.exercise_id,)
            ).fetchone()
            connection.execute(
                """INSERT INTO exercises (
                    exercise_id,skill,title,url,passage_number,section_number,part_number,question_types,question_count,
                    completion_status,last_score,last_total,best_score,best_total,last_band,best_band,
                    attempt_count,last_attempt_at,anki_note_id,anki_card_id,
                    source_updated_at,discovered_at,updated_at
                ) VALUES (
                    :exercise_id,:skill,:title,:url,:passage_number,:section_number,:part_number,:question_types,:question_count,
                    :completion_status,:last_score,:last_total,:best_score,:best_total,:last_band,:best_band,
                    :attempt_count,:last_attempt_at,:anki_note_id,:anki_card_id,
                    :source_updated_at,:discovered_at,:updated_at
                ) ON CONFLICT(exercise_id) DO UPDATE SET
                    skill=excluded.skill, title=excluded.title, url=excluded.url,
                    passage_number=COALESCE(excluded.passage_number, exercises.passage_number),
                    section_number=COALESCE(excluded.section_number, exercises.section_number),
                    part_number=COALESCE(excluded.part_number, exercises.part_number),
                    question_types=CASE WHEN excluded.question_types='[]' THEN exercises.question_types ELSE excluded.question_types END,
                    question_count=COALESCE(excluded.question_count, exercises.question_count),
                    completion_status=CASE WHEN excluded.completion_status='unknown' THEN exercises.completion_status ELSE excluded.completion_status END,
                    source_updated_at=excluded.source_updated_at, updated_at=excluded.updated_at
                """, values
            )
        return not bool(exists)

    def get_exercise(self, exercise_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM exercises WHERE exercise_id=?", (exercise_id,)).fetchone()
        return self._exercise_row(row) if row else None

    def list_exercises(self, limit: int = 5000) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM exercises ORDER BY updated_at DESC, exercise_id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._exercise_row(row) for row in rows]

    def exercise_page(self, page: int = 1, page_size: int = 50, search: str = "",
                      skill: str | None = None) -> dict[str, Any]:
        where: list[str] = []
        params: list[Any] = []
        if skill:
            where.append("skill=?")
            params.append(skill)
        if search:
            escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            where.append("(title LIKE ? ESCAPE '\\' COLLATE NOCASE OR exercise_id LIKE ? ESCAPE '\\' COLLATE NOCASE)")
            params.extend([f"%{escaped}%", f"%{escaped}%"])
        clause = " WHERE " + " AND ".join(where) if where else ""
        with self.connect() as connection:
            total = connection.execute(f"SELECT COUNT(*) FROM exercises{clause}", params).fetchone()[0]
            rows = connection.execute(
                f"""SELECT exercise_id,skill,title,url,passage_number,section_number,part_number,
                question_types,question_count,last_score,last_total,last_band,anki_card_id,last_sync_error
                FROM exercises{clause} ORDER BY updated_at DESC,exercise_id DESC LIMIT ? OFFSET ?""",
                [*params, page_size, (page - 1) * page_size],
            ).fetchall()
        return {"items": [self._exercise_row(row) for row in rows], "page": page,
                "page_size": page_size, "total": total,
                "total_pages": (total + page_size - 1) // page_size}

    @staticmethod
    def _exercise_row(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        value["question_types"] = json.loads(value["question_types"] or "[]")
        return value

    def update_anki_mapping(self, exercise_id: str, note_id: int, card_id: int) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE exercises SET anki_note_id=?, anki_card_id=?, last_sync_error=NULL, updated_at=? WHERE exercise_id=?",
                (note_id, card_id, utc_now(), exercise_id),
            )

    def set_sync_error(self, exercise_id: str, message: str | None) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE exercises SET last_sync_error=?, updated_at=? WHERE exercise_id=?",
                (message, utc_now(), exercise_id),
            )

    def insert_attempt(self, event: ScoreEvent, rating: int) -> bool:
        event.validate()
        with self.connect() as connection:
            try:
                connection.execute(
                    """INSERT INTO attempts
                    (event_id,exercise_id,score_kind,correct,total,band,accuracy,attempted_at,source,payload,rating,created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (event.event_id, event.exercise_id, event.score_kind, event.correct, event.total, event.band,
                     event.accuracy, event.attempted_at, event.source,
                     json.dumps(event.payload, ensure_ascii=False), rating, utc_now()),
                )
            except sqlite3.IntegrityError as exc:
                if "attempts.event_id" in str(exc) or "UNIQUE constraint failed: attempts.event_id" in str(exc):
                    return False
                raise
            if event.band is not None:
                best_band = connection.execute(
                    "SELECT MAX(band) FROM attempts WHERE exercise_id=?", (event.exercise_id,)
                ).fetchone()[0]
                connection.execute(
                    """UPDATE exercises SET completion_status='finished', last_band=?, best_band=?,
                    attempt_count=attempt_count+1, last_attempt_at=?, updated_at=? WHERE exercise_id=?""",
                    (event.band, best_band, event.attempted_at, utc_now(), event.exercise_id),
                )
            else:
                best = connection.execute(
                    """SELECT correct,total FROM attempts WHERE exercise_id=? AND score_kind='accuracy'
                    ORDER BY (correct * 1.0 / total) DESC, attempted_at DESC LIMIT 1""",
                    (event.exercise_id,),
                ).fetchone()
                connection.execute(
                    """UPDATE exercises SET completion_status='finished', last_score=?, last_total=?,
                    best_score=?, best_total=?, attempt_count=attempt_count+1, last_attempt_at=?, updated_at=?
                    WHERE exercise_id=?""",
                    (event.correct, event.total, best["correct"], best["total"],
                     event.attempted_at, utc_now(), event.exercise_id),
                )
        return True

    def merge_attempt_evidence(self, event: ScoreEvent) -> dict[str, int]:
        """Enrich an existing attempt without creating a second Anki review."""
        with self.connect() as connection:
            attempt = connection.execute(
                "SELECT id,payload FROM attempts WHERE event_id=?", (event.event_id,)
            ).fetchone()
            if not attempt:
                raise ValueError(f"unknown attempt {event.event_id}")
            stored = json.loads(attempt["payload"] or "{}")
            merged = self._deep_merge(stored, event.payload)
            connection.execute(
                "UPDATE attempts SET payload=? WHERE id=?",
                (json.dumps(merged, ensure_ascii=False), attempt["id"]),
            )
            inserted = self._store_observations(
                connection, attempt["id"], event.exercise_id, merged, event.attempted_at
            )
            total = connection.execute(
                "SELECT COUNT(*) FROM error_observations WHERE attempt_id=?", (attempt["id"],)
            ).fetchone()[0]
        return {"inserted": inserted, "total": total}

    @staticmethod
    def _deep_merge(current: Any, incoming: Any) -> Any:
        if isinstance(current, dict) and isinstance(incoming, dict):
            merged = dict(current)
            for key, value in incoming.items():
                merged[key] = Database._deep_merge(merged.get(key), value)
            return merged
        if isinstance(current, list) and isinstance(incoming, list):
            return incoming if incoming else current
        return incoming if incoming is not None else current

    def _store_observations(self, connection: sqlite3.Connection, attempt_id: int,
                            exercise_id: str, payload: dict[str, Any], observed_at: str) -> int:
        observations = payload.get("observations") or []
        inserted = 0
        for value in observations:
            if not isinstance(value, dict):
                continue
            kind = value.get("kind")
            outcome = value.get("outcome")
            if kind not in {"question_error", "criterion_score"} or not outcome:
                continue
            fingerprint = value.get("fingerprint") or hashlib.sha256(
                json.dumps({
                    "kind": kind, "item_key": value.get("item_key"),
                    "category": value.get("category"), "outcome": outcome,
                    "selected_answer": value.get("selected_answer"),
                    "correct_answer": value.get("correct_answer"), "score": value.get("score"),
                }, sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest()
            cursor = connection.execute(
                """INSERT OR IGNORE INTO error_observations
                (attempt_id,exercise_id,fingerprint,kind,item_key,category,outcome,
                 selected_answer,correct_answer,score,source,observed_at,metadata)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (attempt_id, exercise_id, fingerprint, kind, value.get("item_key"),
                 value.get("category"), outcome, value.get("selected_answer"),
                 value.get("correct_answer"), value.get("score"),
                 value.get("source") or "youpass-official-result", observed_at,
                 json.dumps(value.get("metadata") or {}, ensure_ascii=False)),
            )
            inserted += int(cursor.rowcount > 0)
        return inserted

    def error_summary(self) -> dict[str, Any]:
        with self.connect() as connection:
            totals = connection.execute("""SELECT
                SUM(kind='question_error') question_errors,
                SUM(kind='criterion_score') criterion_scores,
                COUNT(DISTINCT attempt_id) attempts_with_evidence
                FROM error_observations""").fetchone()
            categories = connection.execute("""SELECT e.skill,o.kind,
                COALESCE(o.category,'Unknown') category,COUNT(*) observations,
                SUM(o.outcome IN ('incorrect','skipped','weak')) weaknesses,
                AVG(o.score) average_score
                FROM error_observations o JOIN exercises e ON e.exercise_id=o.exercise_id
                GROUP BY e.skill,o.kind,COALESCE(o.category,'Unknown')
                ORDER BY weaknesses DESC,observations DESC LIMIT 100""").fetchall()
        return {**{key: value or 0 for key, value in dict(totals).items()},
                "categories": [dict(row) for row in categories]}

    def exercise_errors(self, exercise_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute("""SELECT o.*,a.event_id,a.attempted_at
                FROM error_observations o JOIN attempts a ON a.id=o.attempt_id
                WHERE o.exercise_id=? ORDER BY a.attempted_at DESC,o.id""", (exercise_id,)).fetchall()
        values = []
        for row in rows:
            value = dict(row)
            value["metadata"] = json.loads(value["metadata"] or "{}")
            values.append(value)
        return values

    def mark_attempt(self, event_id: str, status: str, review_id: str | None = None, error: str | None = None) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE attempts SET review_status=?, anki_review_id=?, review_error=? WHERE event_id=?",
                (status, review_id, error, event_id),
            )

    def get_attempt(self, event_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM attempts WHERE event_id=?", (event_id,)).fetchone()
        return dict(row) if row else None

    def pending_attempts(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM attempts WHERE review_status IN ('pending','failed') ORDER BY id"
            ).fetchall()
        return [dict(row) for row in rows]

    def dashboard_stats(self) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute("""SELECT COUNT(*) total,
                SUM(anki_card_id IS NOT NULL) synchronized,
                SUM(attempt_count > 0) completed,
                SUM(best_score = best_total AND best_total > 0) perfect,
                SUM(attempt_count > 0 AND ((last_total > 0 AND (last_score * 1.0 / last_total) < 0.85)
                    OR (last_band IS NOT NULL AND last_band < 7.0))) needs_practice,
                AVG(CASE WHEN last_total > 0 THEN last_score * 100.0 / last_total END) average_accuracy,
                AVG(last_band) average_band,
                SUM(last_sync_error IS NOT NULL) sync_errors FROM exercises""").fetchone()
            by_skill = connection.execute(
                "SELECT skill,COUNT(*) count FROM exercises GROUP BY skill ORDER BY skill"
            ).fetchall()
            last = connection.execute(
                "SELECT completed_at FROM sync_runs WHERE status='completed' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        result = dict(row)
        result["by_skill"] = {item["skill"]: item["count"] for item in by_skill}
        result["last_sync_at"] = last[0] if last else None
        return {key: (0 if value is None and key != "last_sync_at" else value) for key, value in result.items()}

    def start_run(self, kind: str) -> int:
        with self.connect() as connection:
            cursor = connection.execute("INSERT INTO sync_runs(kind,started_at) VALUES (?,?)", (kind, utc_now()))
            return int(cursor.lastrowid)

    def finish_run(self, run_id: int, status: str, discovered: int = 0, synchronized: int = 0,
                   error_count: int = 0, message: str | None = None) -> None:
        with self.connect() as connection:
            connection.execute("""UPDATE sync_runs SET completed_at=?,status=?,discovered=?,synchronized=?,
                error_count=?,message=? WHERE id=?""",
                (utc_now(), status, discovered, synchronized, error_count, message, run_id))

    def checkpoint(self, scope: str) -> tuple[int, int | None]:
        with self.connect() as connection:
            row = connection.execute("SELECT next_page,total_pages FROM crawl_checkpoints WHERE scope=?", (scope,)).fetchone()
        return (int(row[0]), row[1]) if row else (1, None)

    def save_checkpoint(self, scope: str, next_page: int, total_pages: int | None) -> None:
        with self.connect() as connection:
            connection.execute("""INSERT INTO crawl_checkpoints(scope,next_page,total_pages,updated_at) VALUES (?,?,?,?)
                ON CONFLICT(scope) DO UPDATE SET next_page=excluded.next_page,total_pages=excluded.total_pages,updated_at=excluded.updated_at""",
                (scope, next_page, total_pages, utc_now()))

    def clear_checkpoint(self, scope: str) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM crawl_checkpoints WHERE scope=?", (scope,))

    def get_settings(self) -> dict[str, str]:
        with self.connect() as connection:
            rows = connection.execute("SELECT key,value FROM settings").fetchall()
        return {row[0]: row[1] for row in rows}

    def set_settings(self, values: dict[str, str]) -> None:
        with self.connect() as connection:
            for key, value in values.items():
                connection.execute("""INSERT INTO settings(key,value,updated_at) VALUES (?,?,?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at""",
                    (key, value, utc_now()))

    def export_csv(self) -> str:
        rows = self.list_exercises()
        output = io.StringIO()
        fields = ["exercise_id", "skill", "title", "url", "passage_number", "section_number", "part_number", "question_types", "question_count",
                  "completion_status", "last_score", "last_total", "best_score", "best_total", "last_band", "best_band", "attempt_count",
                  "last_attempt_at", "anki_note_id", "anki_card_id"]
        writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            row = dict(row)
            row["question_types"] = " | ".join(row["question_types"])
            writer.writerow(row)
        return output.getvalue()
