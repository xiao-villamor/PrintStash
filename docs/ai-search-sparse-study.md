# Optional lexical expansion (W13)

SPLADE is an opt-in, local, index-time expansion profile. It does not call a chat
server and does not infer the query. Original Passages, FTS5 rows and PostgreSQL
BM25 postings remain unchanged. Generated words never replace the text shown to
a user.

## Pinned model

The initial catalog entry is
[prithivida/Splade_PP_en_v1](https://huggingface.co/prithivida/Splade_PP_en_v1/tree/762be6a7206e2f299182705972a65e5c46e62be2),
an independent Apache-2.0 implementation of SPLADE. This English profile is a
masked-language model with document expansion, not a generative model. The
[official SPLADE implementation](https://github.com/naver/splade) describes the
underlying sparse retrieval method.

| Contract | Pinned value |
|---|---|
| Repository revision | `762be6a7206e2f299182705972a65e5c46e62be2` |
| Manifest identity | `61075e44331ef757c2e84e3db7c3b1752a1c104c8350ff57982b49fe23322c80` |
| ONNX graph | opset 14, 532,126,852 bytes |
| Graph SHA-256 | `0934583a27a031a66b2e847cbc260fbbef29689e969f500436460ef5146a43f2` |
| Tokenizer | 711,649 bytes, vocabulary 30,522 |
| Tokenizer SHA-256 | `2fc687b11de0bc1b3d8348f92e3b49ef1089a621506c7661fbf3248fcd54947e` |
| Input | One document, at most 16,384 characters / 512 model tokens |
| Pooling | Maximum over attention-masked `log(1 + relu(logit))` |
| Materialization | At most 64 whole-word terms, descending weight / term tie-break; positive continuous weights capped at 10 |

The manifest includes tensor names, graph/tokenizer digests, pooling, vocabulary,
term selection and a real-model canary. Changing any of these changes the
identity. Sparse vocabulary coordinates cannot be used as a dense EmbeddingSpace.
The shared acquisition path requires explicit download permission, verifies the
files and runs a contained canary before atomic cache publication. Preplaced
files work offline. No model is downloaded at startup or during a search.

## Ranking and lifecycle

`search_expansions` records the Passage input hash, immutable model/recipe
identity, lease, attempts and truncation. `search_expansion_terms` stores weights
separately. Foreign keys remove both when a Passage is purged. Only canonical
Passages with live, authorized contributors are materialized. Each unit claims
one Passage, releases its transaction before inference and rechecks the actor,
configuration, input hash and lease before publication. Three failed attempts
quarantine that input; changed content creates fresh work. Work uses the same
local compute slots, resident-worker memory budget and index capacity authority
as the other consumers.

Authorized original lexical results receive `1 / (60 + rank)`. The expansion
field receives at most `0.25 / (60 + rank)`, further multiplied by the sum of
matching weights divided by `10 × distinct query terms`. The two contributions
are added per Passage before Subject aggregation. Continuous weights are never
approximated by repeating tokens. SQLite materializes FTS5 scores before the
window/aggregate operation; PostgreSQL uses the same expansion formula beside
its original BM25 scorer. The LIKE fallback can also use durable expansion.

The configuration flag is read at query time. Disabling expansion immediately
removes its ranking contribution; retained original indexes keep serving during
backfill or model changes. Stale hashes, incompatible recipes and unfinished
work cannot contribute. Query cursors bind the expansion settings. Missing
native model files do not prevent queries over already materialized terms.

The Settings → AI Search → Advanced settings panel offers the sparse catalog,
explicit download and a separate expansion toggle. Installing a model alone does
not turn the feature on. Turning local inference off also clears the UI's
expansion opt-in.

## Measured cost and quality

Measured 2026-09-12 on this x86_64 VM, one ONNX CPU thread, with the existing
32-model engineering corpus and 32 relevant queries plus two outside-domain
queries. These fixtures were written for engineering regression testing; they
are **not independent human acceptance judgments**. The frozen continuous
weights are in `backend/tests/fixtures/search/splade-pp-en-v1-terms.json` and bind
the exact corpus and query-file digests.

| Measurement | Observed |
|---|---:|
| Original lexical table/index pages | 135,168 bytes |
| Additional sparse table/index pages | 147,456 bytes |
| Combined / original footprint | **2.09×** |
| Materialized terms | 2,048 (64 per Passage) |
| Whole backfill, including cold start | 22.19 seconds |
| First document, cold worker | 9.30 seconds |
| Subsequent document median | 0.409 seconds |
| Peak child RSS | 1,710,808 KiB |
| Original recall@5 | 28/32 (87.5%) |
| Expanded recall@5 | 28/32 (87.5%) |
| Relevant queries improved / regressed | 0 / 0 |

SQLite `dbstat` page totals count the original lexical owner separately from the
sparse tables and their indexes; unrelated library tables and model-cache files
are excluded. The approximately 508 MiB model download is additional. The
issue's 3–5× size estimate is not a measured guarantee: this small corpus measured
2.09×, and larger libraries or different term caps need fresh measurements.

The profile can retrieve a concrete expansion-only synonym (for example `bike`
from a Passage containing `bicycle`) without a query-time model call. This
corpus showed no aggregate recall improvement. Both outside-domain queries
still returned five results after expansion; this profile does not establish
reliable abstention or Spanish retrieval. It remains optional.

To reproduce the native measurement, provision the exact files and run the
explicit `tests/integration/modules/inference/preplaced_sparse.py` lane with
`AI_SEARCH_SPARSE_ASSETS` set to its manifest directory. The ordinary suite
replays the frozen real weights through the real lexical readers. Separate
contained-ONNX, HTTPS acquisition, populated migration, PostgreSQL and HTTP e2e
checks cover the runtime contracts.

The browser study uses `PLAYWRIGHT_AI_SEARCH_SPARSE_STUDY=1` with
`PLAYWRIGHT_AI_SEARCH_MODEL_DIR` pointing at those same preplaced files, then
runs `pnpm exec playwright test --config playwright.ai-search.config.ts`. This
explicit lane does not download assets; ordinary browser CI continues to use
its original tiny ONNX contract fixtures.
