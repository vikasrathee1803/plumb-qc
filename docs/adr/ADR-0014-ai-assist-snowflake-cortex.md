# ADR-0014: AI assist runs in-database via Snowflake Cortex

Date: 2026-06-08. Status: accepted. Supersedes ADR-0008 (renumbered from a duplicate ADR-0013).

The assist layer was a Groq-first, multi-provider client over external LLM
SDKs (openai, google-generativeai, with a lazy anthropic path). We do not
have those providers; the only model access we have is Snowflake Cortex.

Decision: the assist layer's single completion path is Snowflake Cortex,
called through the same read-only session the checks already use:

    SELECT SNOWFLAKE.CORTEX.COMPLETE(?, ?) AS response

Consequences:

- No external LLM SDK or API key. openai, google-generativeai, and httpx
  leave the runtime dependencies (httpx moves to dev for the test client).
  The bundle and the portable build shrink accordingly.
- The read-only invariant holds for free: a Cortex call is a single SELECT,
  so it passes the existing read-only guard, and no data leaves Snowflake.
- Cortex needs a live session, so assist is unavailable on a static-only
  run. The web and CLI now explain while the session is still open.

Placeholder: this is wired but off by default while Cortex access is set up.
Enable it with PLUMB_CORTEX_MODEL (for example "llama3.1-70b") on a live run.
get_client() returns None when it is disabled or static-only, so every
surface degrades gracefully, exactly as before.

Invariants unchanged: the assist layer runs only on a decided result and
never sets a status, severity, or verdict. The completion callable stays
injectable, so the layer is fully tested offline without a warehouse.

Reversibility: cheap. The Cortex call is isolated in plumb/ai/client.py
behind the one complete() seam; adding another path later is local.
