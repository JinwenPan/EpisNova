#!/usr/bin/env python3
"""
fetch_arxiv_rss.py
==================
Fetches the daily arXiv RSS feed for Computer Science (cs), splits items
into *current* (pubDate matches the feed date) and *old* (everything else),
and saves them to separate dated JSON files.

Designed to be run once per day via cron at 11 PM ET.

Usage:
    python fetch_arxiv_rss.py
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone, timedelta
from email.utils import parsedate

import feedparser
import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

RSS_URL = "http://rss.arxiv.org/rss/cs"
OUTPUT_DIR = ""
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) arXiv-Daily-Sync-Bot/1.0"
)
REQUEST_TIMEOUT = 15  # seconds

# ---------------------------------------------------------------------------
# Logging setup – log file named by US Eastern date at script start
# ---------------------------------------------------------------------------

ET = timezone(timedelta(hours=-5))
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"

logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    datefmt=LOG_DATEFMT,
)
logger = logging.getLogger(__name__)

os.makedirs(LOG_DIR, exist_ok=True)
_file_handler = logging.FileHandler(
    os.path.join(LOG_DIR, f"{datetime.now(ET).strftime('%Y-%m-%d')}.log"),
    encoding="utf-8",
)
_file_handler.setLevel(logging.INFO)
_file_handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATEFMT))
logger.addHandler(_file_handler)


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def clean_text(text: str) -> str:
    """Replace newlines with spaces and collapse consecutive whitespace."""
    return " ".join(text.split())


def extract_abstract(description: str) -> str:
    """Extract the abstract from an arXiv RSS <description> field.

    The raw field looks like:
        arXiv:2602.23365v1 Announce Type: new
        Abstract: Organisations face polycrisis ...

    We strip everything up to and including 'Abstract:' and clean
    the remaining text.
    """
    marker = "Abstract:"
    idx = description.find(marker)
    if idx != -1:
        return clean_text(description[idx + len(marker):])
    # If no marker found, return the whole thing cleaned
    return clean_text(description)


def _parse_date_fields(obj: feedparser.FeedParserDict) -> str | None:
    """
    Extract a date from a feedparser dict (feed header or entry) and return
    it as a YYYY-MM-DD string.  Tries parsed 9-tuples first, then raw strings.
    """
    for key in ("published_parsed", "updated_parsed"):
        parsed = obj.get(key)
        if parsed:
            return f"{parsed.tm_year:04d}-{parsed.tm_mon:02d}-{parsed.tm_mday:02d}"

    # Fallback: try raw string fields
    for key in ("published", "updated", "dc_date"):
        raw = obj.get(key, "")
        if not raw:
            continue
        # Try RFC 2822 (e.g. "Mon, 02 Mar 2026 00:00:00 -0500")
        parsed_tuple = parsedate(raw)
        if parsed_tuple:
            return f"{parsed_tuple[0]:04d}-{parsed_tuple[1]:02d}-{parsed_tuple[2]:02d}"
        # Try ISO 8601 prefix (e.g. "2026-03-02T...")
        if len(raw) >= 10 and raw[4] == "-":
            return raw[:10]

    return None


def save_json(filepath: str, data: list[dict]) -> None:
    """Write *data* to *filepath* atomically (flush + fsync)."""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.flush()
        os.fsync(fh.fileno())


# ---------------------------------------------------------------------------
# Main routine
# ---------------------------------------------------------------------------

def main() -> None:
    # ------------------------------------------------------------------
    # Step 1 – Fetch the RSS feed XML
    # ------------------------------------------------------------------
    logger.info("Fetching arXiv CS RSS feed from %s …", RSS_URL)

    try:
        response = requests.get(
            RSS_URL,
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
    except requests.exceptions.ConnectionError as exc:
        logger.error("Connection error while fetching the RSS feed: %s", exc)
        sys.exit(1)
    except requests.exceptions.Timeout as exc:
        logger.error("Request timed out: %s", exc)
        sys.exit(1)
    except requests.exceptions.HTTPError as exc:
        logger.error("HTTP error: %s", exc)
        sys.exit(1)
    except requests.exceptions.RequestException as exc:
        logger.error("Unexpected request error: %s", exc)
        sys.exit(1)

    logger.info("Feed fetched successfully (%d bytes).", len(response.content))

    # ------------------------------------------------------------------
    # Step 2 – Parse the feed and extract the feed-level pubDate
    # ------------------------------------------------------------------
    feed = feedparser.parse(response.content)

    feed_date = _parse_date_fields(feed.feed)
    if feed_date is None:
        logger.error(
            "Could not determine the feed-level pubDate. "
            "Available feed keys: %s",
            list(feed.feed.keys()),
        )
        sys.exit(1)


    logger.info("Feed pubDate: %s", feed_date)

    # ------------------------------------------------------------------
    # Step 3 – Handle the no-items case (weekend / holiday)
    # ------------------------------------------------------------------
    if not feed.entries:
        logger.info(
            "Feed date %s contains no items (weekend/holiday). "
            "Nothing to do.",
            feed_date,
        )
        return

    # ------------------------------------------------------------------
    # Step 4 – Classify entries into current vs. old
    # ------------------------------------------------------------------
    current_papers: list[dict[str, str]] = []
    old_papers: list[dict[str, str]] = []

    for entry in feed.entries:
        try:
            entry_date = _parse_date_fields(entry)
            link = entry.get("link", "")
            title = clean_text(entry.get("title", ""))
            abstract = extract_abstract(entry.get("summary", ""))

            if entry_date == feed_date:
                current_papers.append({
                    "link": link,
                    "title": title,
                    "abstract": abstract,
                })
            else:
                old_papers.append({
                    "link": link,
                    "title": title,
                    "abstract": abstract,
                    "pubdate": entry_date or "unknown",
                })
        except Exception:
            logger.warning("Failed to parse entry, skipping.", exc_info=True)

    logger.info(
        "Classified %d current and %d old papers for feed date %s.",
        len(current_papers),
        len(old_papers),
        feed_date,
    )

    # ------------------------------------------------------------------
    # Step 5 – Save JSON files (only for non-empty groups)
    # ------------------------------------------------------------------
    if current_papers:
        path = os.path.join(OUTPUT_DIR, f"arxiv_cs_{feed_date}.json")
        save_json(path, current_papers)
        logger.info("Saved %d current papers to %s", len(current_papers), path)
    else:
        logger.info("No current papers for %s.", feed_date)

    if old_papers:
        path = os.path.join(OUTPUT_DIR, f"arxiv_cs_{feed_date}_old.json")
        save_json(path, old_papers)
        logger.info("Saved %d old papers to %s", len(old_papers), path)
    else:
        logger.info("No old papers for %s.", feed_date)


if __name__ == "__main__":
    main()
