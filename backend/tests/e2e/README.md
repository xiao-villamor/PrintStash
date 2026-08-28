# `tests/e2e` — the whole app, one flow at a time

The real FastAPI app over `httpx.ASGITransport`, against the fakes in `tests/fakes/`.
Nothing is mocked. The single `monkeypatch` in this tier's `conftest.py` — relaxing
`is_public_ip` so loopback counts as public — is the ceiling, not a precedent.

**One file per flow**, named for the flow (`test_ingest.py`, `test_browser_capture.py`),
not for a module. These are the only tests that prove the pieces are actually connected,
which is the failure no unit or integration test can see.

Every new feature gets **one** e2e test for its headline capability (AGENTS.md rule 4).
One — the rest of its coverage belongs at a cheaper tier. An e2e suite that grows a test
per edge case becomes the slowest and flakiest thing in the repo, and stops being run.
