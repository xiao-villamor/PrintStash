# Architecture

PrintStash uses a React/Vite frontend and a FastAPI backend. The default image
runs both behind one web port and persists data in SQLite and local storage.

For repository layout, capability boundaries, and supported process
topologies, see the [backend guide](https://github.com/xiao-villamor/PrintStash/blob/main/backend/README.md),
[repository guide](https://github.com/xiao-villamor/PrintStash/blob/main/AGENTS.md),
and [deployment reference](https://github.com/xiao-villamor/PrintStash/blob/main/docs/deployment.md).
The [domain glossary](https://github.com/xiao-villamor/PrintStash/blob/main/CONTEXT.md)
defines Models, Artifacts, Revisions, and other terms used throughout the code.
