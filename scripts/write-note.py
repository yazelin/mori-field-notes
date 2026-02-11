#!/usr/bin/env python3
import argparse
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DRAFTS_DIR = ROOT / 'drafts'
MATERIALS_DIR = ROOT / 'materials'
LOGS_DIR = ROOT / 'logs'
VALID_TAGS = ['#tech-radar', '#til', '#opinion', '#bug-story', '#monthly']


def setup_logging(date_str):
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOGS_DIR / f"{date_str}.log"
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s',
                        handlers=[logging.FileHandler(log_file, encoding='utf-8'),
                                  logging.StreamHandler(sys.stdout)])
    logging.info(f"Logging to {log_file}")


def read_materials(date_str):
    path = MATERIALS_DIR / f"{date_str}.json"
    if not path.exists():
        raise FileNotFoundError(f"Materials file not found: {path}")
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def spawn_sessions_agent(prompt, timeout=30):
    """
    Try to use sessions_spawn if available. If not, fallback to a simulated local writer.
    """
    try:
        import sessions_spawn
    except Exception:
        # Simulated fallback: basic deterministic writer using the prompt
        return simulated_writer(prompt)

    # If sessions_spawn is available, call it with a reasonable system prompt
    system_prompt = (
        "You are Mori, an observant technical writer. Write a 200-500 word note in first person (我), "
        "include technical observations and personal judgment, and cite sources provided.")

    def call_agent():
        return sessions_spawn.spawn(system=system_prompt, user=prompt)

    with ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(call_agent)
        try:
            return fut.result(timeout=timeout)
        except TimeoutError:
            raise TimeoutError("sessions_spawn agent timed out")


def simulated_writer(prompt):
    # Very simple heuristic writer; in real operation the sessions_spawn agent should replace this.
    title = f"觀察：{prompt[:60]}"
    content = (
        "我最近觀察到技術領域裡的一些趨勢，以下是整理與心得。\n\n"
        "（此為自動生成的示範內容，正式運作時會由 sessions_spawn 撰寫）\n"
    )
    sources = []
    return {"title": title, "content": content, "sources": sources, "tag": "#tech-radar"}


def choose_tag(candidate_text):
    # Basic heuristic mapping to tags
    lower = candidate_text.lower()
    if 'trend' in lower or 'trending' in lower or 'trend' in candidate_text:
        return '#tech-radar'
    if 'learn' in lower or 'til' in lower or 'learned' in lower:
        return '#til'
    if 'bug' in lower or 'error' in lower or 'issue' in lower:
        return '#bug-story'
    return '#opinion'


def write_draft(date_str, note):
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    output_path = DRAFTS_DIR / f"{date_str}.json"
    payload = {
        'date': date_str,
        'tag': note.get('tag') or choose_tag(note.get('content', '')),
        'title': note.get('title'),
        'content': note.get('content'),
        'sources': note.get('sources', [])
    }
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return output_path


def main():
    parser = argparse.ArgumentParser(description='Write note from materials via sessions_spawn')
    parser.add_argument('--date', help='YYYY-MM-DD')
    parser.add_argument('--timeout', type=int, default=30, help='Agent timeout seconds')
    args = parser.parse_args()

    date_str = args.date or dt_now()
    setup_logging(date_str)
    try:
        materials = read_materials(date_str)
    except Exception as e:
        logging.exception('Failed to read materials')
        sys.exit(1)

    # Create a short prompt for the agent using top 3 materials
    snippets = []
    for m in materials[:3]:
        snippets.append(f"{m.get('title')} - {m.get('url')}")
    prompt = '\n'.join(snippets)

    try:
        note = spawn_sessions_agent(prompt, timeout=args.timeout)
    except Exception as e:
        logging.exception('Agent failed')
        sys.exit(1)

    # Validate note
    if not note.get('title') or not note.get('content'):
        logging.error('Agent returned invalid note')
        sys.exit(1)

    path = write_draft(date_str, note)
    logging.info('Wrote draft to %s', path)


def dt_now():
    from datetime import datetime
    return datetime.now().date().isoformat()


if __name__ == '__main__':
    main()
