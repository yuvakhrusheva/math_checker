# Project Context

## Purpose
This file provides high-level project overview for AI agents. Helps agents understand WHAT we're building and WHY.

---

## Project Overview

**Name:** math_checker

**Description:** Local tool for AI-powered grading of handwritten math monitoring tests for 2nd and 3rd grade students in Azerbaijan.

The tool takes scanned PDF worksheets uploaded to Google Drive, recognizes students' handwritten answers using a vision LLM, grades them against predefined criteria, and exports results to Excel for analyst use.

---

## Target Audience

**Primary users:** Project coordinator (single operator) running the grading process after tests are collected.

**Use case:** After a large-scale math monitoring session (~1200 students across 2 grades), the coordinator needs to grade all worksheets without doing it manually. They run this tool locally, assign metadata to each batch of scans, and receive a structured Excel file ready for analysis.

---

## Core Problem

Grading 1200 handwritten math worksheets manually is slow, labor-intensive, and inconsistent. Each worksheet has 16 tasks with multi-level scoring criteria (0 / partial credit / full credit), making standardization hard. The tool automates recognition and scoring so the coordinator gets structured per-student results in one session, and analysts can immediately work with the data.

---

## Key Features

- **Google Drive folder intake** - App traverses a root Google Drive folder, finds all leaf folders with scans, and presents them for metadata entry
- **Metadata assignment** - Operator fills per-folder metadata: school, teacher, class number and letter, project participation flag, language (auto-detected or manual), test date
- **AI recognition and grading** - Each PDF page is converted to an image and sent to a vision LLM (via LiteLLM) with task-specific grading prompts; the model returns recognized answers and scores per task
- **8 test configurations** - Supports all combinations: grade 2/3 × Russian/Azerbaijani sector × Variant 1/2 — each with its own answer key and scoring criteria loaded from JSON
- **Excel export** - Produces a two-sheet Excel file: recognized answers per student and scores per task with totals and performance levels

---

## Out of Scope

- No web deployment or multi-user access — runs locally only
- No teacher- or student-facing interfaces
- No real-time dashboard or live reporting
- No direct integration with school information systems
- No automatic scan quality enhancement or deskewing
- No support for other subjects or grade levels (at this stage)
