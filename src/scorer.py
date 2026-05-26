"""Scoring engine for math_checker — двухпроходная архитектура.

Что изменилось по сравнению со старой версией:
- Если ответ точно совпал с одним из correct_answers (после нормализации) — full tier.
- Если ответ пустой — 0 баллов.
- Если в задании ТОЛЬКО full + zero (без partial) — wrong answer → zero tier (как было).
- Если в задании ЕСТЬ partial tiers — компонент вызывает LLM, который выбирает label тира
  по сформулированным в criteria conditions. Это исправляет баг с дробными баллами,
  которые раньше игнорировались — особенно при ручной правке в Review Panel.

LLM-вызов делается через litellm с настройками из config/settings.json.
"""
import json
import re
from pathlib import Path

import litellm

_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?```\s*$", re.DOTALL)
_SETTINGS_PATH = Path(__file__).parent.parent / "config" / "settings.json"


def _load_settings() -> dict:
    return json.loads(_SETTINGS_PATH.read_text(encoding="utf-8"))


_SETTINGS: dict = _load_settings()


def compute_score(
    recognized_answer: str | None,
    task_criteria: dict,
) -> tuple[float, str]:
    """Compute score for one task.

    Стратегия:
    1. Пустой ответ → 0 баллов (без LLM).
    2. Точное совпадение с correct_answers (после нормализации) → full tier.
    3. Если у задания только full + zero (нет partial) — non-match → zero tier.
    4. Если у задания есть partial тиры — вызывается LLM для выбора тира.

    Returns (score, grading_notes).
    """
    answer = (recognized_answer or "").strip()
    if not answer:
        return 0.0, "No answer provided"

    tiers: list[dict] = task_criteria.get("tiers", [])
    correct_answers: list[str] = task_criteria.get("correct_answers", [])
    answer_type: str = task_criteria.get("answer_type", "text")
    max_score: float = float(task_criteria.get("max_score", 0))

    # Шаг 2: точное совпадение
    matched = _matches(answer, correct_answers, answer_type)
    if matched:
        full_tiers = [t for t in tiers if t.get("label") == "full"]
        if full_tiers:
            return float(full_tiers[0]["score"]), full_tiers[0].get("condition", "Correct answer")
        return max_score, "Correct answer"

    # Шаг 3: проверяем, есть ли partial-тиры (label не "full" и не "zero")
    partial_tiers = [t for t in tiers if t.get("label") not in ("full", "zero")]
    if not partial_tiers:
        zero_tiers = [t for t in tiers if t.get("label") == "zero"]
        if zero_tiers:
            return float(zero_tiers[0]["score"]), zero_tiers[0].get("condition", "Incorrect answer")
        return 0.0, "Incorrect answer"

    # Шаг 4: есть partial-тиры — спрашиваем LLM
    try:
        label = _classify_tier_via_llm(answer, task_criteria)
    except Exception as exc:
        # При ошибке LLM (нет сети, нет ключа) — безопасно ставим zero
        zero_tiers = [t for t in tiers if t.get("label") == "zero"]
        if zero_tiers:
            return (
                float(zero_tiers[0]["score"]),
                f"LLM classifier failed ({type(exc).__name__}); defaulted to zero",
            )
        return 0.0, f"LLM classifier failed ({type(exc).__name__}); defaulted to zero"

    chosen = next((t for t in tiers if t.get("label") == label), None)
    if chosen is None:
        zero_tiers = [t for t in tiers if t.get("label") == "zero"]
        if zero_tiers:
            return (
                float(zero_tiers[0]["score"]),
                f"LLM returned unknown label {label!r}; defaulted to zero",
            )
        return 0.0, f"LLM returned unknown label {label!r}; defaulted to zero"
    return float(chosen["score"]), chosen.get("condition", f"Tier: {label}")


def _classify_tier_via_llm(answer: str, task_criteria: dict) -> str:
    """Ask the LLM which tier label fits this answer best.

    Returns one of the tier labels from task_criteria (e.g. "full", "partial", "zero").
    Возвращает пустую строку, если ответ LLM не распарсился.
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
        f"Pick the tier label that best fits the student's answer based on the conditions above. "
        f"Be strict — only award higher tiers when the answer truly satisfies the condition.\n"
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
    """Return True if the answer matches any of the accepted correct answers."""
    answer_norm = _normalize(answer, answer_type)
    for correct in correct_answers:
        if _normalize(correct, answer_type) == answer_norm:
            return True
    return False


def _normalize(value: str, answer_type: str) -> str:
    """Normalize an answer string for comparison."""
    v = value.strip()
    if answer_type in ("text", "ordered_list"):
        v = v.lower()
    if answer_type == "ordered_list":
        parts = [p.strip() for p in v.split(",")]
        v = ", ".join(parts)
    return v
