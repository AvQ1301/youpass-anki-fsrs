from __future__ import annotations

import argparse
import hashlib
from datetime import datetime, timezone

import httpx

parser = argparse.ArgumentParser(description="Submit an official score manually to the local bridge")
parser.add_argument("exercise_id")
parser.add_argument("correct", type=int)
parser.add_argument("total", type=int)
parser.add_argument("--url", default="http://127.0.0.1:8844")
args = parser.parse_args()
now = datetime.now(timezone.utc).isoformat(timespec="seconds")
event_id = "manual-cli-" + hashlib.sha256(f"{args.exercise_id}:{args.correct}:{args.total}:{now}".encode()).hexdigest()[:24]
response = httpx.post(args.url + "/api/results", json={"event_id": event_id, "exercise_id": args.exercise_id,
    "correct": args.correct, "total": args.total, "attempted_at": now, "source": "manual-cli"}, timeout=30)
response.raise_for_status()
print(response.json())
