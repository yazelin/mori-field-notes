#!/usr/bin/env python3

import argparse
import datetime as dt
import json
import logging
import os
import sys
import shlex
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def parse_args():
    parser = argparse.ArgumentParser(description="Publish daily note.")
    parser.add_argument("--date", help="Publish date in YYYY-MM-DD format.")
    return parser.parse_args()


def parse_date(date_str):
    if not date_str:
        return dt.date.today().isoformat()
    try:
        dt.datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("Invalid date format. Use YYYY-MM-DD.") from exc
    return date_str


def setup_logger(date_str):
    logs_dir = ROOT / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / f"{date_str}.log"
    logger = logging.getLogger("publish")
    logger.setLevel(logging.INFO)
    if logger.handlers:
        return logger
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    return logger


def read_json(path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path, data):
    content = json.dumps(data, ensure_ascii=False, indent=2)
    path.write_text(content + "\n", encoding="utf-8")


def load_note(date_str, logger):
    draft_path = ROOT / "drafts" / f"{date_str}.json"
    if not draft_path.exists():
        raise FileNotFoundError(f"Draft not found: {draft_path}")
    note = read_json(draft_path)
    if not isinstance(note, dict):
        raise ValueError("Draft JSON must be an object.")
    if note.get("date") != date_str:
        note["date"] = date_str
    if not note.get("image"):
        note["image"] = f"images/{date_str}.webp"
    logger.info("Loaded draft %s", draft_path)
    return note


def ensure_image(date_str):
    image_path = ROOT / "docs" / "images" / f"{date_str}.webp"
    if not image_path.exists():
        raise FileNotFoundError(f"Image not found: {image_path}")
    return image_path


def update_notes(note, logger):
    notes_path = ROOT / "docs" / "notes.json"
    notes = []
    if notes_path.exists():
        notes = read_json(notes_path)
    if not isinstance(notes, list):
        raise ValueError("notes.json must be a list.")
    notes.insert(0, note)
    write_json(notes_path, notes)
    logger.info("Updated notes.json")


def update_state(note, date_str, logger):
    state_path = ROOT / "state.json"
    state = {}
    if state_path.exists():
        state = read_json(state_path)
    if not isinstance(state, dict):
        raise ValueError("state.json must be an object.")
    state["lastPublishDate"] = date_str
    total = state.get("totalNotes", 0)
    try:
        total = int(total)
    except (TypeError, ValueError):
        total = 0
    state["totalNotes"] = total + 1
    topics = state.get("topics")
    if not isinstance(topics, list):
        topics = []
    tags = []
    if isinstance(note.get("tags"), list):
        tags = [str(tag) for tag in note["tags"] if tag]
    elif note.get("tag"):
        tags = [note["tag"]]
    for tag in tags:
        if tag not in topics:
            topics.append(tag)
    state["topics"] = topics
    monthly_stats = state.get("monthlyStats")
    if not isinstance(monthly_stats, dict):
        monthly_stats = {}
    month_key = date_str[:7]
    try:
        monthly_stats[month_key] = int(monthly_stats.get(month_key, 0)) + 1
    except (TypeError, ValueError):
        monthly_stats[month_key] = 1
    state["monthlyStats"] = monthly_stats
    write_json(state_path, state)
    logger.info("Updated state.json")


def run_git(date_str, title, logger):
    message = f"📝 Daily note: {title} ({date_str})"
    quoted_message = shlex.quote(message)
    cmd = (
        f"git add docs/notes.json state.json docs/images/{date_str}.webp"
        f" && git commit -m {quoted_message}"
        " && git push"
    )
    logger.info("Running git command")
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            check=True,
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        if result.stdout:
            logger.info(result.stdout.strip())
        if result.stderr:
            logger.info(result.stderr.strip())
        return True
    except subprocess.CalledProcessError as exc:
        logger.error("Git command failed with code %s", exc.returncode)
        if exc.stdout:
            logger.error(exc.stdout.strip())
        if exc.stderr:
            logger.error(exc.stderr.strip())
        return False


def main():
    args = parse_args()
    try:
        date_str = parse_date(args.date)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    logger = setup_logger(date_str)
    logger.info("Publishing note for %s", date_str)
    try:
        note = load_note(date_str, logger)
        ensure_image(date_str)
        update_notes(note, logger)
        update_state(note, date_str, logger)
        title = note.get("title")
        if not title:
            raise ValueError("Draft must include a title for the commit message.")
        if not run_git(date_str, title, logger):
            return 1
    except Exception as exc:
        logger.exception("Publish failed: %s", exc)
        return 1
    logger.info("Publish complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
