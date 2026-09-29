# Provenance Guard — Planning & Spec

Provenance Guard is a backend service a creative-sharing platform can plug into. It classifies submitted text as likely human-written, likely AI-generated, or uncertain; attaches a plain-language transparency label; lets creators appeal; and records every decision in an audit log.

**Guiding principle:** On a writing platform, a false positive (calling a human's work AI-generated) is worse than a false negative. Every design choice below leans toward "uncertain" rather than "accuse."

---

## Architecture

### Submission flow

```
 Client
   │  JSON {text, creator_id}
   ▼
┌──────────────────────┐   429 if over limit
│ POST /submit         │──────────────────────► Client
│ (Flask-Limiter)      │
└──────────┬───────────┘
           │ raw text (validated: non-empty, ≤ 10,000 chars)
           ├──────────────────────────────┐
           ▼                              ▼
┌──────────────────────┐      ┌──────────────────────────┐
│ Signal 1: LLM        │      │ Signal 2: Stylometry     │
│ (Groq llama-4-scout) │      │ (pure Python)            │
└──────────┬───────────┘      └────────────┬─────────────┘
           │ llm_score 0–1                 │ stylo_score 0–1
           │ + llm_reasoning               │ + metric breakdown
           └──────────────┬────────────────┘
                          ▼
             ┌──────────────────────────┐
             │ Confidence scoring       │
             │ weighted combine + rules │
             └────────────┬─────────────┘
                          │ confidence 0–1 + attribution
                          ▼
             ┌──────────────────────────┐
             │ Label generator          │
             └────────────┬─────────────┘
                          │ label text
                          ▼
             ┌──────────────────────────┐
             │ SQLite                   │
             │  submissions (state)     │
             │  audit_log (events)      │
             └────────────┬─────────────┘
                          │ content_id + full record
                          ▼
                 JSON response ──► Client
```

### Appeal flow

```
 Client
   │  JSON {content_id, creator_id, creator_reasoning}
   ▼
┌──────────────────────┐   404 unknown content_id
│ POST /appeal         │   403 creator_id mismatch
│ (Flask-Limiter)      │   409 already appealed
└──────────┬───────────┘──────────────────────► Client
           │ content_id + reasoning
           ▼
┌──────────────────────────────┐
│ submissions: status →        │
│ "under_review"               │
└──────────┬───────────────────┘
           │ original decision + reasoning
           ▼
┌──────────────────────────────┐
│ audit_log: "appeal_filed"    │
│ event (with original scores) │
└──────────┬───────────────────┘
           │ appeal_id, status
           ▼
   JSON confirmation ──► Client
```

**Narrative.** When a creator submits text, `/submit` checks the rate limit and validates the input. It then runs two independent detection signals: a Groq LLM judgment, which captures meaning and overall style, and stylometric statistics, which capture structure. The scoring module combines the two into one confidence score and an attribution, the label generator turns that into reader-facing text, and the result is stored in SQLite (current state plus an append-only audit event) before being returned with a `content_id`. When a creator disputes a result, `/appeal` checks that the `content_id` exists and belongs to them, sets the content's status to `under_review`, and writes an `appeal_filed` audit event that includes the original decision. The event lands in a queue for a human reviewer.

### Components / files

| File | Responsibility |
|---|---|
| `app.py` | Flask app, routes, rate limiter, input validation |
| `signals/llm.py` | `llm_signal(text) -> dict` |
| `signals/stylometry.py` | `stylometric_signal(text) -> dict` |
| `scoring.py` | `combine(llm, stylo, word_count) -> dict` |
| `labels.py` | `make_label(attribution) -> dict` |
| `db.py` | SQLite schema, `save_submission`, `log_event`, `get_log`, `file_appeal` |
| `scripts/calibrate.py` | Runs the test inputs and prints each signal next to the combined score |

---

## API Contract

### `POST /submit`
Request:
```json
{ "text": "string, required, 1–10,000 chars", "creator_id": "string, required" }
```
Response `200`:
```json
{
  "content_id": "uuid4",
  "creator_id": "test-user-1",
  "attribution": "likely_ai | likely_human | uncertain",
  "confidence": 0.82,
  "signals": {
    "llm":   { "score": 0.88, "reasoning": "..." },
    "stylometry": { "score": 0.73, "metrics": { "sentence_length_cv": 0.21, "avg_word_length": 5.9, "informality": 0.02 } }
  },
  "flags": ["signal_disagreement" | "short_text" | "llm_unavailable"],
  "label": { "variant": "ai | human | uncertain", "title": "...", "text": "..." },
  "status": "classified",
  "timestamp": "ISO-8601 UTC"
}
```
Errors: `400` missing or invalid fields, `429` rate limited.

### `POST /appeal`
Request:
```json
{ "content_id": "uuid4", "creator_id": "string", "creator_reasoning": "string, 10–2,000 chars" }
```
Response `200`:
```json
{ "appeal_id": "uuid4", "content_id": "...", "status": "under_review", "message": "Your appeal has been received and will be reviewed by a person." }
```
Errors: `400` invalid, `403` creator mismatch, `404` unknown content, `409` already under review, `429` rate limited.

### `GET /log?limit=20`
Returns `{"entries": [...]}`, newest first, from `audit_log`.

### `GET /content/<content_id>`
Returns the current submission record (attribution, confidence, label, status, appeal if any).

### `GET /appeals`
The reviewer queue: every submission with status `under_review`, together with its original decision, both signals, and the creator's reasoning.

---

## Detection Signals

Both signals output a score in **[0, 1] where 0 = strongly human-like and 1 = strongly AI-like**. Using the same scale for both makes them directly combinable.

### Signal 1 — LLM classification (Groq, `meta-llama/llama-4-scout-17b-16e-instruct`)

- **What it measures:** A holistic judgment of meaning and style. It looks at generic hedging ("it is important to note"), balanced-but-empty structure, lack of concrete personal detail, formulaic transitions, and overly polished tone.
- **Why it differs between human and AI writing:** LLM output tends toward the statistically "average" phrasing and structure, and another LLM is good at recognizing that register.
- **Output:** The model is prompted to return JSON only (`response_format={"type": "json_object"}`, `temperature=0`):
  ```json
  { "ai_probability": 0.0-1.0, "reasoning": "one or two sentences" }
  ```
  `llm_score = clamp(ai_probability, 0, 1)`.
- **Failure handling:** If the API errors or returns unparseable JSON, the result is `llm_score = None`, the `llm_unavailable` flag is set, and scoring falls back as described below. The endpoint never returns 500 because Groq is down.
- **Blind spots:** LLMs are overconfident and inconsistent near the middle of the range. They can be fooled by AI text prompted to sound casual or by lightly edited AI output. They may also rate polished, formal, or non-native human writing as AI because it "sounds like" AI register.

### Signal 2 — Stylometric heuristics (pure Python)

- **What it measures:** Structural statistics of the text, independent of its meaning. There are three metrics:

| Metric | Computation | AI-like direction | Normalized sub-score (0 = human, 1 = AI) |
|---|---|---|---|
| **Burstiness** — sentence-length coefficient of variation | `stdev(words per sentence) / mean` (sentences split on `.!?` and newlines) | Low variation (uniform sentences) | `clamp((0.75 − cv) / 0.55)` → cv ≤ 0.20 → 1.0, cv ≥ 0.75 → 0.0 |
| **Word length** — average characters per word | `mean(len(word))` over alphabetic tokens | Longer, "elevated" vocabulary | `clamp((avg − 4.2) / 1.6)` → ≤ 4.2 → 0.0, ≥ 5.8 → 1.0 |
| **Informality markers** — rate per word | count of contractions, lowercase "i", lowercase sentence starts, `!`/`?`/`...`, parentheses, ALL-CAPS words, dashes; divided by word count | Few informal markers | `clamp(1 − rate / 0.08)` → rate ≥ 0.08 → 0.0 |

  `stylo_score = 0.4·burstiness + 0.25·word_length + 0.35·informality`

  Burstiness gets the most weight because it is the most widely cited structural difference. Word length gets the least because formal human writing also uses long words.
- **Why it differs:** AI text tends to be uniform: sentences of similar length, consistent register, and clean punctuation. Human writing is more irregular.
- **Output:**
  ```json
  { "score": 0.73, "metrics": { "sentence_length_cv": 0.21, "avg_word_length": 5.9, "informality_rate": 0.01 }, "sentence_count": 4, "word_count": 58 }
  ```
- **Blind spots:** The metrics can't read meaning. Formal academic writing, legal or technical prose, non-native English writers, and poetry built on deliberate repetition all look "uniform" and score as AI-like. Casual-sounding AI text (prompted to use contractions and lowercase) scores as human. Burstiness is unreliable with fewer than about 3 sentences.

**Why these two:** One signal is semantic and one is structural, so they fail in different ways. When they agree, we can be more confident. When they disagree, that disagreement is itself useful information: it means we should say "uncertain."

The starting constants above are **initial values to be calibrated in M4** against the test inputs. Any changes will be recorded here.

---

## Confidence Scoring & Uncertainty

### What the number means

`confidence` is the system's estimate of **how likely the text is AI-generated**, from 0 to 1:
- **0.5 means "the evidence is balanced — we genuinely don't know."**
- **0.6 means "a slight lean toward AI, not enough to say so."** A 0.6 always produces the *uncertain* label.
- Only scores at or above **0.80** ("strong, consistent evidence") earn the AI label.

### Combination

```
combined = 0.6 · llm_score + 0.4 · stylo_score
```
The LLM gets more weight because it uses far more information. Stylometry keeps it honest and supplies the disagreement check.

### Override rules (applied in order)

1. **LLM unavailable:** `combined = stylo_score`, and the attribution is forced to `uncertain`. We never accuse someone based on heuristics alone.
2. **Short text** (< 50 words): the attribution is forced to `uncertain` and the `short_text` flag is set. There is too little text to judge.
3. **Signal disagreement** (`|llm_score − stylo_score| > 0.45`): the attribution is forced to `uncertain` and the `signal_disagreement` flag is set.
4. Otherwise, apply the thresholds below.

### Thresholds (deliberately lopsided)

| Combined score | Attribution | Label variant |
|---|---|---|
| **≥ 0.80** | `likely_ai` | High-confidence AI |
| **0.36 – 0.79** | `uncertain` | Uncertain |
| **≤ 0.35** | `likely_human` | High-confidence human |

**Why lopsided:** The AI band requires a score 0.30 above the midpoint, while the human band needs only 0.15 below it. A false "AI" label can damage a real writer's reputation. A false "human" label is a less harmful miss, and it can still be corrected through reports and review. Most of the scale deliberately lands in "uncertain."

### Validation plan (M4)
Run `scripts/calibrate.py` over the four provided test inputs plus at least two of my own (a repetitive poem and a non-native formal paragraph). Print both signals and the combined score for each. The system passes when:
- The clearly AI text reaches ≥ 0.80.
- The clearly human text reaches ≤ 0.35.
- The borderline cases land in the uncertain band, or are explained.
- All three label variants are reachable.

---

## Transparency Labels

The labels are written for a reader with no technical background. They contain no raw percentages, because "73% AI" invites over-reading. Each label states the verdict, how sure we are in plain words, and a reminder that detection can be wrong.

| Variant | Title | Body text |
|---|---|---|
| **High-confidence AI** (`ai`) | 🤖 Likely AI-generated | "Our analysis found strong, consistent signs that this text was generated with AI tools. Automated detection isn't perfect — the creator can appeal this label, and appealed work is reviewed by a person." |
| **High-confidence human** (`human`) | ✍️ Likely human-written | "Our analysis found strong signs that this text was written by a person. No detection system is perfect, but we found no meaningful indicators of AI generation." |
| **Uncertain** (`uncertain`) | ❔ Origin unclear | "We couldn't confidently tell whether this text was written by a person or with AI assistance. This is not an accusation — many human writers get this result. Consider the creator's own description of their work." |

When a submission is under appeal, the label text gets one more line: *"The creator has appealed this result; it is under review."*

---

## Appeals Workflow

- **Who can appeal:** Only the creator who submitted the content. The request's `creator_id` must match the stored `creator_id`. In production this would be tied to authentication; here it is a simple ownership check.
- **What they provide:** `content_id`, `creator_id`, and `creator_reasoning` (free text, 10–2,000 characters). For example: "I wrote this from personal experience; I'm a non-native speaker so my style is formal."
- **What the system does:**
  1. Validates the request (exists → 404, owner → 403, not already `under_review` → 409).
  2. Sets `submissions.status` from `classified` to `under_review` and stores `appeal_reasoning` and `appealed_at`.
  3. Appends an `appeal_filed` event to `audit_log`. The event includes the **original** attribution, confidence, and both signal scores, so a reviewer sees the decision exactly as it was made.
  4. Returns an `appeal_id` and the new status.
- **No automated re-classification.** A person resolves the appeal. Resolution is out of scope, but the statuses reserved for it are `upheld` and `overturned`.
- **What a reviewer sees** (`GET /appeals`), one entry per appealed item:
  - the content text
  - the creator ID
  - the original attribution, confidence, and label
  - the LLM score and reasoning
  - the stylometric score and its metrics
  - any flags
  - the creator's reasoning
  - the submission and appeal timestamps

  The queue is sorted oldest-first.

---

## Anticipated Edge Cases

1. **A repetitive, simple-vocabulary poem.** For example, a villanelle or a children's poem with refrains ("I will not go / I will not go"). Repeated lines of equal length give very low burstiness, and there is little informal punctuation, so stylometry leans AI. The LLM may recognize it as deliberate poetic form. *Mitigation:* the disagreement rule sends it to **uncertain**, not AI.
2. **A non-native English speaker's formal prose.** Careful, textbook-style grammar with few contractions and uniform sentences. Both signals may lean AI, which is the worst-case false positive. *Mitigation:* the high 0.80 bar, the uncertain label's wording ("many human writers get this result"), and the appeal path. This is the scenario the appeal test uses.
3. **Lightly edited AI output.** AI text with a few added contractions and a personal opener ("I've been thinking a lot about…"). The informality score drops and the LLM may be fooled. It likely lands in **uncertain**, which is the honest answer.
4. **Very short text** (a haiku, a one-line caption). Too few sentences for meaningful statistics. *Mitigation:* the under-50-words rule forces **uncertain**.
5. **Lists, code, or dialogue-heavy text.** Splitting on sentence punctuation behaves strangely (bullet lists have no periods, and dialogue has many short fragments). This is documented as a known limitation.

---

## Rate Limiting

| Endpoint | Limit (per IP) | Reasoning |
|---|---|---|
| `POST /submit` | **10 per minute; 100 per day** | A real writer submits a few pieces per session. Even someone bulk-uploading a back catalog rarely exceeds one piece every 6 seconds. 10/min absorbs bursts, while a flooding script is stopped within seconds. 100/day covers heavy legitimate use and caps how much of the shared Groq free-tier quota any single client can burn. It also limits an adversary probing the detector with many variants to find text that slips through. |
| `POST /appeal` | **5 per hour** | Appeals are rare and deliberate. A tight limit prevents spamming the human review queue. |
| `GET` endpoints | none | Read-only; in production they would sit behind auth. |

The limits are keyed by IP (`get_remote_address`), not `creator_id`, because `creator_id` is client-supplied and trivially spoofable. Storage is `memory://` for local development; production would use Redis. The limit strings live as constants in `app.py`.

---

## Data Model (SQLite)

**`submissions`** — the current state of each piece of content
`content_id` (PK), `creator_id`, `text`, `attribution`, `confidence`, `llm_score`, `llm_reasoning`, `stylo_score`, `stylo_metrics` (JSON), `flags` (JSON), `label_variant`, `status` (`classified` | `under_review`), `appeal_reasoning`, `appealed_at`, `created_at`

**`audit_log`** — append-only, one row per event; never updated
`id` (PK), `timestamp`, `event` (`classified` | `appeal_filed`), `content_id`, `creator_id`, `attribution`, `confidence`, `llm_score`, `stylo_score`, `signals_used` (JSON), `flags` (JSON), `status`, `appeal_reasoning`

Example `audit_log` entry:
```json
{
  "timestamp": "2026-09-29T14:32:10.123Z",
  "event": "classified",
  "content_id": "3f7a2b1e-...",
  "creator_id": "test-user-1",
  "attribution": "likely_ai",
  "confidence": 0.84,
  "llm_score": 0.90,
  "stylo_score": 0.75,
  "signals_used": ["llm", "stylometry"],
  "flags": [],
  "status": "classified",
  "appeal_reasoning": null
}
```

---

## AI Tool Plan

### M3 — Submission endpoint + first signal
- **Provide:** the Architecture section (diagram and narrative), the API Contract for `/submit` and `/log`, Signal 1 of Detection Signals, and the Data Model.
- **Ask for:**
  - a Flask app skeleton with the `POST /submit` route stub (input validation, hardcoded response first) and `GET /log`
  - `signals/llm.py` with `llm_signal(text) -> {"score", "reasoning"}`, using Groq JSON mode and failure handling
  - `db.py` with both tables
- **Verify:**
  - Call `llm_signal()` directly in a REPL on the AI and human test inputs and confirm it returns a float in [0, 1] with reasoning. Confirm a bad API key yields `score=None` rather than a crash.
  - Check that the function signature matches the spec.
  - curl `/submit` and confirm `content_id` is present, then confirm `/log` shows a structured entry.

### M4 — Second signal + confidence scoring
- **Provide:** Detection Signals (both signals and their exact normalization formulas), Confidence Scoring & Uncertainty (weights, override rules, thresholds), and the diagram.
- **Ask for:** `signals/stylometry.py` with `stylometric_signal(text)`, `scoring.py` with `combine(...)`, and `scripts/calibrate.py`.
- **Verify:**
  - Read the generated scoring code line by line against the threshold table (0.80 / 0.35), the 0.45 disagreement rule, the 50-word rule, and the 0.6 / 0.4 weights. AI tools often quietly substitute symmetric 0.5 cutoffs.
  - Run the calibration script on the six inputs. Check that the AI and human texts are far apart, and that each signal on its own behaves sensibly.
  - Confirm the audit log now stores both signal scores.

### M5 — Production layer
- **Provide:** Transparency Labels (exact text), Appeals Workflow, the API Contract for `/appeal`, `/appeals`, and `/content`, Rate Limiting, and the diagram.
- **Ask for:** `labels.py` with `make_label(attribution, status)`, the `POST /appeal` endpoint, `GET /appeals`, `GET /content/<id>`, and the Flask-Limiter setup.
- **Verify:**
  - Diff the generated label strings against this document word for word.
  - Submit inputs that reach all three variants.
  - Appeal a real `content_id` and confirm `/content/<id>` shows `under_review` and `/log` shows an `appeal_filed` event with the reasoning. Also check the 404, 403, and 409 cases.
  - Run the 12-request loop and confirm 10×200 followed by 2×429.

---

## Stretch Features

*To be planned here before starting any of them.*

Candidates:
- **Ensemble:** a third signal, a lexicon of phrases AI text overuses, with documented weights.
- **Analytics dashboard:** label distribution, appeal rate, and average signal disagreement.

---

## Change Log

- **M3 — LLM model swap.** `meta-llama/llama-4-scout-17b-16e-instruct` returned `NotFoundError`: Groq has retired it. Switched to `openai/gpt-oss-120b`, the strongest general model still available on the account. It is overridable with the `GROQ_MODEL` env var. Standalone results on the four test inputs: clear AI 0.85, clear human 0.15, and both borderline cases 0.55, so the signal already separates the two ends and hedges in the middle as the prompt asks.
- **M3 — Port.** The dev server defaults to port **5001** (overridable with `PORT`), because macOS's AirPlay Receiver occupies 5000 and answers with a `403 AirTunes` response.
