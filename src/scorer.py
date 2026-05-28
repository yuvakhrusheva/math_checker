"""Scoring engine for math_checker — двухпроходная архитектура (v3).

Что изменилось по сравнению с предыдущей версией:
- УСИЛЕНА нормализация в `_normalize`: схлопывает пробелы, унифицирует
  тире/минусы (— – − → -), убирает конечные точки, унифицирует запятую
  и точку как десятичный разделитель, извлекает итоговое число из
  записи вида "<выражение> = <число>" (типичный кейс: ученик написал
  "24 + 37 = 61" вместо просто "61").
- ДОБАВЛЕН LLM-fallback для задач с тирами только `full + zero`:
  если строгая нормализация не дала совпадения, вместо моментального
  zero — вызывается LLM (текстовый), который решает, эквивалентен ли
  ответ ученика эталону по смыслу/численно. Это исправляет кейсы вида
  «5 ман.» vs «5 ман», «47<74» vs «47 < 74», и т.п.
- ДОБАВЛЕН vision-fallback для визуальных задач: если `correct_answers`
  пуст и нет partial-тиров (типичная визуальная задача «отметь рисунок»),
  и в scorer передана картинка страницы — делается vision-LLM-вызов,
  который смотрит на скан и сам решает full/zero по condition.
- compute_score теперь принимает опциональный `page_image_b64` для
  vision-fallback. Старая сигнатура (без него) сохранена для совместимости.

LLM-вызов делается через litellm с настройками из config/settings.json.
"""
import json
import re
from pathlib import Path

import litellm

_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?```\s*$", re.DOTALL)
_SETTINGS_PATH = Path(__file__).parent.parent / "config" / "settings.json"

# ---------- нормализация ----------

# Все «длинные» дефисы/минусы, которые встречаются в сканах и LLM-выходе.
_DASH_CHARS = "‐‑‒–—―−"  # ‐ ‑ ‒ – — ― −
_DASH_TRANS = str.maketrans({c: "-" for c in _DASH_CHARS})

# Извлекаем "хвост" после последнего знака равенства: "24 + 37 = 61" → "61".
_EQ_TAIL_RE = re.compile(r"=\s*([^=]+)\s*$")

# Извлекаем "хвост" после слова "Ответ:" / "ответ:" / "Ответ —" и т.п.
# Это нужно, когда LLM записал и вычисления, и финальный ответ одной строкой.
_OTVET_TAIL_RE = re.compile(
    r"(?:^|[^\w])[оО][тТ][вВ][еЕ][тТ]\s*[:\-—–]\s*(.+?)\s*$"
)

# Чисто числовое содержимое (после нормализации) — для типа numeric.
_NUM_RE = re.compile(r"^-?\d+([.,]\d+)?$")


def _load_settings() -> dict:
    return json.loads(_SETTINGS_PATH.read_text(encoding="utf-8"))


_SETTINGS: dict = _load_settings()


def compute_score(
    recognized_answer: str | None,
    task_criteria: dict,
    page_image_b64: str | None = None,
) -> tuple[float, str]:
    """Compute score for one task.

    Стратегия:
    1. Пустой ответ → 0 баллов (без LLM).
    2. Если correct_answers пуст И есть partial-тиры — спрашиваем LLM по тирам.
    3. Если correct_answers пуст И только full+zero — это визуальная задача:
       если есть page_image_b64 → vision-LLM выбирает full/zero по condition;
       иначе → 0 с заметкой.
    4. Точное совпадение с correct_answers (после нормализации) → full tier.
    5. Если есть partial-тиры — спрашиваем LLM, какой тир подходит.
    6. Только full+zero, нет точного match — LLM-fallback (text-only): спросить,
       эквивалентен ли ответ. Если да → full, иначе → zero.

    Args:
        recognized_answer: что распознал grader.
        task_criteria:    словарь критериев из criteria/*.json для одной задачи.
        page_image_b64:   base64-jpeg страницы с этим заданием. Нужен только для
                          визуальных задач (correct_answers=[] + только full/zero).

    Returns (score, grading_notes).
    """
    answer = (recognized_answer or "").strip()
    if not answer or _looks_like_empty_marker(answer):
        return 0.0, "No answer provided"

    tiers: list[dict] = task_criteria.get("tiers", [])
    correct_answers: list[str] = task_criteria.get("correct_answers", [])
    answer_type: str = task_criteria.get("answer_type", "text")
    max_score: float = float(task_criteria.get("max_score", 0))

    partial_tiers = [t for t in tiers if t.get("label") not in ("full", "zero")]

    # --- Визуальные задачи: correct_answers пуст ---
    if not correct_answers:
        if partial_tiers:
            # Есть partial — обычная LLM-классификация по тирам.
            return _score_via_tier_classifier(answer, task_criteria, tiers)

        # Только full + zero. Если есть скан страницы — делаем vision-проверку.
        if page_image_b64:
            return _score_via_vision(answer, task_criteria, tiers, page_image_b64)

        # Нет ни эталона, ни картинки — ничего не можем сказать.
        zero_score = _zero_tier_score(tiers)
        return zero_score, (
            "Visual task with no correct_answers and no page image available; "
            "defaulted to zero"
        )

    # --- Обычные задачи: есть correct_answers ---

    # Шаг 4: точное совпадение после нормализации
    matched = _matches(answer, correct_answers, answer_type)
    if matched:
        full_tiers = [t for t in tiers if t.get("label") == "full"]
        if full_tiers:
            return (
                float(full_tiers[0]["score"]),
                full_tiers[0].get("condition", "Correct answer"),
            )
        return max_score, "Correct answer"

    # Шаг 5: есть partial-тиры — спрашиваем LLM
    if partial_tiers:
        return _score_via_tier_classifier(answer, task_criteria, tiers)

    # Шаг 6: только full+zero + нет точного match → LLM-fallback на эквивалентность
    try:
        equivalent = _check_equivalence_via_llm(answer, correct_answers, task_criteria)
    except Exception as exc:
        zero_score = _zero_tier_score(tiers)
        return zero_score, (
            f"Equivalence check failed ({type(exc).__name__}); defaulted to zero"
        )

    if equivalent:
        full_tiers = [t for t in tiers if t.get("label") == "full"]
        if full_tiers:
            return (
                float(full_tiers[0]["score"]),
                "Semantic match via LLM equivalence check",
            )
        return max_score, "Semantic match via LLM equivalence check"

    return _zero_tier_score(tiers), "Incorrect answer (no semantic equivalence)"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _zero_tier_score(tiers: list[dict]) -> float:
    """Return score for the zero tier (or 0 if no zero tier defined)."""
    zero_tiers = [t for t in tiers if t.get("label") == "zero"]
    if zero_tiers:
        return float(zero_tiers[0]["score"])
    return 0.0


def _score_via_tier_classifier(
    answer: str, task_criteria: dict, tiers: list[dict]
) -> tuple[float, str]:
    """Старый путь: спросить LLM, какой тир подходит. Возвращает (score, notes)."""
    try:
        label = _classify_tier_via_llm(answer, task_criteria)
    except Exception as exc:
        return _zero_tier_score(tiers), (
            f"LLM classifier failed ({type(exc).__name__}); defaulted to zero"
        )

    chosen = next((t for t in tiers if t.get("label") == label), None)
    if chosen is None:
        return _zero_tier_score(tiers), (
            f"LLM returned unknown label {label!r}; defaulted to zero"
        )
    return float(chosen["score"]), chosen.get("condition", f"Tier: {label}")


def _score_via_vision(
    answer: str,
    task_criteria: dict,
    tiers: list[dict],
    page_image_b64: str,
) -> tuple[float, str]:
    """Визуальная задача: показать LLM скан и спросить full/zero по condition."""
    full_tier = next((t for t in tiers if t.get("label") == "full"), None)
    if full_tier is None:
        return _zero_tier_score(tiers), "Visual task: no full tier defined"

    description = task_criteria.get("description", "")
    full_condition = full_tier.get("condition", "")
    task_num = task_criteria.get("task_number", "?")

    prompt = (
        f"Visual scoring for math task {task_num}.\n"
        f"Task: {description}\n\n"
        f"The student wrote / marked: \"{answer}\"\n\n"
        f"Full-credit condition: {full_condition}\n\n"
        f"Look at the scanned page and decide whether the student's mark/answer "
        f"satisfies the full-credit condition. Be strict but fair: if the marked "
        f"choice (or drawing) clearly satisfies the condition, return full; "
        f"otherwise return zero.\n"
        f"Return ONLY valid JSON (no markdown fences):\n"
        f"{{\"label\": \"full\" | \"zero\", \"reason\": \"<short explanation>\"}}"
    )

    try:
        response = litellm.completion(
            model=_SETTINGS["model"],
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a strict but fair math test grader for "
                        "Azerbaijani primary school. Output JSON only."
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{page_image_b64}"
                            },
                        },
                    ],
                },
            ],
            max_tokens=200,
            temperature=0,
        )
    except Exception as exc:
        return _zero_tier_score(tiers), (
            f"Vision check failed ({type(exc).__name__}); defaulted to zero"
        )

    raw = (response.choices[0].message.content or "").strip()
    m = _FENCE_RE.match(raw)
    if m:
        raw = m.group(1).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return _zero_tier_score(tiers), (
            "Vision check: could not parse LLM response; defaulted to zero"
        )

    label = str(data.get("label", "")).strip().lower()
    reason = str(data.get("reason", "")).strip()

    chosen = next((t for t in tiers if t.get("label") == label), None)
    if chosen is None:
        return _zero_tier_score(tiers), (
            f"Vision check returned unknown label {label!r}; defaulted to zero"
        )
    notes = chosen.get("condition", f"Tier: {label}")
    if reason:
        notes = f"{notes} | Vision: {reason}"
    return float(chosen["score"]), notes


def _check_equivalence_via_llm(
    answer: str, correct_answers: list[str], task_criteria: dict
) -> bool:
    """Спросить LLM, эквивалентен ли ответ ученика хотя бы одному эталону.

    Возвращает True/False. Сюда мы попадаем только если строгая нормализация
    уже не совпала, поэтому LLM смотрит на смысловую/численную эквивалентность.
    """
    task_num = task_criteria.get("task_number", "?")
    description = task_criteria.get("description", "")
    answer_type = task_criteria.get("answer_type", "text")
    accepted = "\n".join(f"  - {a!r}" for a in correct_answers)

    prompt = (
        f"You are grading one math test answer for task {task_num} "
        f"({description}).\n\n"
        f"Accepted correct answers (any of these is full credit):\n{accepted}\n\n"
        f"Student wrote: \"{answer}\"\n\n"
        f"Decide whether the student's answer is EQUIVALENT to one of the "
        f"accepted answers. Equivalence rules:\n"
        f"- Numeric answers: the final value matches (ignore intermediate work, "
        f"e.g. \"24 + 37 = 61\" is equivalent to \"61\"; \"5\" is equivalent "
        f"to \"5 ман\" if accepted forms include the bare number).\n"
        f"- Units: missing or extra units of measurement are acceptable when "
        f"the numeric value is the same and the task clearly implies the unit.\n"
        f"- Equations: \"54 - 9 = 45\" is equivalent to \"45\" only if 45 is "
        f"explicitly an accepted answer; otherwise require the full expression.\n"
        f"- Spacing/punctuation: differences in spaces, commas vs spaces as "
        f"separators, or trailing dots do NOT change correctness.\n"
        f"- Text: case-insensitive, ignore extra whitespace.\n"
        f"- Do NOT accept answers that are merely close (e.g. 60 instead of 61).\n"
        f"- Answer type for this task: {answer_type}\n\n"
        f"Return ONLY valid JSON (no markdown fences):\n"
        f"{{\"equivalent\": true | false, \"reason\": \"<short explanation>\"}}"
    )

    response = litellm.completion(
        model=_SETTINGS["model"],
        messages=[
            {
                "role": "system",
                "content": (
                    "You decide whether a student's math answer is equivalent "
                    "to an accepted answer. Output JSON only."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        max_tokens=200,
        temperature=0,
    )

    raw = (response.choices[0].message.content or "").strip()
    m = _FENCE_RE.match(raw)
    if m:
        raw = m.group(1).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return False
    return bool(data.get("equivalent", False))


def _classify_tier_via_llm(answer: str, task_criteria: dict) -> str:
    """Ask the LLM which tier label fits this answer best.

    Returns one of the tier labels from task_criteria (e.g. "full", "partial", "zero").
    """
    task_num = task_criteria.get("task_number", "?")
    description = task_criteria.get("description", "")
    tiers = task_criteria.get("tiers", [])
    labels = [t.get("label", "") for t in tiers if t.get("label")]

    tier_lines = "\n".join(
        f"- \"{t.get('label')}\" ({t.get('score')} pts): {t.get('condition')}"
        for t in tiers
    )

    prompt = (
        f"You are classifying a math test answer into one scoring tier.\n"
        f"Task {task_num}: {description}\n\n"
        f"The student wrote: \"{answer}\"\n\n"
        f"Available scoring tiers:\n{tier_lines}\n\n"
        f"Pick the tier label that best fits the student's answer based on the "
        f"conditions above. Treat equivalent forms as a match (e.g. \"24+37=61\" "
        f"is equivalent to \"61\"; \"5 ман\" is equivalent to \"5 манат\"; "
        f"differences in spaces/punctuation/case do not change correctness). "
        f"Be strict — only award higher tiers when the answer truly satisfies "
        f"the condition.\n"
        f"Return ONLY valid JSON in this exact form (no markdown fences):\n"
        f"{{\"label\": \"<one of: {', '.join(labels)}>\"}}"
    )

    response = litellm.completion(
        model=_SETTINGS["model"],
        messages=[
            {
                "role": "system",
                "content": "You classify student answers into scoring tiers. Output JSON only.",
            },
            {"role": "user", "content": prompt},
        ],
        max_tokens=100,
        temperature=0,
    )

    raw = (response.choices[0].message.content or "").strip()
    m = _FENCE_RE.match(raw)
    if m:
        raw = m.group(1).strip()
    try:
        data = json.loads(raw)
        return str(data.get("label", "")).strip()
    except json.JSONDecodeError:
        return ""


def _matches(answer: str, correct_answers: list[str], answer_type: str) -> bool:
    """Return True if the answer matches any of the accepted correct answers.

    Сравниваем несколько вариантов:
    1) Целиком (после нормализации).
    2) Хвост после слова «Ответ:» — на случай если LLM записал и
       вычисления, и финальный ответ.
    3) Хвост после последнего знака равенства — для уравнений вида
       «24 + 37 = 61».
    """
    answer_norm = _normalize(answer, answer_type)
    otvet_tail = _extract_otvet_tail(answer)
    otvet_tail_norm = _normalize(otvet_tail, answer_type) if otvet_tail else ""
    eq_tail = _extract_eq_tail(answer)
    eq_tail_norm = _normalize(eq_tail, answer_type) if eq_tail else ""

    for correct in correct_answers:
        correct_norm = _normalize(correct, answer_type)
        if correct_norm == answer_norm:
            return True
        if otvet_tail_norm and otvet_tail_norm == correct_norm:
            return True
        if eq_tail_norm and eq_tail_norm == correct_norm:
            return True
        # Иногда эталон — это короткое слово/число, а ответ ученика
        # дополнительно содержит его (например "в среду" внутри "Меньше всего
        # потратили в среду"). Подстрочный поиск ТОЛЬКО когда эталон
        # достаточно длинный (>= 3 символа после нормализации), иначе можно
        # ложно сматчить «1» в «10».
        if len(correct_norm) >= 3 and correct_norm in answer_norm:
            return True
    return False


def _extract_otvet_tail(value: str) -> str:
    """Из строки '... Ответ: 14 наклеек' вернуть '14 наклеек'. Иначе ''."""
    m = _OTVET_TAIL_RE.search(value)
    return m.group(1).strip() if m else ""


def _extract_eq_tail(value: str) -> str:
    """Из строки вида '24 + 37 = 61' вернуть '61'. Иначе — ''."""
    if "=" not in value:
        return ""
    m = _EQ_TAIL_RE.search(value)
    return m.group(1).strip() if m else ""


def _normalize(value: str, answer_type: str) -> str:
    """Normalize an answer string for comparison.

    Делает:
    - .strip()
    - унифицирует все виды длинных дефисов/минусов в обычный ASCII '-'
    - схлопывает любые подряд идущие пробелы в один
    - убирает пробелы вокруг операторов '+', '-', '=', '<', '>', '*'
    - убирает один конечный знак '.' (но только если строка не пустая)
    - для text/ordered_list — приводит к нижнему регистру
    - для ordered_list — нормализует «разделители-запятые»
    - для numeric — приводит запятую к точке как десятичный разделитель
    """
    v = value.strip()
    if not v:
        return ""

    # Единый дефис
    v = v.translate(_DASH_TRANS)

    # Убираем пробелы вокруг типовых операторов
    v = re.sub(r"\s*([+\-=<>*/])\s*", r"\1", v)

    # Схлопываем оставшиеся пробелы
    v = re.sub(r"\s+", " ", v).strip()

    # Один конечный «.» как маркер сокращения — убираем
    if v.endswith("."):
        v = v[:-1].rstrip()

    if answer_type in ("text", "ordered_list"):
        v = v.lower()

    if answer_type == "ordered_list":
        # «504 450 405 54» / «504,450,405,54» / «504, 450, 405, 54» → канон
        parts = re.split(r"[,\s]+", v)
        parts = [p for p in parts if p]
        v = ", ".join(parts)

    if answer_type == "numeric":
        # Десятичный разделитель — точка
        if _NUM_RE.match(v):
            v = v.replace(",", ".")

    return v

# Маркеры «нет ответа» — LLM иногда записывает не пустую строку, а тире,
# слово «пусто» / «нет ответа». Считаем такие случаи нулём без LLM-вызова.
_EMPTY_MARKERS = {
    "", "-", "—", "–", "−",
    "нет", "нет ответа", "нет.", "—.",
    "пусто", "пусто.", "пропуск", "пропуск.",
    "no answer", "no", "n/a", "na", "—  —", "?",
}


def _looks_like_empty_marker(value: str) -> bool:
    v = (value or "").strip().lower()
    if not v:
        return True
    # Уберём все пробелы и пунктуацию, оставим знак-маркер
    return v in _EMPTY_MARKERS

