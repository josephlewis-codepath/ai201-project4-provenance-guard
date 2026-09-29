"""Signal 2: structural statistics of the text, independent of meaning.

Each metric is normalized to a sub-score where 0 = human-like and 1 = AI-like,
then combined with fixed weights (see planning.md, "Signal 2").
"""
import re
import statistics

WEIGHTS = {"burstiness": 0.40, "word_length": 0.25, "informality": 0.35}

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_WORD = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")
_INFORMAL_PATTERNS = [
    re.compile(r"\b[A-Za-z]+'(?:t|s|re|ve|ll|d|m)\b", re.I),  # contractions
    re.compile(r"\bi\b"),                                      # lowercase "i"
    re.compile(r"[!?]"),
    re.compile(r"\.\.\.|…"),
    re.compile(r"[()]"),
    re.compile(r"\b[A-Z]{2,}\b"),                              # shouting / ALL-CAPS
    re.compile(r"—|–|\s-\s"),                                  # dashes
]


def _clamp(x):
    return min(max(x, 0.0), 1.0)


def split_sentences(text):
    return [s for s in (p.strip() for p in _SENTENCE_SPLIT.split(text)) if _WORD.search(s)]


def stylometric_signal(text):
    """Return {"score", "metrics", "subscores", "sentence_count", "word_count"}."""
    sentences = split_sentences(text)
    words = _WORD.findall(text)
    word_count = len(words)

    lengths = [len(_WORD.findall(s)) for s in sentences]
    if len(lengths) >= 2 and statistics.mean(lengths) > 0:
        cv = statistics.pstdev(lengths) / statistics.mean(lengths)
    else:
        cv = 0.0  # a single sentence has no measurable variation
    avg_word_length = statistics.mean(len(w) for w in words) if words else 0.0

    markers = sum(len(p.findall(text)) for p in _INFORMAL_PATTERNS)
    markers += sum(1 for s in sentences if s[0].islower())    # lowercase sentence starts
    informality_rate = markers / word_count if word_count else 0.0

    subscores = {
        "burstiness": _clamp((0.75 - cv) / 0.55),
        "word_length": _clamp((avg_word_length - 4.2) / 1.6),
        "informality": _clamp(1 - informality_rate / 0.08),
    }
    score = sum(WEIGHTS[k] * v for k, v in subscores.items())

    return {
        "score": round(score, 3),
        "metrics": {
            "sentence_length_cv": round(cv, 3),
            "avg_word_length": round(avg_word_length, 2),
            "informality_rate": round(informality_rate, 3),
        },
        "subscores": {k: round(v, 3) for k, v in subscores.items()},
        "sentence_count": len(sentences),
        "word_count": word_count,
    }
