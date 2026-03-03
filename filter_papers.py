#!/usr/bin/env python3
"""
filter_papers.py
================
Downstream of fetch_arxiv_rss.py.  Loads today's arXiv CS JSON, screens
each paper for relevance using Gemini, and generates a Chinese-language
digest for relevant papers.

Designed to be run once per day via cron at 11:30 PM ET (30 min after fetch).

Usage:
    python filter_papers.py
"""

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone, timedelta

from google import genai

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# API Key: prefer environment variable, fall back to script-level constant.
GEMINI_API_KEY = "" 

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "arxiv_data")
QUERY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "input.txt")
DIGEST_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "digests")
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")

MODEL = "gemini-3-flash-preview"
RPM = 30  # Max requests per minute (adjust to your quota)

# ---------------------------------------------------------------------------
# Logging setup – log file named by US Eastern date at script start
# ---------------------------------------------------------------------------

ET = timezone(timedelta(hours=-5))
TODAY_ET = datetime.now(ET).strftime("%Y-%m-%d")

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
    os.path.join(LOG_DIR, f"{TODAY_ET}.log"),
    encoding="utf-8",
)
_file_handler.setLevel(logging.INFO)
_file_handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATEFMT))
logger.addHandler(_file_handler)


# ---------------------------------------------------------------------------
# Rate-limited Gemini caller
# ---------------------------------------------------------------------------

class GeminiCaller:
    """Thin wrapper around the google-genai SDK with rate limiting."""

    def __init__(self, api_key: str, model: str, rpm: int) -> None:
        self.client = genai.Client(api_key=api_key)
        self.model = model
        self.interval = 60.0 / rpm  # seconds between calls
        self._last_call: float = 0.0

    def _wait(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if elapsed < self.interval:
            time.sleep(self.interval - elapsed)

    def generate(self, prompt: str, retries: int = 3) -> str:
        """Call Gemini with rate limiting and retry on transient errors."""
        for attempt in range(1, retries + 1):
            self._wait()
            try:
                self._last_call = time.monotonic()
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=prompt,
                )
                return response.text or ""
            except Exception as exc:
                logger.warning(
                    "Gemini API error (attempt %d/%d): %s",
                    attempt, retries, exc,
                )
                if attempt < retries:
                    time.sleep(2 ** attempt)  # exponential back-off
        logger.error("Gemini API failed after %d attempts.", retries)
        return ""


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

SCREENING_PROMPT = """\
You are a research paper screening assistant.

Given the following research interest description and a paper's title and abstract, \
determine whether this paper is relevant to the research interest.

Research interest:
{query}

Paper title: {title}
Paper abstract: {abstract}

Reply with ONLY "yes" or "no". Do not include any other text or explanation.
"""

EXPLANATION_PROMPT = """\
你是一个学术论文解读助手。请用通俗易懂的中文，解释以下论文：
1. 这篇论文具体要解决什么问题？
2. 它是怎么做的（核心方法）？

论文标题: {title}
论文摘要: {abstract}

请直接开始解释，不要重复标题。
"""


# ---------------------------------------------------------------------------
# Main routine
# ---------------------------------------------------------------------------

def main() -> None:
    logger.info("=== filter_papers.py started ===")

    # ------------------------------------------------------------------
    # Step 1 – Resolve API key
    # ------------------------------------------------------------------
    api_key = os.environ.get("GEMINI_API_KEY", "") or GEMINI_API_KEY
    if not api_key:
        logger.error(
            "No Gemini API key found. Set the GEMINI_API_KEY environment "
            "variable or fill in the GEMINI_API_KEY constant in the script."
        )
        sys.exit(1)

    # ------------------------------------------------------------------
    # Step 2 – Load query
    # ------------------------------------------------------------------
    if not os.path.isfile(QUERY_FILE):
        logger.error("Query file not found: %s", QUERY_FILE)
        sys.exit(1)

    with open(QUERY_FILE, "r", encoding="utf-8") as fh:
        query = fh.read().strip()

    if not query:
        logger.error("Query file is empty: %s", QUERY_FILE)
        sys.exit(1)

    logger.info("Loaded query from %s (%d chars).", QUERY_FILE, len(query))

    # ------------------------------------------------------------------
    # Step 3 – Load today's JSON
    # ------------------------------------------------------------------
    json_path = os.path.join(DATA_DIR, f"arxiv_cs_{TODAY_ET}.json")

    if not os.path.isfile(json_path):
        logger.info(
            "No data file for today (%s). Skipping.", json_path,
        )
        return

    with open(json_path, "r", encoding="utf-8") as fh:
        papers = json.load(fh)

    logger.info("Loaded %d papers from %s.", len(papers), json_path)

    if not papers:
        logger.info("Paper list is empty. Nothing to do.")
        return

    # ------------------------------------------------------------------
    # Step 4 – Phase 1: Relevance screening
    # ------------------------------------------------------------------
    caller = GeminiCaller(api_key=api_key, model=MODEL, rpm=RPM)

    relevant_papers: list[dict] = []

    for i, paper in enumerate(papers, 1):
        title = paper.get("title", "")
        abstract = paper.get("abstract", "")

        prompt = SCREENING_PROMPT.format(
            query=query, title=title, abstract=abstract,
        )
        answer = caller.generate(prompt).strip()

        is_relevant = answer.lower().startswith("yes")

        logger.info(
            "[Screen %d/%d] %s → %s",
            i, len(papers),
            title[:60],
            "RELEVANT" if is_relevant else "skip",
        )

        if is_relevant:
            relevant_papers.append(paper)

    logger.info(
        "Screening complete: %d / %d papers are relevant.",
        len(relevant_papers), len(papers),
    )

    if not relevant_papers:
        logger.info("No relevant papers found. No digest generated.")
        return

    # ------------------------------------------------------------------
    # Step 5 – Phase 2: Chinese explanation for relevant papers
    # ------------------------------------------------------------------
    digest_entries: list[str] = []

    for i, paper in enumerate(relevant_papers, 1):
        title = paper.get("title", "")
        abstract = paper.get("abstract", "")
        link = paper.get("link", "")

        prompt = EXPLANATION_PROMPT.format(title=title, abstract=abstract)
        explanation = caller.generate(prompt).strip()

        logger.info(
            "[Explain %d/%d] %s",
            i, len(relevant_papers), title[:60],
        )

        entry = f"### [{title}]({link})\n\n{explanation}\n"
        digest_entries.append(entry)

    # ------------------------------------------------------------------
    # Step 6 – Write digest markdown
    # ------------------------------------------------------------------
    os.makedirs(DIGEST_DIR, exist_ok=True)
    digest_path = os.path.join(DIGEST_DIR, f"{TODAY_ET}.md")

    header = f"# arXiv CS 每日精选 — {TODAY_ET}\n\n"
    header += f"共筛选 {len(papers)} 篇论文，其中 {len(relevant_papers)} 篇与研究兴趣相关。\n\n---\n\n"

    with open(digest_path, "w", encoding="utf-8") as fh:
        fh.write(header)
        fh.write("\n---\n\n".join(digest_entries))
        fh.flush()
        os.fsync(fh.fileno())

    logger.info("Digest saved to %s", digest_path)
    logger.info("=== filter_papers.py finished ===")


if __name__ == "__main__":
    main()
