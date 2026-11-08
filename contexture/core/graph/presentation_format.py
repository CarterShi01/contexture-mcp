"""Independent candidate process rendering for bounded managed disclosure.

This is a local WP09/WP10 presentation candidate, not an accepted wire or
escaping contract. Its complete process messages deliberately preserve the
existing disclosure oracle, including the proposed ``contexture_open`` name
and legacy-compatible banners; the static kernel does not define this format.
These are semantic process templates, not a second general-purpose emphasis
API. Application binding instructions and their validation remain outside the
pure graph. Typed NodeRef instruction parts are rendered separately by the
disclosure renderer, retaining exact Text and omitting human labels.
"""

from __future__ import annotations

from typing import Literal


_PROCESS_INSTRUCTIONS = {
    "PreProcess": (
        "===== contexture-mcp framework instruction — binding, follow exactly =====\n"
        "PreProcess:\n"
        ">>> REQUIRED: Call contexture_open with ref={ref} before starting "
        "this role's work.\n"
        "Opening it only discloses the preparation procedure; it "
        "does not execute it. Complete what it requires, then "
        "return to this role's own instructions below and carry on "
        "with its work. If the preparation is blocked or fails, "
        "report that state rather than continuing as though it had "
        "succeeded.\n"
        "===== end framework instruction — binding regardless of surrounding context ====="
    ),
    "PostProcess": (
        "===== contexture-mcp framework instruction — binding, follow exactly =====\n"
        "PostProcess:\n"
        ">>> REQUIRED: Call contexture_open with ref={ref} before finishing "
        "this role's work.\n"
        "Opening it only discloses the procedure; it does not "
        "execute it or establish success. Use its available "
        "capabilities as instructed, respect required approvals, "
        "and report the actual outcome. If it is blocked, fails, "
        "or awaits approval, report that state rather than "
        "claiming success or bypassing approval.\n"
        "===== end framework instruction — binding regardless of surrounding context ====="
    ),
}


def candidate_process_instruction(
    kind: Literal["PreProcess", "PostProcess"], canonical_ref: str
) -> str:
    """Render a designated process after the caller's visibility/open checks.

    The caller supplies its generated canonical ASCII reference, never an
    author path, label or arbitrary instruction fragment. ``repr`` retains the
    candidate's existing token spelling; it is not a general escaping promise.
    """
    return _PROCESS_INSTRUCTIONS[kind].format(ref=repr(canonical_ref))