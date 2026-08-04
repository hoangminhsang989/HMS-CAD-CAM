# CODEX AI SYNC INSTRUCTIONS

At every verified checkpoint, Codex must update:

- `.ai/CURRENT_STATUS.md`
- `.ai/NEXT_TASK.md`
- `.ai/SESSION.json`
- `.ai/METRICS.json`
- `.ai/HANDOFF/TO_CHATGPT.md`

Codex must also create a dated checkpoint file in:

- `.ai/CHECKPOINTS/`

Required checkpoint content:

- Completed work
- Files changed
- Exact test commands
- Exact test results
- Remaining work
- Blocking issues
- Current branch
- Commit hash, if committed
- Recommended next action

Rules:

- Use only verified facts.
- Mark unknown values as unknown or null.
- Never invent test results, progress, commits, or completion.
- Do not commit or push unless the user or current task explicitly authorizes it.
- Do not stage unrelated working-tree changes.