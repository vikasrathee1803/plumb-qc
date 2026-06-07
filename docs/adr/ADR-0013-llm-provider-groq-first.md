# ADR-0013: AI assist is Groq-first, multi-provider

Date: 2026-06-08. Status: accepted. Supersedes the Anthropic-only client
in ADR notes for the Phase 2 assist layer.

The assist layer originally wrapped the Anthropic SDK. Direction changed
to use Groq. Decision: make the client provider-agnostic, choosing the
first key present in this order: Groq (OpenAI-compatible,
https://api.groq.com/openai/v1, default llama-3.3-70b-versatile), then
xAI Grok (OpenAI-compatible), then Google Gemini, then Anthropic. This
mirrors the convention in the job-assistant llm.py the request pointed to,
and means Plumb "uses Groq" whenever GROQ_API_KEY is set, with graceful
fallbacks otherwise.

Shipped SDKs: openai (Groq and xAI use it) and google-generativeai
(Gemini). anthropic is no longer a shipped dependency; its code path
remains behind a lazy import for anyone who installs it.

Invariants unchanged: the assist layer runs only on a decided result and
never sets a status. The completion callable stays injectable, so the
layer is fully tested offline regardless of provider.

Model override: PLUMB_AI_MODEL. Keys come from env or the OS keychain,
never the repo.

Reversibility: cheap. Provider selection and the per-provider calls are
isolated in plumb/ai/client.py behind one complete() seam.
