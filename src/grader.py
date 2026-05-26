"""LLM grader for math_checker — двухпроходная архитектура (v4).

Что изменилось по сравнению с v3 (fixes4):
- Визуальные задачи (correct_answers=[] и только full+zero) ПРИНУДИТЕЛЬНО
  получают confidence="low" после Pass 2. Это нужно, чтобы такие задачи
  гарантированно попадали в Review Panel — куратор должен вручную
  перепроверить vision-оценку, как бы уверенно ни выглядел Pass 1.
- В grading_notes для визуальных задач добавляется префикс
  "[VISUAL — MANUAL REVIEW]", чтобы в Excel/Review Panel это сразу
  бросалось в глаза.

Поведение для НЕвизуальных задач не изменилось.
"""
import json
import re
from pathlib import Path

import litellm

from src.scorer import compute_score

# Claude и некоторые другие модели иногда оборачивают JSON в markdown-фенсы.
_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?```\s*$", re.DOTALL)

_SETTINGS_PATH = Path(__file__).parent.parent / "config" / "settings.json"


def _load_settings() -> dict:
    return json.loads(_SETTINGS_PATH.read_text(encoding="utf-8"))


_SETTINGS: dict = _load_settings()


_SYSTEM_PROMPT = (
    "You read handwritten math tests from Azerbaijani school students (grades 2-3). "
    "Your ONLY job is to identify exactly what each student wrote — do NOT grade, "
    "do NOT judge correctness, do NOT compare to expected answers. "
    "Output JSON ONLY (no markdown fences, no extra text). "
    "Schema: { "
    "\"recognized_student_name\": string|null, "
    "\"detected_variant\": integer|null, "
    "\"tasks\": [ { "
    "\"task_number\": integer, "
    "\"page_number\": integer, "
    "\"recognized_answer\": string, "
    "\"confidence\": \"low\"|\"high\", "
    "\"notes\": string "
    "} ] }. "
    "CRITICAL: recognized_answer MUST be exactly what is written on the paper, "
    "even if it looks wrong or unusual. Never guess, never substitute a more "
    "plausible answer. If you cannot read the answer reliably, set "
    "recognized_answer to \"\" and confidence to \"low\". "
    "For visual tasks (e.g. \"mark the picture\"), describe which option the "
    "student marked (e.g. \"first picture marked with a tick\", \"second box "
    "ticked\") — the page image will be re-shown to a separate scorer."
)


def build_prompt(criteria: dict, pages: list[tuple[int, str]]) -> str:
    lines = [
        f"Grade: {criteria.get('grade')}  Language: {criteria.get('language')}  "
        f"Variant: {criteria.get('variant')}",
        "",
        "Tasks (identify what the student wrote — do not grade):",
    ]

    for task in criteria.get("tasks", []):
        task_num = task["task_number"]
        desc = task.get("description", "")
        answer_type = task.get("answer_type", "")
        lines.append(f"\nTask {task_num} (answer_type={answer_type}): {desc}")

    page_nums = [str(p) for p, _ in pages]
    lines += [
        "",
        f"The test has {len(pages)} page(s) (pages: {', '.join(page_nums)}).",
        "Return ONLY valid JSON matching the schema described in the system prompt.",
    ]

    return "\n".join(lines)


def _parse_llm_response(raw: str) -> dict:
    text = (raw or "").strip()
    m = _FENCE_RE.match(text)
    if m:
        text = m.group(1).strip()
    data = json.loads(text)

    for task in data.get("tasks", []):
        if "notes" in task:
            task["grading_notes"] = task.pop("notes")
        elif "grading_notes" not in task:
            task["grading_notes"] = ""

    return data


def _is_visual_task(task_criteria: dict) -> bool:
    """Визуальная задача: пустой correct_answers и нет partial-тиров."""
    if task_criteria.get("correct_answers"):
        return False
    partial = [
        t for t in task_criteria.get("tiers", [])
        if t.get("label") not in ("full", "zero")
    ]
    return not partial


def grade_student(
    pages: list[tuple[int, str]],
    criteria: dict,
) -> dict:
    """Call vision LLM to extract answers, then locally score each task via scorer."""
    settings = _SETTINGS
    prompt_text = build_prompt(criteria, pages)

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
    data = _parse_llm_response(raw)

    # ВТОРОЙ ПРОХОД
    tasks_criteria = {t["task_number"]: t for t in criteria.get("tasks", [])}
    pages_by_num = {pn: b64 for pn, b64 in pages}

    for task in data.get("tasks", []):
        task_num = task.get("task_number")
        task_criteria = tasks_criteria.get(task_num, {})
        recognized = task.get("recognized_answer", "")

        visual = _is_visual_task(task_criteria)
        page_b64 = pages_by_num.get(task.get("page_number")) if visual else None

        score, scoring_notes = compute_score(
            recognized, task_criteria, page_image_b64=page_b64
        )
        task["score"] = score
        task["max_score"] = float(task_criteria.get("max_score", 0))

        # Склейка grading_notes
        existing = task.get("grading_notes", "")
        if existing and scoring_notes:
            task["grading_notes"] = f"{existing} | {scoring_notes}"
        elif scoring_notes:
            task["grading_notes"] = scoring_notes

        # Для визуальных задач — принудительно low confidence и заметный префикс,
        # чтобы куратор обязательно их перепроверил в Review Panel.
        if visual:
            task["confidence"] = "low"
            task["grading_notes"] = f"[VISUAL — MANUAL REVIEW] {task['grading_notes']}"

    return data
