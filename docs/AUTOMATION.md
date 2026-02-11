# Automation

Mori's Field Notes uses **OpenClaw** cron jobs to run the daily pipeline automatically.

## OpenClaw Cron Job Configuration

| Parameter | Value |
|---|---|
| `sessionTarget` | `yazelin/mori-field-notes` |
| `payload.kind` | `daily-update` |
| `schedule` | `0 8 * * *` (every day at 08:00) |
| `tz` | `Asia/Taipei` |

The cron job triggers `scripts/daily-update.sh`, which sequentially executes material collection, note writing, image generation, and publishing.

## Testing with a Manual Run

1. **Trigger manually from the OpenClaw dashboard:**
   - Navigate to the cron job entry for `mori-field-notes`.
   - Click **Run now** to dispatch immediately.

2. **Trigger from the command line:**
   ```bash
   # Run locally for today
   ./scripts/daily-update.sh

   # Run locally for a specific date
   ./scripts/daily-update.sh 2026-03-15
   ```

3. **Verify the run:**
   - Check `logs/YYYY-MM-DD.log` for step-by-step output.
   - Confirm `state.json` was updated with the new publish date.
   - Confirm `docs/notes.json` contains the new note entry.
   - Visit the GitHub Pages site to see the published note.

## Troubleshooting

- If a step fails, the log file records the error and the pipeline exits immediately.
- Re-run with the same `--date` to retry; scripts are idempotent.
- Ensure `GEMINI_API_KEY` and `GITHUB_TOKEN` secrets are configured in the OpenClaw environment.
