#!/usr/bin/env python3
"""
Project Gutenberg Non-Fiction Crawler — Elementary Edition
-----------------------------------------------------------
Downloads public domain non-fiction .txt files from Project Gutenberg,
then screens each one with an LLM to ensure it is engaging and appropriate
for elementary-age students (grades K–6).

Output structure:
  OUTPUT_DIR/
    raw/          ← every downloaded .txt (passes catalog filter)
    approved/     ← LLM-approved books only (used for vector ingestion)
    metadata.json ← full record of all books with LLM scores
"""

import csv
import gzip
import io
import json
import logging
import os
import re
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional

# ── Configuration ─────────────────────────────────────────────────────────────

CATALOG_URL            = "https://www.gutenberg.org/cache/epub/feeds/pg_catalog.csv.gz"
GUTENBERG_TXT_UTF8     = "https://www.gutenberg.org/cache/epub/{id}/pg{id}-0.txt"
GUTENBERG_TXT_TEMPLATE = "https://www.gutenberg.org/cache/epub/{id}/pg{id}.txt"

TARGET_LANGUAGE   = "en"
TARGET_COUNT      = int(os.getenv("TARGET_COUNT", "75"))
REQUEST_DELAY     = float(os.getenv("REQUEST_DELAY", "2.0"))
OUTPUT_DIR        = Path(os.getenv("OUTPUT_DIR", "/output"))
CATALOG_CACHE     = Path(os.getenv("CATALOG_CACHE", "/cache/pg_catalog.csv.gz"))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# Era filter — sweet spot for accessible, narrative public domain prose
ISSUED_YEAR_MIN = int(os.getenv("ISSUED_YEAR_MIN", "1880"))
ISSUED_YEAR_MAX = int(os.getenv("ISSUED_YEAR_MAX", "1930"))

# File size limits
MIN_FILE_BYTES = 10_000          # < 10 KB → likely a metadata stub
MAX_FILE_BYTES = 500 * 1_024    # > 200 KB → too long for elementary chunking

# LLM screening
LLM_PREVIEW_CHARS = 4_000       # first ~1,000 words sent to LLM
LLM_APPROVE_SCORE = 3           # minimum score (1–5) to move to approved/

# ── Elementary-focused subject keywords ───────────────────────────────────────

NONFICTION_SUBJECTS = {
    # Life sciences
    "animals", "natural history", "zoology", "botany", "birds", "insects",
    "marine biology", "ecology", "wildlife", "plants", "biology", "evolution",
    "microbiology", "genetics",
    # Earth & space
    "astronomy", "geology", "volcanoes", "weather", "meteorology",
    "oceanography", "paleontology", "fossils", "climate",
    # Physical sciences
    "physics", "chemistry", "electricity", "magnetism", "mechanics", "optics",
    "thermodynamics", "atomic",
    # Human body & health
    "physiology", "anatomy", "medicine", "hygiene", "health",
    # Technology & invention
    "inventors", "inventions", "engineering", "technology", "science",
    # Math & scientific method
    "mathematics", "geometry", "experiments",
}

# Hard-reject if any of these appear
REJECT_SUBJECTS = {
    "fiction", "drama", "poetry", "short stories", "comic", "satire",
    "romance", "detective", "mystery", "adventure stories", "fairy tales",
    "juvenile fiction", "law", "economics", "political science", "statistics",
    "commerce", "accounting", "theology", "philosophy", "logic", "rhetoric",
    "linguistics", "grammar", "government", "sociology", "history", "biography",
    "mythology", "folklore", "travel", "exploration",
}

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class BookRecord:
    gutenberg_id: int
    title: str
    author: str
    subjects: list[str]
    language: str
    issued_year: Optional[int]
    txt_url: str
    local_path: str = ""
    approved_path: str = ""
    file_bytes: int = 0
    status: str = "pending"       # pending | downloaded | skipped | error
    llm_score: int = 0            # 1–5; 0 = not screened
    llm_approved: bool = False
    llm_topics: list[str] = field(default_factory=list)
    llm_reasoning: str = ""


# ── Helpers ───────────────────────────────────────────────────────────────────

def slugify(text: str, max_len: int = 50) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")[:max_len]


def http_get(url: str, retries: int = 3) -> Optional[bytes]:
    headers = {"User-Agent": "GutenbergEduCrawler/1.0 (educational use)"}
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            log.warning("HTTP %s attempt %d/%d: %s", e.code, attempt, retries, url)
        except Exception as e:
            log.warning("Error attempt %d/%d: %s", attempt, retries, e)
        if attempt < retries:
            time.sleep(REQUEST_DELAY * attempt)
    return None


def normalize_columns(row: dict) -> dict:
    return {k.strip().lower(): v for k, v in row.items()}


def find_col(row: dict, *candidates: str) -> str:
    for key in candidates:
        if key in row:
            return row[key]
    return ""


def parse_year(val: str) -> Optional[int]:
    m = re.search(r"\b(1[6-9]\d{2}|20\d{2})\b", val)
    return int(m.group(1)) if m else None


def is_good_subject(subjects: list[str]) -> bool:
    lower = " ".join(s.lower() for s in subjects)
    has_reject     = any(kw in lower for kw in REJECT_SUBJECTS)
    has_nonfiction = any(kw in lower for kw in NONFICTION_SUBJECTS)
    return has_nonfiction and not has_reject


# ── LLM screening ─────────────────────────────────────────────────────────────

SCREEN_SYSTEM = """You are a science curriculum specialist evaluating public domain texts
for an AI tutor serving elementary school students (grades K-6, ages 5-12).

Your TWO-PART job:
1. Is the science in this text still accurate and relevant today? Some Victorian-era
   science is outdated or wrong (e.g. ether theory, Lamarckian evolution, miasma theory).
   Reject texts whose core scientific claims have been overturned.
2. Is the text engaging and accessible for modern elementary students?

Respond ONLY with a JSON object - no markdown, no preamble. Schema:
{
  "score": <integer 1-5>,
  "approved": <true|false>,
  "science_topics": [<2-5 specific modern science topics covered, e.g. "photosynthesis", "volcanoes", "electricity">],
  "still_accurate": <true|false>,
  "reasoning": "<one sentence explaining the score and whether the science holds up today>"
}

Scoring rubric:
5 - Science is accurate today, vivid narrative prose, clearly relevant to elementary curriculum
4 - Science is mostly accurate, engaging, minor archaic vocabulary or fringe claims
3 - Science is sound but prose is dry or vocabulary is challenging for young students
2 - Science is partially outdated/inaccurate OR topic has no relevance to modern curriculum
1 - Core science is wrong by modern standards, adult content, or completely inaccessible prose

approved = true if score >= 3 AND still_accurate = true, false otherwise."""


def llm_screen(record: BookRecord, preview_text: str) -> BookRecord:
    if not OPENAI_API_KEY:
        log.warning("[%d] No OPENAI_API_KEY — skipping LLM screen, auto-approving",
                    record.gutenberg_id)
        record.llm_score    = 3
        record.llm_approved = True
        record.llm_reasoning = "LLM screening skipped (no API key)"
        return record

    prompt = (
        f"Title: {record.title}\n"
        f"Author: {record.author}\n"
        f"Subjects: {'; '.join(record.subjects)}\n"
        f"Published: {record.issued_year or 'unknown'}\n\n"
        f"--- FIRST ~1000 WORDS ---\n{preview_text}\n--- END PREVIEW ---\n\n"
        f"Evaluate this text for elementary school students (grades K-6)."
    )

    payload = json.dumps({
        "model": "gpt-5-mini",
        "max_tokens": 400,
        "messages": [
            {"role": "system", "content": SCREEN_SYSTEM},
            {"role": "user",   "content": prompt},
        ],
    }).encode()

    try:
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {OPENAI_API_KEY}",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())

        raw = data["choices"][0]["message"]["content"].strip()
        
        start_idx = raw.find('{')
        end_idx = raw.rfind('}')
        
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            json_str = raw[start_idx:end_idx + 1]
            result = json.loads(json_str)
        else:
            raise ValueError(f"No JSON object found. Raw LLM output: {raw}")

        record.llm_score     = int(result.get("score", 0))
        still_accurate       = bool(result.get("still_accurate", True))
        record.llm_approved  = bool(result.get("approved", False)) and still_accurate
        record.llm_topics    = result.get("science_topics", result.get("topics", []))
        record.llm_reasoning = result.get("reasoning", "")
        log.info("[%d] LLM score %d/5 | approved=%s | %s",
                 record.gutenberg_id, record.llm_score,
                 record.llm_approved, record.llm_reasoning[:80])

    except urllib.error.HTTPError as e:
        error_msg = e.read().decode("utf-8")
        log.warning("[%d] OpenAI API 400 Error: %s", record.gutenberg_id, error_msg)
        record.llm_score    = 3
        record.llm_approved = True
        record.llm_reasoning = "API Error"
    except Exception as e:
        log.warning("[%d] LLM screening failed: %s — auto-approving", record.gutenberg_id, e)
        record.llm_score    = 3
        record.llm_approved = True
        record.llm_reasoning = f"LLM error: {e}"

    return record


# ── Catalog ───────────────────────────────────────────────────────────────────

def fetch_catalog() -> bytes:
    CATALOG_CACHE.parent.mkdir(parents=True, exist_ok=True)
    if CATALOG_CACHE.exists():
        log.info("Using cached catalog at %s", CATALOG_CACHE)
        return CATALOG_CACHE.read_bytes()
    log.info("Downloading Gutenberg catalog (~30 MB)...")
    data = http_get(CATALOG_URL)
    if not data:
        raise RuntimeError("Failed to download catalog")
    CATALOG_CACHE.write_bytes(data)
    log.info("Catalog saved to %s", CATALOG_CACHE)
    return data


def parse_catalog(raw_gz: bytes) -> list[BookRecord]:
    log.info("Parsing catalog...")
    with gzip.open(io.BytesIO(raw_gz), "rt", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        log.info("Catalog columns: %s", reader.fieldnames)
        records: list[BookRecord] = []
        total = 0

        for raw_row in reader:
            total += 1
            row = normalize_columns(raw_row)

            # Language
            lang_raw = find_col(row, "language", "languages").strip().lower()
            if lang_raw != TARGET_LANGUAGE:
                continue

            # Type — only want plain text books, not audio/images
            type_val = find_col(row, "type", "types").strip()
            if type_val and type_val not in ("Text", "Dataset", ""):
                continue

            # Gutenberg's Issued date reflects digitization not original publication,
            # so we skip the era filter. The LLM screen handles quality/accessibility.
            issued_year = None

            # Subjects
            raw_subjects = find_col(row, "subjects", "subject")
            subjects = [s.strip() for s in raw_subjects.split(";") if s.strip()]
            if not is_good_subject(subjects):
                continue

            # ID
            id_val = find_col(row, "text#", "id", "gutenberg_id", "book_id", "number")
            try:
                book_id = int(id_val)
            except (ValueError, TypeError):
                continue

            records.append(BookRecord(
                gutenberg_id=book_id,
                title=find_col(row, "title").strip() or "Unknown",
                author=find_col(row, "authors", "author", "creator").strip() or "Unknown",
                subjects=subjects,
                language=TARGET_LANGUAGE,
                issued_year=issued_year,
                txt_url="",
            ))

    log.info("Scanned %d rows → %d candidates (English text, science subjects)",
             total, len(records))
    return records


# ── Download ──────────────────────────────────────────────────────────────────

def resolve_txt_url(book_id: int) -> Optional[str]:
    for template in (GUTENBERG_TXT_UTF8, GUTENBERG_TXT_TEMPLATE):
        url = template.format(id=book_id)
        try:
            req = urllib.request.Request(
                url, method="HEAD",
                headers={"User-Agent": "GutenbergEduCrawler/1.0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                if r.status == 200:
                    return url
        except Exception:
            pass
    return None


def download_book(record: BookRecord) -> BookRecord:
    raw_dir = OUTPUT_DIR / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    url = resolve_txt_url(record.gutenberg_id)
    if not url:
        log.warning("[%d] No .txt URL found — skipping", record.gutenberg_id)
        record.status = "skipped"
        return record

    record.txt_url = url
    data = http_get(url)
    if data is None:
        record.status = "error"
        return record

    size = len(data)
    if size < MIN_FILE_BYTES:
        log.warning("[%d] Too small (%d B) — skipping", record.gutenberg_id, size)
        record.status = "skipped"
        return record

    if size > MAX_FILE_BYTES:
        log.warning("[%d] Too large (%.1f KB) — skipping",
                    record.gutenberg_id, size / 1024)
        record.status = "skipped"
        return record

    slug     = slugify(record.title)
    filename = f"{record.gutenberg_id}_{slug}.txt"
    out_path = raw_dir / filename
    out_path.write_bytes(data)

    record.local_path = str(out_path)
    record.file_bytes = size
    record.status     = "downloaded"
    log.info("[%d] downloaded  %s  (%.1f KB, %s)",
             record.gutenberg_id, record.title[:55],
             size / 1024, record.issued_year or "?")
    return record


# ── Screen & approve ──────────────────────────────────────────────────────────

def screen_and_approve(record: BookRecord) -> BookRecord:
    try:
        raw_bytes = Path(record.local_path).read_bytes()
        text = raw_bytes.decode("utf-8", errors="replace")
    except Exception as e:
        log.warning("[%d] Could not read file: %s", record.gutenberg_id, e)
        return record

    # Skip the Gutenberg boilerplate header (~first 2000 chars)
    preview = text[2000: 2000 + LLM_PREVIEW_CHARS]
    record  = llm_screen(record, preview)

    if record.llm_approved:
        approved_dir = OUTPUT_DIR / "approved"
        approved_dir.mkdir(parents=True, exist_ok=True)
        dest = approved_dir / Path(record.local_path).name
        dest.write_bytes(raw_bytes)
        record.approved_path = str(dest)
        log.info("[%d] APPROVED -> approved/", record.gutenberg_id)
    else:
        log.info("[%d] rejected (score %d/5)", record.gutenberg_id, record.llm_score)

    return record


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    log.info("=== Gutenberg Elementary Crawler ===")
    log.info("Target: %d books | Era: %d-%d | Delay: %.1fs | Output: %s",
             TARGET_COUNT, ISSUED_YEAR_MIN, ISSUED_YEAR_MAX, REQUEST_DELAY, OUTPUT_DIR)

    if not OPENAI_API_KEY:
        log.warning("OPENAI_API_KEY not set — LLM screening disabled, "
                    "all downloads will be auto-approved.")

    raw_gz  = fetch_catalog()
    catalog = parse_catalog(raw_gz)

    import random
    random.seed(42)
    random.shuffle(catalog)
    candidates = catalog[:TARGET_COUNT * 4]  # over-sample; many will be skipped/rejected

    results:    list[BookRecord] = []
    downloaded  = 0
    approved    = 0

    for i, record in enumerate(candidates):
        if downloaded >= TARGET_COUNT:
            break

        log.info("-- %d/%d | downloaded %d/%d | approved %d --",
                 i + 1, len(candidates), downloaded, TARGET_COUNT, approved)

        record = download_book(record)
        time.sleep(REQUEST_DELAY)

        if record.status == "downloaded":
            downloaded += 1
            record = screen_and_approve(record)
            if record.llm_approved:
                approved += 1

        results.append(record)

    # Metadata sidecar
    meta_path = OUTPUT_DIR / "metadata.json"
    meta = {
        "total_attempted":  len(results),
        "total_downloaded": downloaded,
        "total_approved":   approved,
        "era_filter":       f"{ISSUED_YEAR_MIN}-{ISSUED_YEAR_MAX}",
        "books":            [asdict(r) for r in results],
    }
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    log.info("Metadata -> %s", meta_path)

    skipped = sum(1 for r in results if r.status == "skipped")
    errors  = sum(1 for r in results if r.status == "error")
    log.info("=== Done ===  Downloaded: %d | Approved: %d | Skipped: %d | Errors: %d",
             downloaded, approved, skipped, errors)


if __name__ == "__main__":
    main()
