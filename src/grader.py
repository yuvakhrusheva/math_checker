"""LLM grader for math_checker — двухпроходная архитектура.

Что изменилось по сравнению со старой версией:
- LLM теперь ТОЛЬКО распознаёт что написал ученик (recognized_answer),
  не получая в промпте ни correct_answers, ни tiers. Это решает баг
  "эталонные ответы попадают в Excel вместо ученических".
- score и max_score проставляются ВТОРЫМ ПРОХОДОМ через scorer.compute_score,
  который умеет корректно работать с partial-тирами. Это решает баг
  "при ручной правке балл не пересчитывается с учётом partial".

Поведение grade_student снаружи не изменилось: возвращает тот же словарь
с 'recognized_student_name', 'detected_variant', 'tasks' (с score, max_score,
confidence, grading_notes).
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


# Новый системный промпт: задача LLM — ТОЛЬКО распознавать,
# не оценивать. Эталоны и тиры ему не показываются вовсе.
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
    "recognized_answer to \"\" and confidence to \"low\"."
)


def build_prompt(criteria: dict, pages: list[tuple[int, str]]) -> str:
    """Build text portion of the recognition prompt.

    Внимание: НЕ включает correct_answers и tier conditions — оценивание
    делается отдельно в scorer.py, чтобы LLM не мог "подсмотреть" правильный
    ответ при распознавании.
    """
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

        # Описание задания нужно, чтобы LLM понимал, что искать на странице.
        # Но никаких "Accepted answers" и никаких tiers — это табу.
        lines.append(f"\nTask {task_num} (answer_type={answer_type}): {desc}")

    page_nums = [str(p) for p, _ in pages]
    lines += [
        "",
        f"The test has {len(pages)} page(s) (pages: {', '.join(page_nums)}).",
        "Return ONLY valid JSON matching the schema described in the system prompt.",
    ]

    return "\n".join(lines)


def _parse_llm_response(raw: str) -> dict:
    """Parse raw LLM text. Renames 'notes' → 'grading_notes'."""
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


def grade_student(
    pages: list[tuple[int, str]],
    criteria: dict,
) -> dict:
    """Call vision LLM to extract answers, then locally score each task via scorer.

    Args:
        pages:    List of (page_number, base64_jpeg) tuples from pdf_processor.
        criteria: Loaded criteria dict for this student's grade/language/variant.

    Returns:
        Dict с полями 'recognized_student_name', 'detected_variant', 'tasks'.
        Каждый task содержит 'task_number', 'page_number', 'recognized_answer',
        'confidence', 'score', 'max_score', 'grading_notes'.
    """
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

    # ВТОРОЙ ПРОХОД: каждой задаче проставляем score через scorer.
    # Если в исходном ответе LLM был score (старая совместимость) — он будет
    # перезаписан правильным значением.
    tasks_criteria = {t["task_number"]: t for t in criteria.get("tasks", [])}
    for task in data.get("tasks", []):
        task_num = task.get("task_number")
        task_criteria = tasks_criteria.get(task_num, {})
        recognized = task.get("recognized_answer", "")
        score, scoring_notes = compute_score(recognized, task_criteria)
        task["score"] = score
        task["max_score"] = float(task_criteria.get("max_score", 0))
        # Добавляем причину оценки в grading_notes, не теряя оригинальную заметку.
        existing = task.get("grading_notes", "")
        if existing and scoring_notes:
            task["grading_notes"] = f"{existing} | {scoring_notes}"
        elif scoring_notes:
            task["grading_notes"] = scoring_notes

    return data
