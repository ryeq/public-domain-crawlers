# Gutenberg Non-Fiction Crawler

Downloads public domain non-fiction `.txt` files from Project Gutenberg for
use in a student library / AI tutor vector database pipeline.

## What it produces

```
output/
├── 123_the_origin_of_species.txt
├── 456_wealth_of_nations.txt
├── ...
└── metadata.json          ← structured record of every downloaded book
```

`metadata.json` shape:
```json
{
  "total_attempted": 110,
  "total_downloaded": 75,
  "books": [
    {
      "gutenberg_id": 2009,
      "title": "The Origin of Species by Means of Natural Selection",
      "author": "Darwin, Charles",
      "subjects": ["Natural selection", "Evolution (Biology)"],
      "language": "en",
      "txt_url": "https://www.gutenberg.org/cache/epub/2009/pg2009-0.txt",
      "local_path": "/output/2009_the_origin_of_species.txt",
      "file_bytes": 891234,
      "status": "downloaded"
    }
  ]
}
```

---

## Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (Mac/Windows)
  or Docker Engine + Compose plugin (Linux)

---

## Quick start

```bash
# 1. Clone / copy this folder to your machine
cd gutenberg_crawler

# 2. Build the image
docker compose build

# 3. Run (downloads 75 books by default)
docker compose up

# Books appear in ./output/ as they download.
# The run takes ~5–10 minutes (polite 2 s delay between requests).
```

---

## Configuration

Edit `docker-compose.yml` environment block — no rebuild needed:

| Variable | Default | Description |
|---|---|---|
| `TARGET_COUNT` | `75` | Number of books to download |
| `REQUEST_DELAY` | `2.0` | Seconds between HTTP requests |

Example — download 200 books:
```yaml
environment:
  TARGET_COUNT: "200"
  REQUEST_DELAY: "2.0"
```

---

## Subject coverage

The crawler targets these Library of Congress subject areas:

**STEM** — science, mathematics, physics, chemistry, biology, botany, zoology,
astronomy, geology, natural history, medicine, physiology, anatomy, engineering,
electricity, mechanics, evolution, ecology

**Humanities** — history, ancient history, medieval, civilization, biography,
autobiography, memoirs, philosophy, ethics, logic, rhetoric, linguistics,
language, grammar, literature history, mythology, religion, theology,
archaeology

**Social sciences** — economics, political science, government, law, sociology,
education, psychology, anthropology, geography, exploration, travel, commerce,
agriculture, statistics

Fiction, drama, poetry, and juvenile fiction are explicitly excluded.

---

## Re-running

The Gutenberg catalog gz is cached in `./cache/` after the first run.
Subsequent runs skip the 30 MB download and start filtering immediately.

To force a fresh catalog:
```bash
rm -rf ./cache
docker compose up
```

---

## Next steps (LLM tagging & vector ingestion)

Each `.txt` file pairs with its `metadata.json` entry, giving you:
- Gutenberg ID (stable, deduplicated key)
- Title, author, subjects (seed tags for your LLM tagger)
- File path (for chunking and embedding)

Recommended pipeline:
1. **Chunk** each `.txt` into ~500-token passages (LangChain `RecursiveCharacterTextSplitter` or similar)
2. **Tag** each chunk via LLM (subjects, difficulty level, key concepts)
3. **Embed** with `text-embedding-3-small` or similar
4. **Ingest** into Pinecone / Chroma / pgvector with metadata filters

---

## Project Gutenberg terms of use

This crawler respects Gutenberg's bulk download policy:
- Uses their official catalog CSV (not scraping HTML)
- Downloads from the `/cache/epub/` CDN path
- Sets a polite `User-Agent` header
- Enforces a ≥2 second delay between requests

For large runs (500+ books) consider using one of their
[official rsync mirrors](https://www.gutenberg.org/help/mirroring.html)
instead of the CDN.
# public-domain-crawlers
