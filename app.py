"""Provenance Guard: attribution analysis API for creative-sharing platforms."""
import os
import uuid

from flask import Flask, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

import db
from labels import make_label
from scoring import combine
from signals.lexicon import lexicon_signal
from signals.llm import llm_signal
from signals.stylometry import stylometric_signal

MAX_TEXT_CHARS = 10_000
APPEAL_REASONING_CHARS = (10, 2_000)

# Rate limits (reasoning in README / planning.md). Keyed by IP, since creator_id is client-supplied.
SUBMIT_LIMIT = "10 per minute;100 per day"
APPEAL_LIMIT = "5 per hour"

app = Flask(__name__)
db.init_db()

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)


@app.errorhandler(429)
def rate_limited(e):
    return jsonify({"error": "rate limit exceeded", "limit": str(e.description)}), 429


def _bad_request(message):
    return jsonify({"error": message}), 400


def _required_str(body, field):
    value = body.get(field)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _public_view(sub):
    """Submission as shown to clients: current label reflects appeal status."""
    return {
        "content_id": sub["content_id"],
        "creator_id": sub["creator_id"],
        "attribution": sub["attribution"],
        "confidence": sub["confidence"],
        "signals": {
            "llm": {"score": sub["llm_score"], "reasoning": sub["llm_reasoning"]},
            "stylometry": {"score": sub["stylo_score"], "metrics": sub["stylo_metrics"]},
            "lexicon": {"score": sub["lexicon_score"], "hits": sub["lexicon_hits"]},
        },
        "flags": sub["flags"],
        "label": make_label(sub["attribution"], sub["status"]),
        "status": sub["status"],
        "appeal_id": sub["appeal_id"],
        "appeal_reasoning": sub["appeal_reasoning"],
        "appealed_at": sub["appealed_at"],
        "timestamp": sub["created_at"],
    }


@app.route("/submit", methods=["POST"])
@limiter.limit(SUBMIT_LIMIT)
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
    lexicon = lexicon_signal(text)
    result = combine(llm["score"], stylo["score"], lexicon["score"], stylo["word_count"])
    attribution = result["attribution"]
    label = make_label(attribution)

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
        "lexicon_score": lexicon["score"],
        "lexicon_hits": lexicon["hits"],
        "flags": result["flags"],
        "label_variant": label["variant"],
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
            "lexicon": {"score": lexicon["score"], "hits": lexicon["hits"],
                        "hits_per_100_words": lexicon["hits_per_100_words"]},
        },
        "flags": record["flags"],
        "label": label,
        "status": record["status"],
        "timestamp": record["created_at"],
    })


@app.route("/appeal", methods=["POST"])
@limiter.limit(APPEAL_LIMIT)
def appeal():
    body = request.get_json(silent=True) or {}
    content_id = _required_str(body, "content_id")
    creator_id = _required_str(body, "creator_id")
    reasoning = _required_str(body, "creator_reasoning")
    if not content_id:
        return _bad_request("'content_id' is required")
    if not creator_id:
        return _bad_request("'creator_id' is required")
    lo, hi = APPEAL_REASONING_CHARS
    if not reasoning or not lo <= len(reasoning) <= hi:
        return _bad_request(f"'creator_reasoning' is required and must be {lo}-{hi} characters")

    sub = db.get_submission(content_id)
    if sub is None:
        return jsonify({"error": "unknown content_id"}), 404
    if sub["creator_id"] != creator_id:
        return jsonify({"error": "only the original creator can appeal this content"}), 403

    updated = db.file_appeal(content_id, reasoning)
    if updated is None:
        return jsonify({"error": "this content has already been appealed",
                        "status": sub["status"]}), 409

    return jsonify({
        "appeal_id": updated["appeal_id"],
        "content_id": content_id,
        "status": updated["status"],
        "original_decision": {"attribution": updated["attribution"],
                              "confidence": updated["confidence"]},
        "label": make_label(updated["attribution"], updated["status"]),
        "message": "Your appeal has been received and will be reviewed by a person.",
    })


@app.route("/content/<content_id>", methods=["GET"])
def content(content_id):
    sub = db.get_submission(content_id)
    if sub is None:
        return jsonify({"error": "unknown content_id"}), 404
    return jsonify(_public_view(sub))


@app.route("/appeals", methods=["GET"])
def appeals():
    """Human reviewer queue: original decision, both signals, and the creator's reasoning."""
    queue = [{**_public_view(sub), "text": sub["text"]} for sub in db.get_appeal_queue()]
    return jsonify({"count": len(queue), "appeals": queue})


@app.route("/log", methods=["GET"])
def log():
    limit = min(max(request.args.get("limit", default=20, type=int), 1), 200)
    return jsonify({"entries": db.get_log(limit)})


if __name__ == "__main__":
    # Default 5001: on macOS, port 5000 is taken by the AirPlay Receiver.
    app.run(debug=True, port=int(os.environ.get("PORT", 5001)))
