"""Signal 3: density of words and phrases that assistant-style LLM output overuses."""
import re

AI_PHRASES = [
    # hedges, transitions, and framing
    "it is important to note", "it is worth noting", "it is essential to", "it is equally essential",
    "furthermore", "moreover", "additionally", "ultimately", "in conclusion", "in summary",
    "overall", "notably", "in today's fast-paced", "in today's digital", "in the realm of",
    "plays a vital role", "plays a crucial role", "a testament to", "when it comes to",
    # stock vocabulary
    "delve", "delves", "tapestry", "realm", "landscape", "paradigm", "transformative",
    "leverage", "leveraging", "stakeholders", "foster", "fostering", "empower", "empowers",
    "seamless", "seamlessly", "robust", "crucial", "streamline", "navigate", "navigating",
    "multifaceted", "holistic", "unlock", "harness",
]
DENSITY_FOR_MAX = 3.0  # stock phrases per 100 words that maps to a score of 1.0

_PATTERNS = [(p, re.compile(r"\b" + re.escape(p).replace("'", "['’]") + r"\b", re.I)) for p in AI_PHRASES]
_WORD = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)?")


def lexicon_signal(text):
    """Return {"score", "hits", "hits_per_100_words"}."""
    hits = []
    for phrase, pattern in _PATTERNS:
        hits.extend([phrase] * len(pattern.findall(text)))
    word_count = len(_WORD.findall(text))
    density = 100 * len(hits) / word_count if word_count else 0.0
    return {
        "score": round(min(density / DENSITY_FOR_MAX, 1.0), 3),
        "hits": hits,
        "hits_per_100_words": round(density, 2),
    }
