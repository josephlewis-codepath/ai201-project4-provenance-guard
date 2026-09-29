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
{
  "appeal_id": "uuid4",
  "content_id": "...",
  "status": "under_review",
  "original_decision": { "attribution": "likely_ai", "confidence": 0.817 },
  "label": { "variant": "ai", "title": "...", "text": "... The creator has appealed this result; it is under review." },
  "message": "Your appeal has been received and will be reviewed by a person."
}
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
- Only scores at or above **0.75** ("strong, consistent evidence"), with the LLM itself leaning AI, earn the AI label.

### Combination

```
combined = 0.6 · llm_score + 0.4 · stylo_score
```
The LLM gets more weight because it uses far more information. Stylometry keeps it honest and supplies the disagreement check.

### Override rules (applied in order)

1. **LLM unavailable:** `combined = stylo_score`, and the attribution is forced to `uncertain`. We never accuse someone based on heuristics alone.
2. **Short text** (< 30 words): the attribution is forced to `uncertain` and the `short_text` flag is set. There is too little text to judge.
3. **Signal disagreement** (`|llm_score − stylo_score| > 0.45`): the attribution is forced to `uncertain` and the `signal_disagreement` flag is set.
4. Otherwise, apply the thresholds below.
5. **LLM floor for AI verdicts:** if the thresholds give `likely_ai` but `llm_score < 0.70`, the attribution becomes `uncertain` and the `weak_llm_evidence` flag is set. Stylometry can support an AI verdict but can never produce one on its own.

### Thresholds (deliberately lopsided)

| Combined score | Attribution | Label variant |
|---|---|---|
| **≥ 0.75** (and llm ≥ 0.70) | `likely_ai` | High-confidence AI |
| **0.36 – 0.74** | `uncertain` | Uncertain |
| **≤ 0.35** | `likely_human` | High-confidence human |

**Why lopsided:** The AI band requires a score 0.25 above the midpoint, plus the LLM floor, while the human band needs only 0.15 below it. A false "AI" label can damage a real writer's reputation. A false "human" label is a less harmful miss, and it can still be corrected through reports and review. Most of the scale deliberately lands in "uncertain."

### Validation plan (M4)
Run `scripts/calibrate.py` over the four provided test inputs plus at least two of my own (a repetitive poem and a non-native formal paragraph). Print both signals and the combined score for each. The system passes when:
- The clearly AI text reaches ≥ 0.75.
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
2. **A non-native English speaker's formal prose.** Careful, textbook-style grammar with few contractions and uniform sentences. Both signals may lean AI, which is the worst-case false positive. *Mitigation:* the high 0.75 bar and the LLM floor, the uncertain label's wording ("many human writers get this result"), and the appeal path. This is the scenario the appeal test uses.
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
  - Read the generated scoring code line by line against the threshold table (0.75 / 0.35), the 0.70 LLM floor, the 0.45 disagreement rule, the 30-word rule, and the 0.6 / 0.4 weights. AI tools often quietly substitute symmetric 0.5 cutoffs.
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

Planned after M5 and before any stretch code was written.

### S1 — Ensemble detection (three signals, weighted)

**Signal 3 — AI-phrase lexicon** (`signals/lexicon.py`, pure Python)
- **What it measures:** How densely the text uses words and phrases that assistant-style LLM output overuses. The list is fixed and curated (about 40 entries), for example:
  - hedges and transitions: "it is important to note", "furthermore", "moreover", "additionally", "ultimately", "in conclusion"
  - stock vocabulary: "delve", "tapestry", "realm", "landscape", "paradigm", "leverage", "stakeholders", "foster", "empower", "seamless", "robust", "crucial", "plays a vital role", "in today's fast-paced"

  Matching is case-insensitive on whole words.
- **Why it's distinct:** Stylometry measures structure (length, rhythm, punctuation) and the LLM makes a holistic judgment. The lexicon measures specific word *choice*, which is transparent, explainable, and independent of sentence shape. A reviewer can see exactly which phrases fired.
- **Output:**
  ```json
  { "score": 0.0-1.0, "hits": ["furthermore", "it is important to note"], "hits_per_100_words": 4.6 }
  ```
  `score = clamp(hits_per_100_words / 3.0)`, so 3 or more stock phrases per 100 words gives 1.0 and none gives 0.0.
- **Blind spots:** It is trivially evaded by paraphrasing or by asking the model to avoid those words. Human business, academic, and corporate writing uses "furthermore" and "stakeholders" legitimately. The list goes stale as model habits shift.

**Ensemble weighting (replaces the two-signal formula):**
```
combined = 0.5 · llm + 0.3 · stylometry + 0.2 · lexicon
```
The LLM keeps the largest share as the richest signal. Stylometry stays second. The lexicon gets the least weight because it is the easiest to game and the most prone to false positives on formal human prose.

**Rules stay the same:** the thresholds (0.75 / 0.35), the 30-word rule, the LLM floor (0.70), and the disagreement rule (`|llm − stylometry| > 0.45`) carry over unchanged. The LLM floor matters even more now: two heuristic signals together can't produce an AI verdict. If the LLM is unavailable, `combined = 0.6 · stylometry + 0.4 · lexicon`, still forced to `uncertain`.

**Storage:** `lexicon_score` and `lexicon_hits` are added to `submissions`, and `lexicon_score` to `audit_log`. `signals_used` includes `"lexicon"`. Existing databases are migrated with `ALTER TABLE ADD COLUMN`.

**Verification:** Re-run `scripts/calibrate.py` with a lexicon column and confirm:
- the AI samples stay ≥ 0.75
- the human sample stays ≤ 0.35
- the formal human paragraph and the edge cases stay `uncertain`

### S2 — Analytics dashboard

- **`GET /stats`** (JSON) and **`GET /dashboard`** (a server-rendered HTML page with no JavaScript, reading the same numbers).
- **Detection patterns:** the count and share of each attribution (`likely_ai` / `uncertain` / `likely_human`), a histogram of confidence scores in bands (0–0.35, 0.35–0.5, 0.5–0.75, 0.75–1), and how often each flag fires.
- **Appeal rate:** overall (appealed ÷ submissions) and **per attribution**. A high appeal rate on `likely_ai` would be the first warning sign of false positives.
- **Additional metric — signal agreement:** the mean `|llm − stylometry|` and the share of submissions flagged `signal_disagreement`. Rising disagreement means the signals are drifting apart and the thresholds need recalibration.
- **Verification:** seed a fresh database with the test inputs plus one appeal, then hand-check the counts, the rates, and the mean disagreement against `/log`.

---

## Change Log

- **M3 — LLM model swap.** `meta-llama/llama-4-scout-17b-16e-instruct` returned `NotFoundError`: Groq has retired it. Switched to `openai/gpt-oss-120b`, the strongest general model still available on the account. It is overridable with the `GROQ_MODEL` env var. Standalone results on the four test inputs: clear AI 0.85, clear human 0.15, and both borderline cases 0.55, so the signal already separates the two ends and hedges in the middle as the prompt asks.
- **M3 — Port.** The dev server defaults to port **5001** (overridable with `PORT`), because macOS's AirPlay Receiver occupies 5000 and answers with a `403 AirTunes` response.
- **M4 — Calibration changes** (made after running `scripts/calibrate.py`; the original values are listed for the record):

  | input | words | llm | stylo | combined | attribution |
  |---|---|---|---|---|---|
  | clear_ai | 43 | 0.86 | 0.77 | 0.823 | likely_ai |
  | clear_ai_long | 69 | 0.82 | 0.94 | 0.867 | likely_ai |
  | clear_human | 55 | 0.15 | 0.11 | 0.135 | likely_human |
  | borderline_formal_human | 43 | 0.55 | 0.96 | 0.714 | uncertain |
  | borderline_edited_ai | 39 | 0.55 | 0.55 | 0.548 | uncertain |
  | edge_repetitive_poem | 56 | 0.35 | 0.75 | 0.510 | uncertain |
  | edge_non_native_formal | 69 | 0.60 | 0.66 | 0.624 | uncertain |

  - *Short-text cutoff 50 → 30 words.* Three of the four provided test inputs are 39–43 words, so at 50 nothing on a paragraph-length post could ever get a definite label. 30 still catches haiku and captions.
  - *AI threshold 0.80 → 0.75.* Repeated runs showed the LLM varies by about ±0.02 even at temperature 0 (clear_ai scored 0.82–0.86). That put the clear AI sample at 0.799–0.823, right on the 0.80 line, so the same text flip-flopped between labels. A threshold should not sit inside the noise band of the canonical example. The asymmetry is kept: 0.25 above the midpoint vs. 0.15 below.
  - *New LLM floor rule (llm ≥ 0.70 for an AI verdict).* With the lower bar, the formal human paragraph (0.714, driven by stylometry's 0.96) would sit only 0.04 from an AI label. Stylometry's blind spot for formal prose is exactly what the spec predicted, so it may support an AI verdict but not produce one.
  - *Stylometry confirmed its blind spots:* formal human prose 0.96, repetitive poem 0.75, and the non-native writer 0.66 are all AI-leaning. In every case the LLM, the disagreement rule, or the LLM floor kept them at `uncertain`.
- **M5 — Appeals details.**
  - `appeal_id` is stored on both `submissions` and the `appeal_filed` audit event, so an appeal can be traced from the response to the log. The original data model only returned the ID without storing it.
  - The `/appeal` response also echoes `original_decision` and the updated label, so the client can show the creator exactly what they contested.
  - The status change is a single conditional `UPDATE … WHERE status = 'classified'`, so two simultaneous appeals can't both succeed. The second gets a 409.
- **M5 — Rate-limit evidence.** With `10 per minute;100 per day`, 12 rapid requests returned ten `200`s then two `429`s (`docs/evidence/rate_limit_test.txt`). The 429 body is JSON (`{"error": "rate limit exceeded", "limit": "10 per 1 minute"}`) instead of Flask-Limiter's default HTML page.
