"""ACS special codes. They are annotations, and they are not estimates."""

from __future__ import annotations

# Official annotation table. -555555555 stays null. It is not rewritten to 0.
SENTINELS = {
    "-666666666": ("-", "The estimate could not be computed."),
    "-999999999": ("N", "Too few sample cases to display."),
    "-888888888": ("(X)", "Not applicable or not available."),
    "-222222222": ("**", "The margin of error could not be computed."),
    "-333333333": ("***", "The margin of error could not be computed because the median falls in an open interval."),
    "-555555555": ("*****", "The margin of error is not appropriate because the estimate is controlled."),
}

_ANNOTATION_WORDS = {"median-", "median+", "N", "(X)", "-", "**", "***", "*****"}


def parse_cell(raw: object) -> dict:
    """Return value, annotation, and note. A sentinel integer is never a value."""
    if raw is None:
        return {"value": None, "annotation": None, "note": "No data for this geography."}
    text = str(raw).strip()
    if text == "" or text.lower() == "null":
        return {"value": None, "annotation": None, "note": "No data for this geography."}
    if text in SENTINELS:
        annotation, note = SENTINELS[text]
        return {"value": None, "annotation": annotation, "note": note}
    if text in _ANNOTATION_WORDS or text.startswith("median"):
        return {"value": None, "annotation": text, "note": "The cell is an annotation, not an estimate."}
    number = _number(text)
    if number is None:
        return {"value": None, "annotation": text, "note": "The cell was not a number."}
    if isinstance(number, int) and str(number) in SENTINELS:
        annotation, note = SENTINELS[str(number)]
        return {"value": None, "annotation": annotation, "note": note}
    return {"value": number, "annotation": None, "note": None}


def _number(text: str):
    if text.startswith("-"):
        body = text[1:]
        if body.isdigit():
            return -int(body)
        return None
    if text.isdigit():
        return int(text)
    if text.count(".") == 1:
        left, right = text.split(".")
        if left.lstrip("-").isdigit() and right.isdigit() and left not in ("", "-", "+"):
            return float(text)
    return None
