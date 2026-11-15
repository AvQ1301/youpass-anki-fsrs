from __future__ import annotations

import argparse
import asyncio
from urllib.parse import urlparse

import httpx
from playwright.async_api import async_playwright

from youpass_anki.evidence import normalized_result


async def watch(cdp_url: str, local_url: str) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(cdp_url)
        seen_pages: set[int] = set()

        async def attach(page) -> None:
            identity = id(page)
            if identity in seen_pages:
                return
            seen_pages.add(identity)
            quiz_payload = None
            submitted: set[str] = set()

            async def response(res) -> None:
                nonlocal quiz_payload
                url = res.url
                try:
                    payload = await res.json()
                except Exception:
                    return
                if "/v1/public/quizzes/" in url and res.request.method == "GET":
                    quiz_payload = payload
                    return
                if res.request.method == "POST" and any(
                    suffix in url for suffix in ("/answer", "/submit")
                ):
                    value = _unwrap(payload)
                    if value.get("id") is not None:
                        submitted.add(str(value["id"]))
                if "/v1/answers/" not in url and "/v1/public/answers/" not in url:
                    return
                answer = _unwrap(payload)
                answer_id = str(answer.get("id") or "")
                if not answer_id or answer_id not in submitted:
                    return
                match = _page_identity(page.url)
                if not match:
                    return
                skill, source_id = match
                async with httpx.AsyncClient(timeout=30, headers={
                    "Origin": "https://e-learning.youpass.vn",
                    "Referer": "https://e-learning.youpass.vn/",
                }) as client:
                    quiz = quiz_payload
                    if quiz is None:
                        response = await client.get(
                            f"https://api.youpass.vn/v1/public/quizzes/{source_id}/true/null"
                        )
                        response.raise_for_status()
                        quiz = response.json()
                    try:
                        event = normalized_result(payload, quiz, skill, source_id)
                    except ValueError:
                        return
                    result = await client.post(local_url.rstrip("/") + "/api/results", json=event)
                    result.raise_for_status()
                print(f"Saved {event['event_id']} with {len(event['payload']['observations'])} observations", flush=True)

            page.on("response", response)

        for context in browser.contexts:
            for page in context.pages:
                await attach(page)
            context.on("page", lambda page: asyncio.create_task(attach(page)))
        print("Watching Chrome for new YouPass submissions. Existing history is ignored.", flush=True)
        await asyncio.Event().wait()


def _unwrap(value):
    while isinstance(value, dict) and isinstance(value.get("data"), dict):
        value = value["data"]
    return value if isinstance(value, dict) else {}


def _page_identity(url: str) -> tuple[str, str] | None:
    parts = [part for part in urlparse(url).path.split("/") if part]
    if len(parts) < 3 or parts[0] != "practice":
        return None
    route, source_id = parts[1], parts[2].split("-")[-1]
    skill = "writing" if route.startswith("writing") else "speaking" if route.startswith("speaking") else route
    if skill not in {"reading", "listening", "writing", "speaking"} or not source_id.isdigit():
        return None
    return skill, source_id


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cdp", default="http://127.0.0.1:9223")
    parser.add_argument("--local", default="http://127.0.0.1:8844")
    args = parser.parse_args()
    asyncio.run(watch(args.cdp, args.local))


if __name__ == "__main__":
    main()
