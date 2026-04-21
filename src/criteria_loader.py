"""Criteria JSON loader, validator, and saver for math_checker."""
import json
import re
from pathlib import Path

CRITERIA_DIR = Path("criteria")
_FILENAME_RE = re.compile(r"grade([23])_(ru|az)_v([12])\.json")


def _parse_filename(filename: str):
    """Return (grade, language, variant) from a valid filename, or raise ValueError."""
    m = _FILENAME_RE.fullmatch(filename)
    if not m:
        raise ValueError(
            f"Invalid criteria filename format: {filename!r}. "
            f"Expected pattern: grade[23]_(ru|az)_v[12].json"
        )
    return int(m.group(1)), m.group(2), int(m.group(3))


def load_criteria(grade: int, language: str, variant: int) -> dict:
    """Load and return parsed criteria dict. Raise FileNotFoundError if missing."""
    filename = f"grade{grade}_{language}_v{variant}.json"
    path = CRITERIA_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"Criteria file not found: {path}")
    return json.loads(path.read_bytes())


def validate_criteria_file(filename: str, content: bytes) -> dict:
    """
    Validate filename format and that internal fields match filename-derived values.
    Returns parsed dict on success; raises ValueError with descriptive message on failure.
    """
    grade_from_name, lang_from_name, variant_from_name = _parse_filename(filename)

    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {filename!r}: {exc}") from exc

    if data.get("grade") != grade_from_name:
        raise ValueError(
            f"grade mismatch in {filename!r}: filename says {grade_from_name}, "
            f"JSON contains {data.get('grade')!r}"
        )
    if data.get("language") != lang_from_name:
        raise ValueError(
            f"language mismatch in {filename!r}: filename says {lang_from_name!r}, "
            f"JSON contains {data.get('language')!r}"
        )
    if data.get("variant") != variant_from_name:
        raise ValueError(
            f"variant mismatch in {filename!r}: filename says {variant_from_name}, "
            f"JSON contains {data.get('variant')!r}"
        )

    return data


def save_criteria_file(filename: str, content: bytes) -> Path:
    """
    Validate content, enforce path-traversal protection, write to criteria/.
    Returns resolved Path on success; raises ValueError on any validation failure.
    """
    # Reject filenames with path separators or parent-directory components upfront
    if Path(filename).name != filename or ".." in filename:
        raise ValueError(
            f"Path traversal detected in filename: {filename!r}. "
            "Filename must not contain path separators or '..' components."
        )

    validate_criteria_file(filename, content)  # raises ValueError if invalid

    CRITERIA_DIR.mkdir(exist_ok=True)

    target = (CRITERIA_DIR / filename).resolve()
    criteria_root = CRITERIA_DIR.resolve()

    if not target.is_relative_to(criteria_root):
        raise ValueError(
            f"Path traversal detected: {filename!r} resolves outside {criteria_root}"
        )

    target.write_bytes(content)
    return target


def list_available_combinations() -> list[dict]:
    """
    Scan criteria/ for files matching naming convention.
    Returns list of {"grade": int, "language": str, "variant": int}.
    Returns [] if criteria/ does not exist or contains no matching files.
    """
    if not CRITERIA_DIR.exists():
        return []

    results = []
    for path in sorted(CRITERIA_DIR.glob("*.json")):
        m = _FILENAME_RE.fullmatch(path.name)
        if m:
            results.append({
                "grade": int(m.group(1)),
                "language": m.group(2),
                "variant": int(m.group(3)),
            })
    return results


def criteria_exists(grade: int, language: str) -> bool:
    """Return True if at least one variant file exists for the given grade+language combo."""
    if not CRITERIA_DIR.exists():
        return False
    for variant in (1, 2):
        if (CRITERIA_DIR / f"grade{grade}_{language}_v{variant}.json").exists():
            return True
    return False
