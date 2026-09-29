"""Provenance Guard: attribution analysis API for creative-sharing platforms."""
import os
import uuid

from flask import Flask, jsonify, request

import db
from signals.llm import llm_signal

MAX_TEXT_CHARS = 10_000

app = Flask(__name__)
db.init_db()


def _bad_request(message):
    return jsonify({"error": message}), 400


@app.route("/submit", methods=["POST"])
def submit():
    body = request.get_json(silent=True) or {}
    text = body.get("text")
    creator_id = body.get("creator_id")
    if not isinstance(text, str) or not text.strip():
        return _bad_request("'text' is required and must be a non-empty string")
    if len(text) > MAX_TEXT_CHARS:
        return _bad_request(f"'text' must be at most {MAX_TEXT_CHARS} characters")
    if not isinstance(creator_id, str) or not creator_id.strip():
        return _bad_request("'creator_id' is required and must be a non-empty string")

    llm = llm_signal(text)

    # Placeholder scoring until the second signal lands (M4): confidence = LLM score.
    confidence = llm["score"] if llm["score"] is not None else 0.5
    if confidence >= 0.80:
        attribution, variant = "likely_ai", "ai"
    elif confidence <= 0.35:
        attribution, variant = "likely_human", "human"
    else:
        attribution, variant = "uncertain", "uncertain"
    label = {"variant": variant, "title": "placeholder", "text": "placeholder label"}

    record = {
        "content_id": str(uuid.uuid4()),
        "creator_id": creator_id.strip(),
        "text": text,
        "attribution": attribution,
        "confidence": round(confidence, 3),
        "llm_score": llm["score"],
        "llm_reasoning": llm["reasoning"],
        "flags": [] if llm["score"] is not None else ["llm_unavailable"],
        "label_variant": variant,
        "status": "classified",
        "created_at": db.now_iso(),
    }
    db.save_submission(record)

    return jsonify({
        "content_id": record["content_id"],
        "creator_id": record["creator_id"],
        "attribution": attribution,
        "confidence": record["confidence"],
        "signals": {"llm": {"score": llm["score"], "reasoning": llm["reasoning"]}},
        "flags": record["flags"],
        "label": label,
        "status": record["status"],
        "timestamp": record["created_at"],
    })


@app.route("/log", methods=["GET"])
def log():
    limit = min(max(request.args.get("limit", default=20, type=int), 1), 200)
    return jsonify({"entries": db.get_log(limit)})


if __name__ == "__main__":
    # Default 5001: on macOS, port 5000 is taken by the AirPlay Receiver.
    app.run(debug=True, port=int(os.environ.get("PORT", 5001)))
