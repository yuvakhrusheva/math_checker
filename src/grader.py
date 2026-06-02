"""LLM grader for math_checker — v8 (stage15).

stage15 — ОТКАТ stage14 в части определения варианта:
- Убран focused-fallback (detect_variant_focused). Эта функция делала
  второй LLM-вызов с узким промптом, но на практике путала модель и
  ломала распознавание ответов.
- Промпт варианта возвращён к КОРОТКОЙ форме (одна строка). Раньше
  модель видела огромный блок с 5+ форматами — это её перегружало и
  «съедало» внимание у распознавания ответов.
- Остальное (bbox, запрет галлюцинаций, извлечение «Ответ:», визуальные
  задачи) — всё на месте, как в stage14.
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
    "answer you think is correct. Example: if the task is «найди уменьшаемое: "
    "X - 18 = 45» (correct answer 63) and the student wrote «27», you MUST "
    "return recognized_answer=\"27\". DO NOT return \"63\". If you cannot read "
    "the answer reliably, set recognized_answer=\"\" and confidence=\"low\".\n"
    "2. For tasks where the student writes calculations AND a final answer, "
    "return ONLY the final answer in recognized_answer. Strip intermediate "
    "calculations and the word «Ответ:»/«ответ:» itself.\n"
    "3. task_number MUST match the number printed in the «№ N» marker on the "
    "page, NOT the ordinal position.\n"
    "4. For visual tasks («отметь рисунок»), describe which option the student "
    "marked (e.g. «first picture marked with a tick»).\n"
    "5. detected_variant — the test variant number printed on the page header "
    "(usually «Вариант 1» / «Вариант 2», or «I» / «II», or «1» / «2»). Return "
    "1 or 2 if you see it, null otherwise.\n"
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

    # stage15: focused-fallback по варианту убран — он ломал распознавание.

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
