# YouPass IELTS → Anki FSRS

A local-first Python application for YouPass IELTS Reading, Listening, Writing, and Speaking. It discovers exercises, stores metadata in SQLite, creates exactly one Anki card per exercise, captures official YouPass results, and submits native Anki reviews so FSRS determines the next interval.

The application stores links and metadata only. It does not copy passage or question content into Anki or SQLite.

## Verified integration facts

Live inspection was performed on 12 November 2026.

- The YouPass listing is a Next.js application backed by `api.youpass.vn`.
- Public Reading listings use `GET /v1/public/quizzes/list/1,9/3/{page}/true/null/null/null/null`.
- Pagination is returned as `total`, `page`, and `page_size`; some unfiltered listings repeat IDs. The crawler enumerates published skill-specific filters and deduplicates by stable ID. The verified live catalogue currently contains 4,510 exercises: 522 Reading, 707 Listening, 901 Writing, and 2,380 Speaking.
- Each item has a stable numeric `id`, title, tags, and update timestamp. Existing Reading IDs remain numeric to preserve prior scheduling mappings; new Listening/Writing/Speaking IDs are namespaced (`listening:11274`, etc.) to avoid cross-skill collisions.
- Exercise pages use `https://e-learning.youpass.vn/practice/{skill}/{id}`; Writing and Speaking include task/part in the skill path.
- Exercise detail requests use `GET /v1/public/quizzes/{id}/true/null`.
- Reading submission uses `POST /v2/quizzes/{id}/answer`. The official request contains `answer.summary.correct` and `answer.summary.total`; the response contains the stable answer ID used on the result URL.
- Anonymous exercises can be opened, but recording a result requires the user's normal YouPass login.
- Current AnkiConnect provides `answerCards`, which calls Anki's native scheduler. When the mapped card is active in the reviewer, this application uses `guiShowAnswer` and `guiAnswerCard` so reviewer custom scheduling and hooks are retained.

These are isolated behind adapters because they can change when either product updates.

## Architecture

```text
YouPass public listing/detail API
       ↓
Playwright/API crawler → SQLite exercises + attempts
       ↓                         ↑
AnkiConnect note sync       userscript official score event
       ↓                         ↓
One Anki card       local FastAPI /api/results
       └──────── native scheduler answer ────────┘
                         ↓
                        FSRS
```

Important correctness rules:

1. Metadata updates use `updateNoteFields`; cards are not recreated, so scheduling survives synchronization.
2. `attempts.event_id` is unique. YouPass answer IDs make result processing idempotent across refreshes and restarts.
3. A score field update is not treated as a review. Only Anki's native answer operation changes FSRS state.
4. The default `REVIEW_POLICY=due_only` refuses to create a review if the card is not due.
5. If the Anki HTTP response is lost during a write, the attempt becomes `manual_check`; it is not automatically retried because that could create a duplicate review.

## Requirements

- Python 3.12+
- Anki Desktop with FSRS enabled
- AnkiConnect
- Chrome/Chromium and a userscript manager such as Tampermonkey

### AnkiConnect compatibility

The implementation uses the legacy v6 endpoint because it is widely deployed. Required actions are:

- `createDeck`, `modelNames`, `createModel`
- `findNotes`, `addNote`, `updateNoteFields`, `findCards`
- `areDue`, `answerCards`, `getReviewsOfCards`
- `guiCurrentCard`, `guiShowAnswer`, `guiAnswerCard`

Current AnkiConnect documentation identifies `answerCards` as a native review operation and maps it to API v1 `scheduling.predict` / `scheduling.answerMany`. It is not equivalent to `setDueDate` or a note field update.

The installed AnkiConnect must expose `answerCards`; test it with:

```powershell
curl.exe -X POST http://127.0.0.1:8765/ -H "Content-Type: application/json" -d '{"action":"answerCards","version":6,"params":{"answers":[]}}'
```

Expected result: `{"result":[],"error":null}`.

## Windows installation

1. Install Python 3.12 or newer and Anki Desktop.
2. In Anki, enable FSRS under **Deck Options → FSRS**.
3. Install AnkiConnect from **Tools → Add-ons → Get Add-ons** using its current AnkiWeb code. Restart Anki.
4. Open PowerShell in this repository:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[test]"
python -m playwright install chromium
Copy-Item .env.example .env
```

5. Keep Anki open with the desired profile selected.
6. Start the application:

```powershell
.\start-windows.bat
```

7. Open <http://127.0.0.1:8844>, then select **Synchronize exercises**.
8. Install `userscript/youpass-anki.user.js` in Tampermonkey. The script reads only official result data from YouPass's own submission request/response and sends `correct`, `total`, exercise ID, and answer ID to localhost.
9. Log into YouPass through its normal UI. No password is requested or stored by this project.

## Daily workflow

1. Review the relevant `IELTS Reading/Listening/Writing/Speaking - YouPass` deck in Anki.
2. Click **Open Exercise** on the card.
3. Complete and submit the exercise on YouPass.
4. The userscript detects the official submission response and posts the score to the local application.
5. The local application updates score metadata, checks idempotency/due status, and submits the mapped Anki rating.
6. Anki/FSRS computes the next schedule.

Default Reading/Listening rating thresholds:

| Accuracy | Rating |
|---|---|
| 100% | Easy (4) |
| 85–99.99% | Good (3) |
| 60–84.99% | Hard (2) |
| below 60% | Again (1) |

Writing/Speaking use the approved IELTS band mapping:

| Band | Rating |
|---|---|
| 8.0–9.0 | Easy (4) |
| 7.0–7.5 | Good (3) |
| 6.0–6.5 | Hard (2) |
| below 6.0 | Again (1) |

Thresholds can be changed on the dashboard or in `.env`.

## Reviewing cards outside the active Anki session

The safest workflow is to open an exercise from the card currently displayed by Anki. The integration then answers that active card through `guiAnswerCard`, retaining reviewer hooks and custom scheduling states.

If the mapped card is not the active reviewer card:

- with `REVIEW_POLICY=due_only` (default), only cards that Anki reports due are answered;
- with `REVIEW_POLICY=allow_not_due`, a native scheduler answer can be recorded for a non-due card. This is a real, artificial early review and changes FSRS memory/history. Use it only if that behavior is intentional.

Cards in suspended, buried, filtered-preview, or incompatible states may be rejected by Anki. The application records the failure instead of changing raw card state.

## Configuration

Copy `.env.example` to `.env`.

| Variable | Purpose |
|---|---|
| `DATABASE_PATH` | SQLite database path |
| `CRAWLER_MODE` | Reserved adapter selection (`api` is the verified default) |
| `CRAWL_DELAY_SECONDS` | Delay between listing pages |
| `CRAWL_MAX_RETRIES` | Bounded request retries |
| `CRAWL_ENRICH_DETAILS` | Fetch detail metadata such as question count |
| `PLAYWRIGHT_PROFILE` | Persistent profile for manual authorized inspection |
| `ANKI_CONNECT_URL` | Local AnkiConnect endpoint |
| `ANKI_CONNECT_KEY` | Optional AnkiConnect API key |
| `ANKI_DECK` / `ANKI_MODEL` | Stable Anki names |
| `REVIEW_POLICY` | `due_only` or `allow_not_due` |
| `RATING_*_MIN` | Score thresholds |

AnkiConnect should remain bound to `127.0.0.1`. Do not expose it to the network.

## Commands

```bash
# Start dashboard
uv run youpass-anki

# Crawl only; checkpoint resumes after an interruption
uv run youpass-crawl

# Start a headed persistent browser, manually log in, and record relevant
# authorized quiz/result traffic for adapter maintenance
uv run youpass-crawl --inspect --output data/inspection

# Tests
uv run pytest
```

## Dashboard performance

The exercise table is server-paginated at 50 rows per request instead of loading the full catalogue. Search uses a 250 ms debounce and cancels stale requests; skill filters run in SQLite. API responses larger than 500 bytes use gzip, static JavaScript is deferred, and table rows use `content-visibility` to reduce layout work. The exercise endpoint accepts `page`, `page_size` (10–100), `search`, and `skill`.

## CSV export

Use **Export CSV** on the dashboard or download:

```text
http://127.0.0.1:8844/api/export.csv
```

## Authenticated browser bridge

For reliable YouPass authentication on Linux, start normal Chrome with a loopback-only DevTools port and run the watcher:

```bash
/opt/google/chrome/google-chrome \
  --user-data-dir="$PWD/data/browser-profile" \
  --remote-debugging-address=127.0.0.1 \
  --remote-debugging-port=9223

PYTHONPATH="$PWD" uv run python scripts/watch_youpass.py
```

The watcher attaches to normal Chrome, so Google/YouPass login and OS cookie encryption continue to work. It observes first-party responses only. It ignores historical GET results unless their answer ID was first observed from a POST submission during the current watcher session. The DevTools port must remain bound to `127.0.0.1`; never expose it to the network.

## Error evidence tracking

Only attempts completed after installing the userscript are tracked. Historical YouPass attempts are intentionally ignored and all catalogue cards remain new to Anki.

For each new official result, the database can store:

- Reading/Listening: official question/item ID, question type, incorrect or skipped outcome, selected answer, and official correct answer when YouPass exposes it.
- Writing/Speaking: official criterion scores such as Task Response/Achievement, Coherence, Lexical Resource, Grammar, Fluency, and Pronunciation.
- Source and observation timestamp, without storing the full copyrighted passage or prompt.

The normalized records are in `error_observations`, linked to the exact attempt and exercise. Repeated result/status responses enrich the existing attempt by stable answer/event ID and cannot create a second Anki review. Opening an old result URL does not create an attempt or review. Use `/api/errors/summary` for aggregate weaknesses and `/api/exercises/{exercise_id}/errors` for one exercise. The dashboard shows the leading tracked categories.

Evidence is stored for future decisions only. The current FSRS rating still uses the configured total accuracy or official overall band; individual errors do not silently alter the rating.

## Score detection and manual fallback

Automatic detection is intentionally strict. Reading/Listening accept official numeric `correct` and `total`. Writing/Speaking wait for official `overall_band_score`, `band_score`, or overall grading data. The integration does not calculate answers or infer a grade.

A **Send result to Anki** fallback accepts `correct/total` for Reading/Listening or an official 0–9 band for Writing/Speaking. Manual entries receive a unique local event ID and remain idempotent.

## Troubleshooting

### AnkiConnect unavailable

- Ensure Anki is running and the intended profile is open.
- Check <http://127.0.0.1:8765> with the `version` action.
- Restart Anki after installing or updating AnkiConnect.
- If an API key is configured, put the same value in `.env`.

### Score saved but review is deferred

The mapped card is not due and `REVIEW_POLICY=due_only`. This is deliberate. Review it through Anki when due, or explicitly choose `allow_not_due` after understanding that it creates an early review.

### Attempt says `manual_check`

Anki may have accepted the review before the HTTP response was lost. Inspect the card's review history in Anki. Do not blindly resend the same result.

### YouPass changed its API/DOM

Run the inspection command, submit one exercise through your authorized account, and inspect `data/inspection/network.json`. Update only the adapter/userscript patterns. Do not bypass login, CAPTCHA, access controls, paywalls, or rate limits.

### Userscript does not report a score

- Confirm the local dashboard is running.
- Confirm Tampermonkey grants access to `127.0.0.1`.
- Open the browser console and look for `YouPass Anki` messages.
- Use the manual fallback rather than guessing the score.

## Test coverage

Fixture-backed tests cover listing parsing, pagination termination, stable exercise IDs, duplicate prevention, official score parsing, rating boundaries, note update behavior, review synchronization, non-due deferral, SQLite persistence, normalized error evidence, idempotent evidence enrichment, API validation, and CSV export.

Live tests deliberately do not submit a real YouPass attempt or mutate a real Anki card automatically. Those require the user's authenticated account and selected Anki collection. Complete the manual acceptance test below once after setup:

1. Create/sync a disposable YouPass exercise card.
2. Start that card in Anki and open its exercise link.
3. Submit an official result.
4. Verify one—and only one—new review log entry with the expected rating.
5. Refresh the YouPass result page and verify no additional review appears.
6. Confirm Anki's due date/FSRS memory changed through the native scheduler.

## Legal and operational limits

Use only content available through your authorized access. Respect YouPass terms, rate limits, and copyright. The application stores no full exercise text, no plaintext password, and no copied answer content.
