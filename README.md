# Gutenberg Elementary Crawler

Downloads public domain non-fiction `.txt` files from Project Gutenberg for
use in an AI tutor vector database pipeline targeting elementary school students
(grades K–6). Each downloaded book is screened by an LLM for age-appropriateness
and factual accuracy before being moved to the approved output folder.

---

## Scripts

There are two specialized crawlers — run them independently to build separate
subject-focused collections.

| Script | Focus | LLM checks for |
|---|---|---|
| `crawler_science.py` | Life sciences, earth & space, physics, chemistry, invention | Science still accurate by modern standards |
| `crawler_history.py` | Ancient civilizations, world history, American history, biography | History not discredited or significantly biased |

Both scripts share the same architecture — only the subject filters and LLM
screening prompts differ.

---

## Output structure

Each crawler produces:

```
<OUTPUT_DIR>/
├── raw/                        ← every .txt that passed catalog filters
│   ├── 2009_the_origin_of_species.txt
│   └── ...
├── approved/                   ← LLM-vetted books ready for vector ingestion
│   ├── 2009_the_origin_of_species.txt
│   └── ...
└── metadata.json               ← full record of all books with LLM scores
```

`metadata.json` shape:

```json
{
  "total_attempted": 300,
  "total_downloaded": 250,
  "total_approved": 181,
  "books": [
    {
      "gutenberg_id": 2009,
      "title": "The Origin of Species by Means of Natural Selection",
      "author": "Darwin, Charles",
      "subjects": ["Natural selection", "Evolution (Biology)"],
      "language": "en",
      "txt_url": "https://www.gutenberg.org/cache/epub/2009/pg2009-0.txt",
      "local_path": "/output/raw/2009_the_origin_of_species.txt",
      "approved_path": "/output/approved/2009_the_origin_of_species.txt",
      "file_bytes": 189432,
      "status": "downloaded",
      "llm_score": 4,
      "llm_approved": true,
      "llm_topics": ["evolution", "natural selection", "biology"],
      "still_accurate": true,
      "reasoning": "Foundational biology text, core claims validated by modern science."
    }
  ]
}
```

---

## Prerequisites

- Python 3.10+ (no pip installs required — stdlib only)
- An OpenAI API key (for LLM screening)
- The scripts run directly on your machine — no Docker needed

---

## Running the science crawler

```bash
export OUTPUT_DIR=/path/to/output/non_fiction_science
export CATALOG_CACHE=/path/to/cache/pg_catalog.csv.gz
export OPENAI_API_KEY=sk-proj-...
export TARGET_COUNT=250

python3 /path/to/crawler_science.py
```

## Running the history crawler

```bash
export OUTPUT_DIR=/path/to/output/non_fiction_history
export CATALOG_CACHE=/path/to/cache/pg_catalog.csv.gz
export OPENAI_API_KEY=sk-proj-...
export TARGET_COUNT=250

python3 /path/to/crawler_history.py
```

> The Gutenberg catalog (~30 MB) is cached after the first run. Subsequent runs
> skip the download and go straight to filtering — point both crawlers at the
> same `CATALOG_CACHE` path to avoid downloading it twice.

---

## Configuration

All options are set via environment variables — no code changes needed.

| Variable | Default | Description |
|---|---|---|
| `TARGET_COUNT` | `75` | Number of books to download |
| `REQUEST_DELAY` | `2.0` | Seconds between HTTP requests (be polite) |
| `OUTPUT_DIR` | `/output` | Where raw/ and approved/ folders are created |
| `CATALOG_CACHE` | `/cache/pg_catalog.csv.gz` | Path to cache the Gutenberg catalog |
| `OPENAI_API_KEY` | — | Required for LLM screening |

---

## Subject coverage

### crawler_science.py

**Life sciences** — animals, natural history, zoology, botany, birds, insects,
marine biology, ecology, wildlife, plants, biology, evolution, microbiology, genetics

**Earth & space** — astronomy, geology, volcanoes, weather, meteorology,
oceanography, paleontology, fossils, climate

**Physical sciences** — physics, chemistry, electricity, magnetism, mechanics,
optics, thermodynamics

**Human body & health** — physiology, anatomy, medicine, hygiene, health

**Technology & invention** — inventors, inventions, engineering, technology, science

**Rejected:** fiction, drama, poetry, history, biography, philosophy, law,
economics, linguistics, government, sociology

### crawler_history.py

**Ancient civilizations** — ancient history, egypt, greece, rome, mesopotamia,
babylon, persia, carthage, archaeology

**Medieval & early modern** — medieval, middle ages, renaissance, crusades,
vikings, byzantine, feudalism

**World civilizations** — aztec, maya, inca, china, japan, india, africa,
native peoples, indigenous

**American history** — united states history, american revolution, civil war,
colonial, frontier, westward expansion

**Exploration & biography** — explorers, discovery, voyages, biography, autobiography

**Rejected:** fiction, drama, poetry, science, physics, chemistry, biology,
mathematics, engineering, medicine

---

## LLM screening

After each download the first ~1,000 words are sent to `gpt-5-mini` for evaluation.
Books that fail are kept in `raw/` but not copied to `approved/`.

**Science screener** checks:

- Is the science still accurate by modern standards? (rejects ether theory,
  miasma theory, Lamarckian evolution, etc.)
- Is the prose engaging and accessible for grades K–6?

**History screener** checks:

- Is the historical account still considered broadly accurate? (rejects Lost
  Cause Civil War narratives, eurocentric dismissal of non-Western civilizations,
  myth presented as fact)
- Is the prose engaging and accessible for grades K–6?

Both return a score (1–5) and a `still_accurate` boolean. A book must score ≥3
**and** pass the accuracy check to be approved.

If `OPENAI_API_KEY` is not set, screening is skipped and all downloads are
auto-approved.

---

## Re-running

The catalog is cached after the first run. To force a fresh download:

```bash
rm /path/to/cache/pg_catalog.csv.gz
```

To resume a partial run without re-downloading already-saved files, the scripts
will skip books whose output file already exists in `raw/`.

---

## Next steps — vector ingestion pipeline

The `approved/` folder and `metadata.json` are designed to feed directly into
a chunking and embedding pipeline. Recommended steps:

1. **Chunk** each `.txt` into ~500-token passages using LangChain's
   `RecursiveCharacterTextSplitter` or similar
2. **Tag** each chunk via LLM using the `llm_topics` field from `metadata.json`
   as seed tags (subjects, difficulty level, key concepts)
3. **Embed** with `text-embedding-3-small` or similar
4. **Ingest** into Pinecone / Chroma / pgvector with metadata filters for
   subject, score, and topic tags

---

## Project Gutenberg terms of use

Both crawlers respect Gutenberg's bulk download policy:

- Use the official catalog CSV (not scraping HTML)
- Download from the `/cache/epub/` CDN path
- Set a polite `User-Agent` header
- Enforce a ≥2 second delay between requests

For runs of 500+ books consider using one of their
[official rsync mirrors](https://www.gutenberg.org/help/mirroring.html)
instead of the CDN.
