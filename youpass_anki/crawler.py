from __future__ import annotations

import argparse
import asyncio
import json
import math
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from .config import Settings
from .database import Database
from .parsing import count_questions, parse_exercise


@dataclass(slots=True)
class CrawlResult:
    discovered: int = 0
    inserted: int = 0
    pages: int = 0


class YouPassCrawler:
    """Discover all accessible IELTS exercises through observed public APIs."""

    def __init__(self, settings: Settings, database: Database, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self.database = database
        self.client = client

    async def crawl(self, resume: bool = True) -> CrawlResult:
        result = CrawlResult()
        own_client = self.client is None
        client = self.client or httpx.AsyncClient(
            base_url=self.settings.api_url,
            timeout=30,
            headers={"Accept": "application/json", "Origin": "https://youpass.vn", "Referer": "https://youpass.vn/"},
        )
        try:
            tag_positions = (await self._request_json(client, "/v1/public/tag-positions")).get("data") or []
            for skill in ("reading", "listening", "writing"):
                skill_result = await self._crawl_quiz_skill(client, skill, tag_positions, resume)
                result.discovered += skill_result.discovered
                result.inserted += skill_result.inserted
                result.pages += skill_result.pages
            skill_result = await self._crawl_speaking(client, tag_positions, resume)
            result.discovered += skill_result.discovered
            result.inserted += skill_result.inserted
            result.pages += skill_result.pages
        finally:
            if own_client:
                await client.aclose()
        return result

    async def _crawl_quiz_skill(self, client: httpx.AsyncClient, skill: str,
                                positions: list[dict[str, Any]], resume: bool) -> CrawlResult:
        result = CrawlResult()
        seen: set[str] = set()
        for name, first, question_type, source, topic in self._quiz_scopes(skill, positions):
            checkpoint = f"{skill}-list:{name}"
            page, _ = self.database.checkpoint(checkpoint) if resume else (1, None)
            while True:
                payload = await self._get_quiz_page(client, skill, page, first, question_type, source, topic)
                data = payload.get("data") or {}
                items = data.get("items") or []
                total = int(data.get("total") or 0)
                page_size = int(data.get("page_size") or len(items) or 20)
                total_pages = math.ceil(total / page_size) if total else page
                if not items:
                    self.database.clear_checkpoint(checkpoint)
                    break
                for item in items:
                    part = int(first) if skill == "writing" and first else None
                    exercise = parse_exercise(item, self.settings.exercise_base, skill, part_number=part)
                    if exercise.exercise_id in seen:
                        continue
                    seen.add(exercise.exercise_id)
                    existing = self.database.get_exercise(exercise.exercise_id)
                    if self.settings.crawl_enrich_details and skill in {"reading", "listening"}:
                        exercise.question_count = (existing or {}).get("question_count")
                        if exercise.question_count is None:
                            exercise.question_count = await self._question_count(client, str(item["id"]))
                    result.inserted += int(self.database.upsert_exercise(exercise))
                result.pages += 1
                page += 1
                self.database.save_checkpoint(checkpoint, page, total_pages)
                if page > total_pages:
                    self.database.clear_checkpoint(checkpoint)
                    break
                await asyncio.sleep(self.settings.crawl_delay)
        result.discovered = len(seen)
        return result

    def _quiz_scopes(self, skill: str, positions: list[dict[str, Any]]) -> list[tuple[str, int | None, int | None, int | None, int | None]]:
        tasks = (1, 2) if skill == "writing" else (None,)
        scopes: list[tuple[str, int | None, int | None, int | None, int | None]] = []
        position_maps = {
            "reading": {"reading_passage_search": 0, "reading_question_types_search": 1,
                        "reading_book_type_search": 2},
            "listening": {"listening_section_search": 0, "listening_question_types_search": 1,
                          "book_type_search": 2},
        }
        for task in tasks:
            scopes.append((f"all-{task or 0}", task, None, None, None))
            if skill == "writing":
                mapping = {"writing_question_types_search": 1,
                           f"book_type_task_{task}_search": 2,
                           ("writing_task_1_topics_search" if task == 1 else "writing_topics_search"): 3}
            else:
                mapping = position_maps[skill]
            for position in positions:
                slot = mapping.get(position.get("position"))
                if slot is None:
                    continue
                for wrapper in position.get("tags") or []:
                    tag_id = (wrapper.get("tag_search_id") or {}).get("id")
                    if tag_id is None:
                        continue
                    values: list[int | None] = [task, None, None, None]
                    values[slot] = int(tag_id)
                    scopes.append((f"{task or 0}-{slot}-{tag_id}", *values))
        return scopes

    async def _get_quiz_page(self, client: httpx.AsyncClient, skill: str, page: int,
                             first: int | None, question_type: int | None,
                             source: int | None, topic: int | None) -> dict[str, Any]:
        types = {"reading": "1,9", "listening": "2,10", "writing": "7"}[skill]
        values = [first, question_type, source, topic]
        filters = "/".join("null" if value is None else str(value) for value in values)
        return await self._request_json(client, f"/v1/public/quizzes/list/{types}/3/{page}/true/{filters}")

    async def _crawl_speaking(self, client: httpx.AsyncClient, positions: list[dict[str, Any]],
                              resume: bool) -> CrawlResult:
        result = CrawlResult()
        seen: set[str] = set()
        positions_by_name = {position.get("position"): position for position in positions}
        for part in (1, 2, 3):
            sources: list[int | None] = [None]
            position = positions_by_name.get(f"book_type_speaking_part_{part}") or {}
            sources.extend(int(tag["tag_search_id"]["id"]) for tag in position.get("tags") or [])
            for source in sources:
                name = f"{part}-{source or 0}"
                checkpoint = f"speaking-list:{name}"
                page, _ = self.database.checkpoint(checkpoint) if resume else (1, None)
                while True:
                    source_value = "null" if source is None else str(source)
                    payload = await self._request_json(
                        client, f"/v1/public/speaking-topics/list/20/{part}/{source_value}/{page}"
                    )
                    data = payload.get("data") or {}
                    topics = data.get("topics") or []
                    total = int(data.get("total") or 0)
                    page_size = int(data.get("page_size") or len(topics) or 20)
                    total_pages = math.ceil(total / page_size) if total else page
                    if not topics:
                        self.database.clear_checkpoint(checkpoint)
                        break
                    for topic in topics:
                        for item in topic.get("quizzes") or []:
                            exercise = parse_exercise(item, self.settings.exercise_base, "speaking",
                                                      part_number=part, topic_title=topic.get("title"))
                            if exercise.exercise_id in seen:
                                continue
                            seen.add(exercise.exercise_id)
                            result.inserted += int(self.database.upsert_exercise(exercise))
                    result.pages += 1
                    page += 1
                    self.database.save_checkpoint(checkpoint, page, total_pages)
                    if page > total_pages:
                        self.database.clear_checkpoint(checkpoint)
                        break
                    await asyncio.sleep(self.settings.crawl_delay)
        result.discovered = len(seen)
        return result

    async def _question_count(self, client: httpx.AsyncClient, source_id: str) -> int | None:
        try:
            payload = await self._request_json(client, f"/v1/public/quizzes/{source_id}/true/null")
            return count_questions(payload)
        except (httpx.HTTPError, ValueError, KeyError):
            return None

    async def _request_json(self, client: httpx.AsyncClient, path: str) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(self.settings.crawl_max_retries):
            try:
                response = await client.get(path)
                response.raise_for_status()
                value = response.json()
                if value.get("code", 0) not in (0, None):
                    raise ValueError(value.get("message") or f"YouPass API code {value['code']}")
                return value
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                if attempt + 1 < self.settings.crawl_max_retries:
                    await asyncio.sleep(min(8, (2**attempt) + random.random()))
        assert last_error
        raise last_error


async def inspect_authenticated(settings: Settings, output: Path) -> None:
    from playwright.async_api import async_playwright

    output.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            str(settings.playwright_profile), headless=False, channel="chrome"
        )
        page = context.pages[0] if context.pages else await context.new_page()
        records: list[dict[str, Any]] = []

        async def record(response: Any) -> None:
            if "api.youpass.vn" not in response.url or not any(
                word in response.url for word in ("quiz", "answer", "result", "speaking")
            ):
                return
            entry: dict[str, Any] = {"url": response.url, "status": response.status,
                                     "method": response.request.method}
            try:
                entry["body"] = await response.json()
            except Exception:
                entry["body"] = "<non-json>"
            records.append(entry)
            (output / "network.json").write_text(
                json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
            )

        page.on("response", record)
        await page.goto(settings.listing_url)
        print("Log in manually, complete an exercise, then close the browser.")
        await context.wait_for_event("close", timeout=0)


def cli() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inspect", action="store_true", help="record authorized exercise/result traffic")
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("data/inspection"))
    args = parser.parse_args()
    settings = Settings.from_env()
    db = Database(settings.database_path)
    db.initialize()
    if args.inspect:
        asyncio.run(inspect_authenticated(settings, args.output))
    else:
        print(asyncio.run(YouPassCrawler(settings, db).crawl(resume=not args.no_resume)))


if __name__ == "__main__":
    cli()
