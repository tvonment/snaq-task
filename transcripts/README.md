# Transcripts

Raw VS Code Copilot Chat session exports for this build, generated via
the `Chat: Export...` command. Six JSON files, ~580k lines total,
covering — roughly in order — initial architecture review, logging
polish, critical project review, repeatability/golden questions, the
stability sweep, and a final whole-project review.

These are the receipts. The artefact written *for* a reader is
[NARRATIVE.md](../NARRATIVE.md) — a curated retrospective of where the
AI helped, where it pulled toward bad ideas, and where I overruled it.
Read that first; come here only if you want to verify a specific claim.

The Claude scoping conversation that started the project (the
over-engineered React + MCP + FastAPI plan I later walked back) is not
in this folder — it lives at the share link in
[NARRATIVE.md §1](../NARRATIVE.md).

## Schema

Each `chat-N.json` has the VS Code Copilot export shape:

```jsonc
{
  "responderUsername": "GitHub Copilot",
  "initialLocation": "panel",
  "requests": [ /* one entry per user turn */ ]
}
```

## Redactions

API keys that ended up in chat (pasted from `.env` while debugging)
have been replaced with `REDACTED_AZURE_OPENAI_API_KEY` /
`REDACTED_USDA_API_KEY`. The original keys have been rotated.
