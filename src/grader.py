"""LLM grader for math_checker — двухпроходная архитектура + bbox.

Этот файл — обновлённая версия предыдущего фикса (двухпроходного grader/scorer).
Дополнительно: LLM теперь возвращает bbox каждого задания на странице,
чтобы Review Panel мог показать обрезанный кусок страницы, а не всю.

Bbox — это нормализованные координаты от 0 до 1:
{ "x1": 0.05, "y1": 0.20, "x2": 0.95, "y2": 0.40 }
где (0,0) — верхний левый угол страницы, (1,1) — правый нижний.

Если LLM не смог определить координаты — возвращает null, и Review Panel
покажет всю страницу как раньше (старое поведение, fallback).
"""
import json
import re
from pathlib import Path

import litellm

from src.scorer import compute_score

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
    "\"notes\": string, "
    "\"bbox\": {\"x1\": float, \"y1\": float, \"x2\": float, \"y2\": float}|null "
    "} ] }. "
    "CRITICAL: recognized_answer MUST be exactly what is written on the paper, "
    "even if it looks wrong or unusual. Never guess, never substitute a more "
    "plausible answer. If you cannot read the answer reliably, set "
    "recognized_answer to \"\" and confidence to \"low\". "
    "BBOX: For each task, also return a 'bbox' field with NORMALIZED coordinates "
    "of the rectangle that contains the entire task on the page — both the question "
    "text/image AND the student's answer area. Values are decimals between 0 and 1 "
    "where (0,0) is the top-left corner of the page and (1,1) is the bottom-right. "
    "Give a generous bbox that includes a little padding around the task. "
    "If you cannot reliably determine the bbox, return null for that task's bbox."
)


def build_prompt(criteria: dict, pages: list[tuple[int, str]]) -> str:
    """Build text portion of the recognition prompt.

    НЕ включает correct_answers и tier conditions — оценивание делается отдельно
    в scorer.py, чтобы LLM не мог "подсмотреть" правильный ответ при распознавании.
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
        lines.append(f"\nTask {task_num} (answer_type={answer_type}): {desc}")

    page_nums = [str(p) for p, _ in pages]
    lines += [
        "",
        f"The test has {len(pages)} page(s) (pages: {', '.join(page_nums)}).",
        "Return ONLY valid JSON matching the schema described in the system prompt.",
    ]

    return "\n".join(lines)


def _parse_llm_response(raw: str) -> dict:
    """Parse raw LLM text. Renames 'notes' → 'grading_notes'.

    Поле bbox оставляем как есть — его обработает grade_student.
    """
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


def _normalize_bbox(bbox) -> str | None:
    """Превратить bbox dict в JSON-строку для хранения в БД.

    Возвращает None, если bbox отсутствует, неверного формата, или координаты
    выходят за пределы [0,1].
    """
    if not isinstance(bbox, dict):
        return None
    try:
        x1 = float(bbox["x1"])
        y1 = float(bbox["y1"])
        x2 = float(bbox["x2"])
        y2 = float(bbox["y2"])
    except (KeyError, TypeError, ValueError):
        return None

    # Зажимаем в [0, 1] и проверяем, что x1<x2, y1<y2
    x1, x2 = max(0.0, min(x1, x2)), min(1.0, max(x1, x2))
    y1, y2 = max(0.0, min(y1, y2)), min(1.0, max(y1, y2))
    if x2 - x1 < 0.01 or y2 - y1 < 0.01:
        # Слишком маленький bbox — игнорируем
        return None

    return json.dumps({"x1": x1, "y1": y1, "x2": x2, "y2": y2}, ensure_ascii=False)


def grade_student(
    pages: list[tuple[int, str]],
    criteria: dict,
) -> dict:
    """Call vision LLM to extract answers + bbox, then locally score each task."""
    settings = _SETTINGS
    prompt_text = build_prompt(criteria, pages)

    image_blocks = [
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
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

    # ВТОРОЙ ПРОХОД: каждой задаче проставляем score через scorer
    # + нормализуем bbox в JSON-строку для БД.
    tasks_criteria = {t["task_number"]: t for t in criteria.get("tasks", [])}
    for task in data.get("tasks", []):
        task_num = task.get("task_number")
        task_criteria = tasks_criteria.get(task_num, {})
        recognized = task.get("recognized_answer", "")
        score, scoring_notes = compute_score(recognized, task_criteria)
        task["score"] = score
        task["max_score"] = float(task_criteria.get("max_score", 0))
        existing = task.get("grading_notes", "")
        if existing and scoring_notes:
            task["grading_notes"] = f"{existing} | {scoring_notes}"
        elif scoring_notes:
            task["grading_notes"] = scoring_notes

        # bbox: dict из ответа → JSON-строка для БД (или None)
        task["bbox"] = _normalize_bbox(task.get("bbox"))

    return data
