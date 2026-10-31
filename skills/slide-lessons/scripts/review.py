#!/usr/bin/env python3
"""Spaced-review cards for video-lessons.

Each topic keeps its cards in <root>/<topic>/review.json, where <root> is
$VIDEO_LESSONS_HOME or ~/Learning.

  review.py due [--limit N]                  due cards across all topics, mixed, as JSON
  review.py add <topic> <prompt> <answer> <lesson-path>
  review.py grade <topic> <card-id> pass|fail
  review.py next                             date of the next due card
"""
import json
import os
import random
import re
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(os.environ.get("VIDEO_LESSONS_HOME") or Path.home() / "Learning").expanduser()
INTERVALS = [1, 3, 7, 21, 60, 120]


def load(topic):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", topic) or ".." in topic:
        sys.exit(f"invalid topic name: {topic!r}")
    path = ROOT / topic / "review.json"
    if not path.exists():
        sys.exit(f"no such topic: {topic} (expected {path})")
    return path, json.loads(path.read_text() or "[]")


def save(path, cards):
    path.write_text(json.dumps(cards, indent=2, ensure_ascii=False) + "\n")


def all_cards():
    for path in sorted(ROOT.glob("*/review.json")):
        for card in json.loads(path.read_text() or "[]"):
            yield path.parent.name, card


def cmd_due(args):
    limit = int(args[args.index("--limit") + 1]) if "--limit" in args else 10
    today = date.today().isoformat()
    due = [dict(card, topic=topic) for topic, card in all_cards() if card["due"] <= today]
    random.shuffle(due)  # interleave topics
    print(json.dumps(due[:limit], indent=2, ensure_ascii=False))
    print(f"# {len(due)} due, showing {min(len(due), limit)}", file=sys.stderr)


def cmd_add(topic, prompt, answer, lesson):
    path, cards = load(topic)
    next_id = max((c["id"] for c in cards), default=0) + 1
    cards.append({
        "id": next_id,
        "prompt": prompt,
        "answer": answer,
        "lesson": lesson,
        "step": 0,
        "due": (date.today() + timedelta(days=INTERVALS[0])).isoformat(),
        "history": [],
    })
    save(path, cards)
    print(f"added card {next_id} to {topic}")


def cmd_grade(topic, card_id, result):
    if result not in ("pass", "fail"):
        sys.exit("result must be pass or fail")
    path, cards = load(topic)
    card = next((c for c in cards if c["id"] == int(card_id)), None)
    if card is None:
        sys.exit(f"no card {card_id} in {topic}")
    card["step"] = min(card["step"] + 1, len(INTERVALS) - 1) if result == "pass" else 0
    card["due"] = (date.today() + timedelta(days=INTERVALS[card["step"]])).isoformat()
    card["history"].append({"date": date.today().isoformat(), "result": result})
    save(path, cards)
    print(f"{topic} card {card_id}: {result}, next due {card['due']}")


def cmd_next():
    dates = sorted(card["due"] for _, card in all_cards())
    print(dates[0] if dates else "no cards yet")


if __name__ == "__main__":
    argv = sys.argv[1:]
    if not argv:
        sys.exit(__doc__)
    cmd, rest = argv[0], argv[1:]
    if cmd == "due":
        cmd_due(rest)
    elif cmd == "add" and len(rest) == 4:
        cmd_add(*rest)
    elif cmd == "grade" and len(rest) == 3:
        cmd_grade(*rest)
    elif cmd == "next":
        cmd_next()
    else:
        sys.exit(__doc__)
