"""Combine signal scores into one confidence score and an attribution.

confidence = estimated likelihood the text is AI-generated (0 = human, 1 = AI);
0.5 means the evidence is balanced. Thresholds are lopsided on purpose: a false
"AI" label harms a real writer, so it requires much stronger evidence.
"""

WEIGHTS = {"llm": 0.5, "stylometry": 0.3, "lexicon": 0.2}
# Used when the LLM is down; the result is still forced to "uncertain".
FALLBACK_WEIGHTS = {"stylometry": 0.6, "lexicon": 0.4}
AI_THRESHOLD = 0.75       # confidence >= this -> likely_ai
HUMAN_THRESHOLD = 0.35    # confidence <= this -> likely_human
LLM_AI_FLOOR = 0.70       # an AI verdict also needs the LLM itself to lean AI
DISAGREEMENT_LIMIT = 0.45
MIN_WORDS = 30


def attribution_for(confidence):
    if confidence >= AI_THRESHOLD:
        return "likely_ai"
    if confidence <= HUMAN_THRESHOLD:
        return "likely_human"
    return "uncertain"


def combine(llm_score, stylo_score, lexicon_score, word_count):
    """Return {"confidence", "attribution", "flags"}; override rules follow planning.md order."""
    flags = []
    if llm_score is None:
        # Never accuse on heuristics alone.
        confidence = (FALLBACK_WEIGHTS["stylometry"] * stylo_score
                      + FALLBACK_WEIGHTS["lexicon"] * lexicon_score)
        return {"confidence": round(confidence, 3), "attribution": "uncertain",
                "flags": ["llm_unavailable"]}

    confidence = (WEIGHTS["llm"] * llm_score + WEIGHTS["stylometry"] * stylo_score
                  + WEIGHTS["lexicon"] * lexicon_score)
    if word_count < MIN_WORDS:
        flags.append("short_text")
    if abs(llm_score - stylo_score) > DISAGREEMENT_LIMIT:
        flags.append("signal_disagreement")

    attribution = "uncertain" if flags else attribution_for(confidence)
    if attribution == "likely_ai" and llm_score < LLM_AI_FLOOR:
        # The heuristic signals can support an AI verdict but never produce one on their own.
        attribution = "uncertain"
        flags.append("weak_llm_evidence")
    return {"confidence": round(confidence, 3), "attribution": attribution, "flags": flags}
