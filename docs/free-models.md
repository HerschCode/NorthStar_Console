# Free models: two keys, one chain

The platform's language-model calls (`/v1/ask`, `/v1/investigations`, `/v1/briefing`, the model fallback of `/v1/nl-filter`) need
**only two keys**, both free:

| Key | Where | Used for |
|---|---|---|
| `GEMINI_API_KEY` (alias `GOOGLE_API_KEY`) | Google AI Studio → API keys | `gemini-2.5-flash`, `gemini-2.5-flash-lite` |
| `GROQ_API_KEY` | Groq console → API keys | `openai/gpt-oss-120b`, `openai/gpt-oss-20b` |

Set either or both. With neither the app still works: answers are labelled deterministic templates and nothing breaks. An Anthropic
key is optional and not required by anything; Ollama is an optional local, free, weaker model.

**Confirm it once, with your own keys:** `python -m scripts.check_free_models --probe` lists the models each key can call today,
flags names in `config/free_models.yaml` that your key cannot use (Groq retires models; this project's earlier default was retired),
and makes one tiny call per model, printing the limits Groq reports for your account.

## How the chain behaves
Models are tried in the order of `config/free_models.yaml`. A model is skipped when
1. it is **cooling down**: the provider answered 429 and told us when it can answer again (`retry-after`, Gemini's `retryDelay`; a
   per-day quota waits until the daily reset, midnight Pacific for Gemini, not the 23 s a header may suggest), or
2. our **soft cap** says the next call would exceed a limit you configured (`rpm`, `rpd`, `tpm`, at 85% of the cap), or
3. the key **cannot call that model name** (remembered for 6 hours, so a retired model costs nothing after the first miss).

The answer says which model wrote it (`groq:openai/gpt-oss-120b`), and a `notes` line says what was skipped and why. When **every**
model is limited the answer is a labelled template, and the response carries a structured `limits` object:

```json
{"exhausted": true, "retry_after_s": 9, "models": [
  {"provider": "gemini", "model": "gemini-2.5-flash", "scope": "day", "retry_after_s": 41520, "message": "Gemini (Google AI Studio free tier) · gemini-2.5-flash: per-day request limit reached — try again in 11.5 h"},
  {"provider": "groq",   "model": "openai/gpt-oss-120b", "scope": "tokens", "retry_after_s": 9, "message": "Groq (free plan) · openai/gpt-oss-120b: per-minute token limit reached — try again in 9 s"}]}
```
Scopes: `minute`, `day`, `tokens` (per minute), `tokens_day`, `request_size` (the request alone is larger than the model's token limit),
`unknown`. `GET /v1/limits` (P2) and `GET /v1/limits` (gateway, which adds its own daily allowance) show every model's state, usage this
minute / today and cooldown. The console shows the same messages in the Ask panel, the alert bar and Observability.

## What is and is not known about the limits
- Google: limits are per **project** (not per key) and per model, in RPM / TPM / RPD; daily quotas reset at **midnight Pacific**;
  the numbers for your project are in AI Studio. The docs do not list free-tier numbers.
- Groq: RPM / RPD / TPM / TPD per model; a 429 includes `retry-after`; `x-ratelimit-limit-requests` is the **daily** request quota and
  `x-ratelimit-limit-tokens` the **per-minute** token quota. The docs point to the console for your numbers.
- So `rpm / rpd / tpm` in `config/free_models.yaml` are **yours to fill**; until you do, only the provider's 429 and a conservative
  8-requests-per-minute self-throttle apply. Nothing in this repo claims a provider's free numbers.

## Gateway allowance (a second, friendlier guard)
The gateway counts AI questions per demo identity and overall per day (`GATEWAY_AI_DAILY_PER_USER` = 40, `GATEWAY_AI_DAILY_GLOBAL` = 400,
0 disables) and answers `429` in its own words before a public demo can exhaust the shared free quota. The rule parser handles
natural-language filters exactly, so the model is asked only when the rules find nothing.

## Google Cloud, ready but off
- `GEMINI_BACKEND=vertex` + `GOOGLE_CLOUD_PROJECT` + `GOOGLE_CLOUD_LOCATION`: the same Gemini code path through Vertex AI with
  application-default credentials (on Cloud Run, the service account; no key). No free tier: the SpendGuard counts cost when the model is
  priced in `config/pricing.yaml`. **Not exercised** (no project or credentials yet); the request shape and bearer-token handling are unit-tested.
- `P2_USAGE_BACKEND=firestore`: usage counters and cooldowns in Firestore (`FirestoreUsageStore`), because Cloud Run's disk is ephemeral.
  Unit-tested against a fake client only.
- Put the two keys in Secret Manager (one reader: the assistant's service account) and inject them as `GEMINI_API_KEY` / `GROQ_API_KEY`.

## Data note
On Google's free tier, prompts may be used to improve their products (check the current terms). Everything sent here is the public BPI 2019
sample, synthetic policy text and the user's own questions: do not point it at non-public data on a free key.
