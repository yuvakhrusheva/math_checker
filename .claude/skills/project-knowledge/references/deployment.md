# Deployment & Operations

## Purpose
Deployment process, infrastructure, and production operations for AI agents.

---

## Deployment Platform

**Platform:** Local machine (no cloud deployment)

**Type:** Local Python app, launched via terminal

**Why:** Single operator, minimal infrastructure, no need for hosting or multi-user access

---

## Running the App

```bash
# Install dependencies
pip install -r requirements.txt

# Set up environment variables (copy and fill in)
cp .env.example .env

# Launch
streamlit run app.py
```

App opens automatically at `http://localhost:8501`.

---

## Environment Variables

**See:** [.env.example](../../.env.example) in project root

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | API key for Claude models (if using Anthropic) |
| `OPENAI_API_KEY` | API key for GPT-4o (if using OpenAI) |
| `GEMINI_API_KEY` | API key for Gemini (if using Google) |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Path to Google service account credentials file for Drive API |

Only the API key for the configured LLM provider is required. Set the active model in `config/settings.json`.

---

## Deployment Triggers

No CI/CD. App runs locally on demand. No staging or production environments.

---

## Pre-Run Checklist

- [ ] `.env` file filled with the correct API key for the configured LLM model
- [ ] Google Drive service account has read access to the root folder with scans
- [ ] `criteria/` folder contains JSON files for all test variants being processed
- [ ] `data/` directory exists (created automatically on first run)

---

## Rollback Procedure

If a processing run produces bad results:
1. Delete `data/results.db` to reset all intermediate state
2. Fix the issue (wrong criteria JSON, bad prompt, etc.)
3. Re-run from scratch — resumable processing will skip nothing (fresh DB)

To redo only specific students: set their `status` back to `pending` in SQLite directly.

---

## Environments

**Local only:** `http://localhost:8501` — runs on operator's machine

---

## Monitoring & Observability

### Logging

**Where:** Streamlit UI (progress bar + status messages per student) + stdout
**Format:** Plain text, human-readable

### Error Tracking

**Tool:** None — errors shown inline in Streamlit UI with student filename and error message
**Failed students** saved in SQLite with `status = error` and error message for review

### Health Checks

Not applicable for local tool.
