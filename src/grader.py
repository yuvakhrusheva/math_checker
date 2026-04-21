"""LLM grader for math_checker.

Orchestrates vision LLM calls via LiteLLM, parses structured JSON responses,
and delegates per-task scoring to scorer.py.

Key responsibilities:
- build_prompt: format criteria + task descriptions into the text part of the prompt
- _parse_llm_response: parse raw LLM JSON, rename 'notes' → 'grading_notes'
- grade_student: assemble multi-image LiteLLM call, return structured result dict
"""
import json
import os
from pathlib import Path

import litellm

from src.scorer import compute_score

_SETTINGS_PATH = Path(__file__).parent.parent / "config" / "settings.json"

def _load_settings() -> dict:
    return json.loads(_SETTINGS_PATH.read_text(encoding="utf-8"))


_SYSTEM_PROMPT = (
    "You are a math grader for Azerbaijani school students (grades 2-3). "
    "Read the handwritten math test shown in the images. "
    "For each task, identify the student's answer, compare it to the given criteria, "
    "and return a JSON response ONLY (no additional text, no markdown fences). "
    "The JSON must contain: recognized_student_name, detected_variant (integer or null), "
    "and tasks array with fields: task_number, page_number, recognized_answer, "
    "score, max_score, confidence (low|high), notes."
)


def build_prompt(criteria: dict, pages: list[tuple[int, str]]) -> str:
    """
    Build the text portion of the grading prompt.

    Args:
        criteria: Parsed criteria dict (from criteria_loader).
        pages:    List of (page_number, base64_jpeg) tuples.

    Returns:
        Prompt string containing task descriptions and grading instructions.
    """
    lines = [
        f"Grade: {criteria.get('grade')}  Language: {criteria.get('language')}  "
        f"Variant: {criteria.get('variant')}",
        f"Total max score: {criteria.get('total_max_score', '?')}",
        "",
        "Tasks to grade:",
    ]

    for task in criteria.get("tasks", []):
        task_num = task["task_number"]
        desc = task.get("description", "")
        max_sc = task.get("max_score", 0)
        answer_type = task.get("answer_type", "")
        correct = task.get("correct_answers", [])
        tiers = task.get("tiers", [])

        lines.append(f"\nTask {task_num} (max {max_sc} pts, type={answer_type}): {desc}")
        if correct:
            lines.append(f"  Accepted answers: {', '.join(correct)}")
        for tier in tiers:
            lines.append(
                f"  [{tier.get('label', '?')}] {tier.get('score', 0)} pts — {tier.get('condition', '')}"
            )

    page_nums = [str(p) for p, _ in pages]
    lines += [
        "",
        f"The test has {len(pages)} page(s) (pages: {', '.join(page_nums)}).",
        "Return ONLY valid JSON matching the schema described in the system prompt.",
    ]

    return "\n".join(lines)


def _parse_llm_response(raw: str) -> dict:
    """
    Parse the raw LLM text response.

    Renames the 'notes' field in each task to 'grading_notes' (DB column name).
    Raises json.JSONDecodeError if the response is not valid JSON.
    """
    data = json.loads(raw)  # raises JSONDecodeError if not valid JSON

    for task in data.get("tasks", []):
        # Rename notes → grading_notes
        if "notes" in task:
            task["grading_notes"] = task.pop("notes")
        elif "grading_notes" not in task:
            task["grading_notes"] = ""

    return data


def grade_student(
    pages: list[tuple[int, str]],
    criteria: dict,
) -> dict:
    """
    Call the vision LLM to grade a student's test and return a structured result.

    Args:
        pages:    List of (page_number, base64_jpeg) tuples from pdf_processor.
        criteria: Loaded criteria dict for this student's grade/language/variant.

    Returns:
        Parsed response dict with 'recognized_student_name', 'detected_variant',
        and 'tasks' list. Each task has 'grading_notes' (renamed from LLM 'notes').

    Raises:
        json.JSONDecodeError: If the LLM returns non-JSON output.
        Any litellm exception: propagated to caller (queue_processor handles it).
    """
    settings = _load_settings()
    prompt_text = build_prompt(criteria, pages)

    # Build image content blocks
    image_blocks = [
        {
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
        }
        for _, b64 in pages
    ]

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt_text},
                *image_blocks,
            ],
        },
    ]

    response = litellm.completion(
        model=settings["model"],
        messages=messages,
        max_tokens=settings["max_tokens"],
        temperature=settings["temperature"],
    )

    raw = response.choices[0].message.content
    return _parse_llm_response(raw)
