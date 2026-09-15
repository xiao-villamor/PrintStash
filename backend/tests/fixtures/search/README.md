# Text retrieval regression corpus

Original CC0 fixture descriptions and relevance labels authored during issue #166 implementation, before running BGE against them. This is an engineering regression corpus, **not a human-labelled user study**. A stakeholder-provided human query set remains an additional acceptance input.

The 32 fictional printable designs include opaque names, related distractors, ordinary keyword queries, paraphrases, Spanish cross-language stress cases, typos, and two out-of-domain queries. Spanish is a stress case for the explicitly English BGE baseline, not a supported-language claim. Out-of-domain queries are evaluated separately for false-positive behavior; they do not have a relevant Subject for recall.

Vectors must be generated offline using the exact registry model and passage recipe; ordinary CI never downloads weights. Do not change labels after measuring a result. Permission coverage uses the separate actual authorization integration suite.

SPLADE continuous term weights in `splade-pp-en-v1-terms.json` are measured with
the pinned Apache-2.0 export documented in `docs/ai-search-sparse-study.md`. They
bind the same original CC0 corpus and query hashes. They are neither generated
user text nor an independent human relevance study.
