"""LLM grader for math_checker — v7 (stage14).

stage14:
- Усиленный промпт по определению варианта (Russian/Azerbaijani).
- НОВОЕ: fallback-вызов detect_variant_focused — если основной grade_student
  вернул detected_variant=null, мы делаем ВТОРОЙ короткий запрос с тем же
  первым скан-листом и очень узким промптом «вернуть только {variant: N}».
  Дешёвый и заметно поднимает recall по варианту (раньше при null работа
  отправлялась на ручную проверку, теперь чаще ловится автоматом).
- Грейдер по-прежнему распознаёт только то, что написано, не «додумывая»
  правильные ответы.
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
    "Do NOT try to identify the student's name — the name is already known from "
    "the file name and you must not return it. "
    "Output JSON ONLY (no markdown fences, no extra text). "
    "Schema: { "
    "\"detected_variant\": integer|null, "
    "\"tasks\": [ { "
    "\"task_number\": integer, "
    "\"page_number\": integer, "
    "\"recognized_answer\": string, "
    "\"confidence\": \"low\"|\"high\", "
    "\"notes\": string, "
    "\"bbox\": {\"x1\": float, \"y1\": float, \"x2\": float, \"y2\": float}|null "
    "} ] }.\n"
    "\n"
    "CRITICAL RULES:\n"
    "1. recognized_answer MUST be EXACTLY what is written on the paper. NEVER "
    "guess, NEVER substitute the student's (possibly wrong) answer with the "
    "answer you think is correct. If you cannot read the answer reliably, "
    "set recognized_answer=\"\" and confidence=\"low\".\n"
    "2. For tasks where the student writes calculations AND a final answer, "
    "return ONLY the final answer in recognized_answer. Strip intermediate "
    "calculations and the word «Ответ:»/«ответ:» itself.\n"
    "3. task_number MUST match the number printed in the «№ N» marker on the page, "
    "NOT just the ordinal position.\n"
    "4. For visual tasks («отметь рисунок»), describe which option the student "
    "marked (e.g. «first picture marked with a tick»).\n"
    "5. detected_variant — the test variant number printed on the page. Tests come "
    "in TWO variants. Look CAREFULLY at the TOP of every page (header area, "
    "right margin, even rotated corner labels). Accept ALL of these forms:\n"
    "      • «Вариант 1» / «Вариант 2» (Russian — most common in ru sector)\n"
    "      • «В-1» / «В-2», «вар. 1», «вар.1», «вар-1»\n"
    "      • «Variant 1» / «Variant 2»\n"
    "      • «Vəriant 1» / «Vəriant 2», «Variantı 1», «1-ci variant», "
    "        «2-ci variant», «I variant», «II variant» (Azerbaijani)\n"
    "      • Sometimes just a Roman numeral «I» or «II» or a digit in a circle/box "
    "        next to the title.\n"
    "   If you see ANY such marker — even a small printed «1» or «2» in the corner —"
    " return the integer (1 or 2). Default to null ONLY if you have actually scanned "
    "every page and found nothing. Do NOT skip variant detection.\n"
    "\n"
    "BBOX RULES (very important — reviewers crop the scan by this rectangle):\n"
    "Coordinates are normalized: (0,0)=top-left, (1,1)=bottom-right. "
    "Include: (a) task header «№ N» + points label; (b) full statement; "
    "(c) any pictures/tables; (d) student's work + «Ответ:» line. Add ~2-3% "
    "padding above the header and below the answer. NEVER overlap the next task."
)


def build_prompt(criteria: dict, pages: list[tuple[int, str]]) -> str:
    lines = [
        f"Grade: {criteria.get('grade')}  Language: {criteria.get('language')}  "
        f"Variant: {criteria.get('variant')}",
        "",
        "Tasks (identify what the student wrote — do not grade). The task_number "
        "listed below is the number we expect to see in the «№ N» marker on the page:",
    ]
    for task in criteria.get("tasks", []):
        task_num = task["task_number"]
        desc = task.get("description", "")
        answer_type = task.get("answer_type", "")
        lines.append(f"\nTask № {task_num} (answer_type={answer_type}): {desc}")

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


def _normalize_bbox(bbox) -> str | None:
    if not isinstance(bbox, dict):
        return None
    try:
        x1 = float(bbox["x1"]); y1 = float(bbox["y1"])
        x2 = float(bbox["x2"]); y2 = float(bbox["y2"])
    except (KeyError, TypeError, ValueError):
        return None
    x1, x2 = max(0.0, min(x1, x2)), min(1.0, max(x1, x2))
    y1, y2 = max(0.0, min(y1, y2)), min(1.0, max(y1, y2))
    if x2 - x1 < 0.01 or y2 - y1 < 0.01:
        return None
    return json.dumps({"x1": x1, "y1": y1, "x2": x2, "y2": y2}, ensure_ascii=False)


def _is_visual_task(task_criteria: dict) -> bool:
    if task_criteria.get("correct_answers"):
        return False
    partial = [
        t for t in task_criteria.get("tiers", [])
        if t.get("label") not in ("full", "zero")
    ]
    return not partial


def detect_variant_focused(first_page_b64: str) -> int | None:
    """Focused second-chance variant detection.

    Если основной grade_student вернул detected_variant=null — вызываем
    эту функцию с первой страницей и узким промптом. Возвращает 1, 2 или None.
    """
    settings = _SETTINGS
    sys_msg = (
        "You are looking for the test VARIANT number on the top of a scanned "
        "math test. The page may show «Вариант 1», «Вариант 2», «В-1», «В-2», "
        "«Variant 1/2», «Vəriant 1/2», «1-ci variant», «I», «II», or just a "
        "small printed digit 1 or 2 in the header / right margin. "
        "Return JSON ONLY in the form {\"variant\": 1} or {\"variant\": 2} or "
        "{\"variant\": null}. Nothing else."
    )
    user_text = (
        "Find the variant number on this scan. Look in the header, the right "
        "margin, any boxed/circled digit. Return JSON only."
    )
    messages = [
        {"role": "system", "content": sys_msg},
        {"role": "user", "content": [
            {"type": "text", "text": user_text},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{first_page_b64}"}},
        ]},
    ]
    try:
        response = litellm.completion(
            model=settings["model"], messages=messages,
            max_tokens=64, temperature=0,
        )
        raw = (response.choices[0].message.content or "").strip()
        m = _FENCE_RE.match(raw)
        if m:
            raw = m.group(1).strip()
        data = json.loads(raw)
        v = data.get("variant")
        if v in (1, 2):
            return int(v)
    except Exception:
        return None
    return None


def grade_student(
    pages: list[tuple[int, str]],
    criteria: dict,
) -> dict:
    settings = _SETTINGS
    prompt_text = build_prompt(criteria, pages)
    image_blocks = [
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
        for _, b64 in pages
    ]
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": [{"type": "text", "text": prompt_text}, *image_blocks]},
    ]
    response = litellm.completion(
        model=settings["model"], messages=messages,
        max_tokens=settings["max_tokens"], temperature=settings["temperature"],
    )
    raw = response.choices[0].message.content
    data = _parse_llm_response(raw)

    # stage14: focused fallback for variant detection.
    if data.get("detected_variant") in (None, 0) and pages:
        v = detect_variant_focused(pages[0][1])
        if v is not None:
            data["detected_variant"] = v

    tasks_criteria = {t["task_number"]: t for t in criteria.get("tasks", [])}
    pages_by_num = {pn: b64 for pn, b64 in pages}

    for task in data.get("tasks", []):
        task_num = task.get("task_number")
        task_criteria = tasks_criteria.get(task_num, {})
        recognized = task.get("recognized_answer", "")
        task["bbox"] = _normalize_bbox(task.get("bbox"))

        visual = _is_visual_task(task_criteria)
        page_b64 = pages_by_num.get(task.get("page_number")) if visual else None

        score, scoring_notes = compute_score(
            recognized, task_criteria, page_image_b64=page_b64
        )
        task["score"] = score
        task["max_score"] = float(task_criteria.get("max_score", 0))

        existing = task.get("grading_notes", "")
        if existing and scoring_notes:
            task["grading_notes"] = f"{existing} | {scoring_notes}"
        elif scoring_notes:
            task["grading_notes"] = scoring_notes

        if visual and (recognized or "").strip():
            task["confidence"] = "low"
            task["grading_notes"] = f"[VISUAL — MANUAL REVIEW] {task['grading_notes']}"

    return data
