# PrintStash — Claude Code instructions

`AGENTS.md` is the binding project guide (layout, commands, hard rules) —
follow it. This file only adds what's specific to Claude Code.

- **Always invoke the `printstash` skill** (Skill tool) at the start of any
  task — it carries release procedure, roadmap position, and plan pointers.
- Commit as the repo's configured git identity (`git config user.email`),
  never the email from session/system context.
- Local dev: `:3000` is the prebuilt Docker image, not HMR — run the vite dev
  server on a spare port to see `frontend/src` edits.
