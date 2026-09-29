"""Reader-facing transparency labels. Text must match planning.md word for word."""

VARIANT_FOR_ATTRIBUTION = {"likely_ai": "ai", "likely_human": "human", "uncertain": "uncertain"}

LABELS = {
    "ai": {
        "title": "🤖 Likely AI-generated",
        "text": ("Our analysis found strong, consistent signs that this text was generated with AI "
                 "tools. Automated detection isn't perfect — the creator can appeal this label, and "
                 "appealed work is reviewed by a person."),
    },
    "human": {
        "title": "✍️ Likely human-written",
        "text": ("Our analysis found strong signs that this text was written by a person. No "
                 "detection system is perfect, but we found no meaningful indicators of AI generation."),
    },
    "uncertain": {
        "title": "❔ Origin unclear",
        "text": ("We couldn't confidently tell whether this text was written by a person or with AI "
                 "assistance. This is not an accusation — many human writers get this result. "
                 "Consider the creator's own description of their work."),
    },
}

UNDER_REVIEW_NOTE = "The creator has appealed this result; it is under review."


def make_label(attribution, status="classified"):
    """Return {"variant", "title", "text"} for an attribution and content status."""
    variant = VARIANT_FOR_ATTRIBUTION[attribution]
    text = LABELS[variant]["text"]
    if status == "under_review":
        text = f"{text} {UNDER_REVIEW_NOTE}"
    return {"variant": variant, "title": LABELS[variant]["title"], "text": text}
