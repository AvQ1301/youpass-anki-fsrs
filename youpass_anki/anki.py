from __future__ import annotations

import html
from typing import Any

import httpx

from .config import Settings

FIELDS = ["Exercise ID", "Skill", "Title", "Original URL", "Part", "Question types",
          "Question count", "Latest result", "Best result", "Last attempt date"]
READING_FIELDS = ["Exercise ID", "Title", "Original URL", "Passage number", "Question types",
                  "Question count", "Latest score", "Best score", "Last attempt date"]
FRONT = """<div class=yp-card><h2>IELTS {{Skill}} Practice</h2><p><b>Title:</b> {{Title}}</p>
<p><b>Part:</b> {{Part}}</p><p><b>Question Types / Topic:</b><br>{{Question types}}</p>
<a class=yp-open href=\"{{Original URL}}\">Open Exercise</a></div>"""
BACK = """{{FrontSide}}<hr><p><b>Latest Result:</b> {{Latest result}}</p>
<p><b>Best Result:</b> {{Best result}}</p><p><b>Last attempt:</b> {{Last attempt date}}</p>"""
READING_FRONT = """<div class=yp-card><h2>IELTS Reading Practice</h2><p><b>Title:</b> {{Title}}</p>
<p><b>Passage:</b> {{Passage number}}</p><p><b>Question Types:</b><br>{{Question types}}</p>
<a class=yp-open href=\"{{Original URL}}\">Open Exercise</a></div>"""
READING_BACK = """{{FrontSide}}<hr><p><b>Latest Score:</b> {{Latest score}}</p>
<p><b>Best Score:</b> {{Best score}}</p><p><b>Last attempt:</b> {{Last attempt date}}</p>"""
CSS = ".card{font-family:Arial,sans-serif;font-size:20px;text-align:left;color:#202124;background:#fff}.yp-card{max-width:650px;margin:auto}.yp-open{display:inline-block;padding:10px 16px;background:#4169e1;color:white!important;border-radius:8px;text-decoration:none}"


class AnkiError(RuntimeError):
    pass


class AnkiConnect:
    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        self.settings = settings
        self.client = client or httpx.Client(timeout=30)

    def invoke(self, action: str, **params: Any) -> Any:
        request: dict[str, Any] = {"action": action, "version": 6, "params": params}
        if self.settings.anki_key:
            request["key"] = self.settings.anki_key
        try:
            response = self.client.post(self.settings.anki_url + "/", json=request)
            response.raise_for_status()
            value = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise AnkiError(f"AnkiConnect unavailable: {exc}") from exc
        if value.get("error") is not None:
            raise AnkiError(str(value["error"]))
        return value.get("result")

    def setup(self) -> None:
        for skill in ("reading", "listening", "writing", "speaking"):
            self.invoke("createDeck", deck=self._deck_name(skill))
            model = self._model_name(skill)
            models = self.invoke("modelNames")
            if model not in models:
                is_reading = skill == "reading"
                self.invoke("createModel", modelName=model,
                            inOrderFields=READING_FIELDS if is_reading else FIELDS,
                            css=CSS, isCloze=False,
                            cardTemplates=[{"Name": "Exercise",
                                            "Front": READING_FRONT if is_reading else FRONT,
                                            "Back": READING_BACK if is_reading else BACK}])

    def _deck_name(self, skill: str) -> str:
        return self.settings.anki_deck if skill == "reading" else f"IELTS {skill.title()} - YouPass"

    def _model_name(self, skill: str) -> str:
        return self.settings.anki_model if skill == "reading" else f"YouPass IELTS {skill.title()}"

    def sync_exercise(self, exercise: dict[str, Any]) -> tuple[int, int]:
        skill = exercise.get("skill", "reading")
        model = self._model_name(skill)
        deck = self._deck_name(skill)
        query = f'"note:{model}" "Exercise ID:{exercise["exercise_id"]}"'
        note_ids = self.invoke("findNotes", query=query)
        fields = self._fields(exercise)
        if note_ids:
            note_id = int(note_ids[0])
            self.invoke("updateNoteFields", note={"id": note_id, "fields": fields})
            cards = self.invoke("findCards", query=f"nid:{note_id}")
        else:
            note_id = int(self.invoke("addNote", note={"deckName": deck,
                "modelName": model, "fields": fields,
                "options": {"allowDuplicate": False},
                "tags": ["youpass", f"ielts-{skill}"]}))
            cards = self.invoke("findCards", query=f"nid:{note_id}")
        if len(cards) != 1:
            raise AnkiError(f"expected one card for note {note_id}, got {len(cards)}")
        return note_id, int(cards[0])

    @staticmethod
    def _fields(exercise: dict[str, Any]) -> dict[str, str]:
        if exercise.get("last_band") is not None:
            latest = f'{exercise["last_band"]:g}/9'
            best = f'{exercise.get("best_band", exercise["last_band"]):g}/9'
        else:
            latest = f'{exercise.get("last_score", 0)}/{exercise["last_total"]}' if exercise.get("last_total") else ""
            best = f'{exercise.get("best_score", 0)}/{exercise["best_total"]}' if exercise.get("best_total") else ""
        skill = exercise.get("skill", "reading")
        question_types = "<br>".join(f"• {html.escape(value)}" for value in exercise.get("question_types", []))
        if skill == "reading":
            return {
                "Exercise ID": html.escape(str(exercise["exercise_id"])),
                "Title": html.escape(exercise["title"]),
                "Original URL": exercise["url"],
                "Passage number": str(exercise.get("passage_number") or ""),
                "Question types": question_types,
                "Question count": str(exercise.get("question_count") or ""),
                "Latest score": latest,
                "Best score": best,
                "Last attempt date": exercise.get("last_attempt_at") or "",
            }
        if skill == "listening":
            part = f'Section {exercise["section_number"]}' if exercise.get("section_number") else ""
        else:
            part = f'Part {exercise["part_number"]}' if exercise.get("part_number") else ""
        return {
            "Exercise ID": html.escape(str(exercise["exercise_id"])),
            "Skill": skill.title(),
            "Title": html.escape(exercise["title"]),
            "Original URL": exercise["url"],
            "Part": part,
            "Question types": question_types,
            "Question count": str(exercise.get("question_count") or ""),
            "Latest result": latest,
            "Best result": best,
            "Last attempt date": exercise.get("last_attempt_at") or "",
        }

    def is_due(self, card_id: int) -> bool:
        values = self.invoke("areDue", cards=[card_id])
        return bool(values and values[0])

    def submit_review(self, card_id: int, rating: int) -> str | None:
        before = self.invoke("getReviewsOfCards", cards=[card_id])
        try:
            current = self.invoke("guiCurrentCard")
        except AnkiError as exc:
            if "review is not currently active" not in str(exc).lower():
                raise
            current = None
        current_id = current.get("cardId") if isinstance(current, dict) else None
        if current_id is not None and int(current_id) == card_id:
            if not self.invoke("guiShowAnswer"):
                raise AnkiError("could not show the active card answer")
            if not self.invoke("guiAnswerCard", ease=rating):
                raise AnkiError("Anki reviewer rejected the review")
        else:
            success = self.invoke("answerCards", answers=[{"cardId": card_id, "ease": rating}])
            if success != [True]:
                raise AnkiError("Anki rejected the review")
        after = self.invoke("getReviewsOfCards", cards=[card_id])
        old_ids = {str(row["id"]) for row in before.get(str(card_id), before.get(card_id, []))}
        rows = after.get(str(card_id), after.get(card_id, []))
        new_ids = [str(row["id"]) for row in rows if str(row["id"]) not in old_ids]
        return new_ids[-1] if new_ids else None
