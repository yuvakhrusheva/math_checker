"""Deterministic scoring engine for math_checker.

compute_score takes a recognized answer string and a task's criteria dict and
returns (score, grading_notes).  No LLM calls, no I/O — fully unit-testable.
"""


def compute_score(
    recognized_answer: str | None,
    task_criteria: dict,
) -> tuple[float, str]:
    """
    Compute the score for one task based on the recognized answer and tier criteria.

    Args:
        recognized_answer: The student's answer as recognised by the LLM (may be None or "").
        task_criteria:      The task definition dict from criteria JSON.

    Returns:
        (score, grading_notes) tuple.
        score is 0 when recognized_answer is None / empty.
    """
    # Empty / missing answer → always zero
    answer = (recognized_answer or "").strip()
    if not answer:
        return 0.0, "No answer provided"

    tiers: list[dict] = task_criteria.get("tiers", [])
    correct_answers: list[str] = task_criteria.get("correct_answers", [])
    answer_type: str = task_criteria.get("answer_type", "text")
    max_score: float = float(task_criteria.get("max_score", 0))

    # Check whether the answer matches any of the accepted correct answers
    matched = _matches(answer, correct_answers, answer_type)

    if matched:
        # Return the highest-scoring tier (usually "full")
        full_tiers = [t for t in tiers if t.get("label") == "full"]
        if full_tiers:
            top = full_tiers[0]
            return float(top["score"]), top.get("condition", "Correct answer")
        # Fallback: max_score
        return max_score, "Correct answer"

    # Answer doesn't match correct answers — look for a zero or partial tier
    # Return the score from the lowest tier (zero tier)
    zero_tiers = [t for t in tiers if t.get("label") == "zero"]
    if zero_tiers:
        t = zero_tiers[0]
        return float(t["score"]), t.get("condition", "Incorrect answer")

    return 0.0, "Incorrect answer"


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
    # Collapse internal whitespace around commas for ordered_list
    if answer_type == "ordered_list":
        parts = [p.strip() for p in v.split(",")]
        v = ", ".join(parts)
    return v
