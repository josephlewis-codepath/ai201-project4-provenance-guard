"""Signal 1: holistic LLM judgment of whether text reads as AI-generated."""
import json
import os

from dotenv import load_dotenv
from groq import Groq

load_dotenv()

# llama-4-scout (the course default) has been retired on Groq; override via GROQ_MODEL.
MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")

SYSTEM_PROMPT = """You are an expert at distinguishing human-written text from AI-generated text.
Assess the text the user provides. Consider: generic hedging or filler phrases ("it is important to note"),
formulaic transitions, balanced-but-empty structure, overly polished tone, and absence of concrete
personal detail (AI-like) versus idiosyncratic voice, specific lived detail, irregular rhythm,
typos, slang, or strong opinions (human-like).

Formal or non-native writing is not by itself evidence of AI. If the evidence is weak or mixed,
give a probability near 0.5.

Respond with JSON only, in exactly this shape:
{"ai_probability": <number from 0.0 (certainly human) to 1.0 (certainly AI)>, "reasoning": "<one or two sentences>"}"""

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
    return _client


def llm_signal(text):
    """Return {"score": float in [0, 1] or None, "reasoning": str}.

    score is None when the API call fails or returns unusable output; callers
    must treat that as "signal unavailable", never as 0.
    """
    try:
        response = _get_client().chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        data = json.loads(response.choices[0].message.content)
        score = min(max(float(data["ai_probability"]), 0.0), 1.0)
        return {"score": round(score, 3), "reasoning": str(data.get("reasoning", ""))}
    except Exception as exc:
        return {"score": None, "reasoning": f"LLM signal unavailable: {type(exc).__name__}"}
