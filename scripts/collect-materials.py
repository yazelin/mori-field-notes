#!/usr/bin/env python3

import argparse
import datetime as dt
import json
import logging
import os
import sys
import time
from typing import Dict, List, Optional
from urllib.parse import urlsplit, urlunsplit

try:
    import requests
except ImportError:
    print("Missing 'requests' package. Install with: pip install requests", file=sys.stderr)
    sys.exit(1)

try:
    from zoneinfo import ZoneInfo
except ImportError:
    print("Python 3.9+ is required for zoneinfo support.", file=sys.stderr)
    sys.exit(1)

BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
KEYWORDS = ["AI agents", "MCP", "GitHub trending", "AI coding tools", "LLM"]
DEFAULT_COUNT = 10
REQUEST_TIMEOUT = 20
MAX_RETRIES = 4
BACKOFF_BASE_SECONDS = 1.5
REQUEST_DELAY_SECONDS = 1.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect daily materials via Brave Search.")
    parser.add_argument("--date", help="Target date in YYYY-MM-DD (defaults to today in Asia/Taipei).")
    return parser.parse_args()


def get_target_date(date_str: Optional[str]) -> str:
    if date_str:
        try:
            parsed = dt.datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError("Invalid --date format, expected YYYY-MM-DD.") from exc
        return parsed.date().isoformat()
    # Default to today's date in Asia/Taipei.
    return dt.datetime.now(ZoneInfo("Asia/Taipei")).date().isoformat()


def setup_logging(date_str: str, base_dir: str) -> logging.Logger:
    log_dir = os.path.join(base_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"{date_str}.log")

    logger = logging.getLogger("collect-materials")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    logger.propagate = False
    return logger


def normalize_url(url: str) -> str:
    # Basic normalization to improve URL deduplication without stripping query parameters.
    parts = urlsplit(url)
    normalized = urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), parts.query, ""))
    return normalized


def request_with_retries(
    session: requests.Session,
    headers: Dict[str, str],
    params: Dict[str, str],
    logger: logging.Logger,
) -> requests.Response:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = session.get(
                BRAVE_ENDPOINT,
                headers=headers,
                params=params,
                timeout=REQUEST_TIMEOUT,
            )

            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                sleep_seconds = float(retry_after) if retry_after else BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
                logger.warning("Rate limited (429). Sleeping for %.1f seconds.", sleep_seconds)
                time.sleep(sleep_seconds)
                continue

            if 500 <= response.status_code < 600:
                raise requests.HTTPError(f"Server error: {response.status_code}")

            response.raise_for_status()
            remaining = response.headers.get("X-RateLimit-Remaining")
            reset = response.headers.get("X-RateLimit-Reset")
            if remaining == "0" and reset:
                try:
                    wait_seconds = max(0, int(reset) - int(time.time()))
                except ValueError:
                    wait_seconds = 0
                if wait_seconds:
                    logger.warning("Rate limit reached. Sleeping for %d seconds.", wait_seconds)
                    time.sleep(wait_seconds)
            return response
        except (requests.RequestException, ValueError) as exc:
            if attempt == MAX_RETRIES:
                logger.error("Request failed after %d attempts: %s", attempt, exc)
                raise
            sleep_seconds = BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
            logger.warning("Request failed (%s). Retrying in %.1f seconds.", exc, sleep_seconds)
            time.sleep(sleep_seconds)

    raise RuntimeError("Exhausted retries without a successful response.")


def fetch_results(
    session: requests.Session,
    keyword: str,
    date_str: str,
    api_key: str,
    logger: logging.Logger,
) -> List[Dict[str, str]]:
    headers = {
        "Accept": "application/json",
        "X-Subscription-Token": api_key,
    }
    params = {
        "q": f"{keyword} {date_str}",
        "count": str(DEFAULT_COUNT),
        "freshness": "day",
    }
    response = request_with_retries(session, headers, params, logger)
    try:
        data = response.json()
    except ValueError as exc:
        logger.error("Failed to decode JSON for keyword %s: %s", keyword, exc)
        return []
    return data.get("web", {}).get("results", [])


def collect_materials(date_str: str, api_key: str, logger: logging.Logger) -> List[Dict[str, str]]:
    session = requests.Session()
    entries: List[Dict[str, str]] = []
    seen_urls: set[str] = set()

    for index, keyword in enumerate(KEYWORDS):
        logger.info("Searching Brave for keyword: %s", keyword)
        results = fetch_results(session, keyword, date_str, api_key, logger)
        for result in results:
            title = (result.get("title") or "").strip()
            url = (result.get("url") or "").strip()
            if not title or not url:
                continue

            normalized = normalize_url(url)
            if normalized in seen_urls:
                continue

            summary = (result.get("description") or result.get("snippet") or "").strip()
            entries.append({"title": title, "url": url, "summary": summary})
            seen_urls.add(normalized)

        if index < len(KEYWORDS) - 1:
            # Small delay between queries to respect API rate limits.
            time.sleep(REQUEST_DELAY_SECONDS)

    return entries


def write_materials(entries: List[Dict[str, str]], date_str: str, base_dir: str) -> str:
    materials_dir = os.path.join(base_dir, "materials")
    os.makedirs(materials_dir, exist_ok=True)
    output_path = os.path.join(materials_dir, f"{date_str}.json")
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(entries, handle, ensure_ascii=False, indent=2)
    return output_path


def main() -> None:
    args = parse_args()
    try:
        date_str = get_target_date(args.date)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        sys.exit(1)

    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    logger = setup_logging(date_str, base_dir)

    api_key = os.environ.get("BRAVE_API_KEY")
    if not api_key:
        logger.error("BRAVE_API_KEY is not set.")
        print("BRAVE_API_KEY environment variable is required.", file=sys.stderr)
        sys.exit(1)

    try:
        entries = collect_materials(date_str, api_key, logger)
        output_path = write_materials(entries, date_str, base_dir)
        logger.info("Wrote %d entries to %s", len(entries), output_path)
        print(output_path)
    except Exception:
        logger.exception("Failed to collect materials.")
        print("Failed to collect materials. See logs for details.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
