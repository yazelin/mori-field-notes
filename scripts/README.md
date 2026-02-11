# Scripts

Daily automation scripts for Mori's Field Notes.

## Scripts

| Script | Description |
|---|---|
| `daily-update.sh` | Orchestrator — runs the four steps below in order, logs output to `logs/YYYY-MM-DD.log`, and exits non-zero on any failure. |
| `collect-materials.py` | Scans GitHub Trending, tech news feeds, and dev updates to gather raw material. |
| `write-note.py` | Selects a topic from collected materials and drafts a 200-500 word note. |
| `generate-image.py` | Generates an AI illustration for the note using Gemini AI. |
| `publish.py` | Commits the note and image to the repo, updates `state.json` and `docs/notes.json`. |

## Usage

```bash
# Run full pipeline for today
./scripts/daily-update.sh

# Run for a specific date
./scripts/daily-update.sh 2026-03-15
```

Each Python script also accepts `--date YYYY-MM-DD` independently:

```bash
python3 scripts/collect-materials.py --date 2026-03-15
```

## Dependencies

- Python 3.10+
- Packages listed in `requirements.txt` (if present)
- `GEMINI_API_KEY` environment variable for image generation
- `GITHUB_TOKEN` environment variable for publish step
