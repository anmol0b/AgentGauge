# Provider responses for replay tests

`tests/test_recorded_responses.py` feeds these through the real SDKs' response
types and the providers' own parsing, so a change in what a provider sends —
or in how we read it — fails offline instead of on someone's bill.

Two kinds, and the difference matters:

- **`gemini/` — recorded.** Real responses from Google's OpenAI-compatible
  endpoint, captured 2026-10-21 with `curl` and during the first live runs.
  Only `thought_signature` values are shortened; they are opaque. These are
  evidence of what the endpoint actually returns.
- **`openai/`, `anthropic/` — schema-shaped.** Built from the response types
  in the pinned `openai` and `anthropic` SDKs (which are generated from each
  API's specification), with token counts chosen to make the arithmetic
  checkable. They prove the parsing matches the published shape, **not** that
  a live server sends it: neither provider has been run against its live API.

Replace a schema-shaped file with a recorded one whenever a real response is
available, and say so here.
