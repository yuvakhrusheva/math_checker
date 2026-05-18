# Patterns & Conventions

Coding conventions, development workflow, and project-specific practices.
For universal coding standards, see `~/.claude/skills/code-writing/references/universal-patterns.md`.

---

## Project-Specific Code Patterns

### LLM calls via LiteLLM
All LLM calls go through LiteLLM. Never call provider SDKs (anthropic, openai) directly. Model name comes from `config/settings.json`. This makes provider switching a one-line config change.

### Grading criteria as JSON
Each of the 8 test configurations has its own JSON file in `criteria/`. The file contains: answer key per task, max score, scoring tiers (full / partial / zero), and the conditions for each tier. Grading prompts are constructed dynamically from these files — never hardcode criteria in Python.

### Resumable processing
Before processing a student PDF, always check `students.status` in SQLite. Skip already-processed students. This allows restarting after interruption without re-spending API tokens.

### Folder naming convention for test variant detection
Language can be auto-detected from scan content by the LLM, but grade and variant must be confirmed by the operator in the metadata form. Never infer grade/variant from folder names — too unreliable.

---

## Git Workflow

### Branch Structure

- **`master`** - Stable, tested code. Merge from `dev` only after manual verification.
- **`dev`** - Active development. All work happens here.

### Testing Requirements

- **On commit:** Run unit tests if logic files changed. Skip for config/docs changes.
- **On merge to main:** Full manual verification run on a sample of real scans.

### Security & Quality Gates

- **Pre-commit:** Gitleaks scans for secrets (API keys, tokens). Commit blocked if detected.
- **Pre-push:** Code review agent validates changes.

---

## Testing & Verification

### Test Infrastructure

`pytest` for unit tests. Run with `pytest tests/`.

Key areas to test:
- Grading logic (score calculation from LLM output)
- Criteria JSON parsing
- Excel export structure (correct sheet names, column headers, formulas)

### Agent Verification Methods

**Grading logic**
- Use saved LLM response fixtures (JSON) to test scoring without actual API calls
- Fixtures stored in `tests/fixtures/llm_responses/`

### User Verification Methods

**End-to-end check**
- Run on 5-10 real scans from a known class, compare AI scores to manually graded sample
- Spot-check Excel output for correct student count, no missing scores, correct totals

---

## Business Rules

### Test Configurations
8 variants total: grade (2/3) × language (ru/az) × variant (1/2).
Each maps to a criteria JSON file. Selection is based on operator-entered metadata — never auto-inferred.

### Scoring Tiers (all tasks)
Three tiers only: full credit / partial credit / zero. No other values.
Partial credit conditions are task-type specific (see criteria JSON files).

### Performance Levels (grade 3, 100-point scale)
- 81–100 points → Advanced (Продвинутый)
- 61–80 points → Basic (Базовый)
- 31–60 points → Minimal (Минимальный)
- 0–30 points → Insufficient (Недостаточный)

**Grade 2 performance levels: TBD** — to be defined when grade 2 criteria files are provided. Until then, grade 2 Excel output should include total score but leave the performance level column blank or marked "TBD".

### Excel Output Structure
- **Sheet 1 "Answers":** one row per student, columns = metadata fields + one column per task (recognized answer text)
- **Sheet 2 "Scores":** one row per student, columns = metadata fields + score per task + total score + performance level
- Row order: grouped by school, then by class, then by student filename
