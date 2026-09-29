"""Provenance Guard: attribution analysis API for creative-sharing platforms."""
import os
import uuid

from flask import Flask, jsonify, request

import db
from scoring import combine
from signals.llm import llm_signal
from signals.stylometry import stylometric_signal

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
    stylo = stylometric_signal(text)
    result = combine(llm["score"], stylo["score"], stylo["word_count"])
    attribution = result["attribution"]

    # Placeholder label text until M5; the variant is already final.
    variant = {"likely_ai": "ai", "likely_human": "human", "uncertain": "uncertain"}[attribution]
    label = {"variant": variant, "title": "placeholder", "text": "placeholder label"}

    record = {
        "content_id": str(uuid.uuid4()),
        "creator_id": creator_id.strip(),
        "text": text,
        "attribution": attribution,
        "confidence": result["confidence"],
        "llm_score": llm["score"],
        "llm_reasoning": llm["reasoning"],
        "stylo_score": stylo["score"],
        "stylo_metrics": stylo["metrics"],
        "flags": result["flags"],
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
        "signals": {
            "llm": {"score": llm["score"], "reasoning": llm["reasoning"]},
            "stylometry": {"score": stylo["score"], "metrics": stylo["metrics"],
                           "subscores": stylo["subscores"]},
        },
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
