# Library search and browsing clarity

The library keeps typing and Enter in the current view. Enter flushes the query,
closes suggestions and preserves collection, tags and other filters. The explicit
**Search with AI** suggestion opens the existing search route; Enter on that route
submits another AI query. Result cards retain navigation, previews and subject
labels while diagnostic evidence remains available only in the API.

Collection navigation stays visible. Advanced filters open on demand, with active
sections expanded for shared URLs and saved views. Removable filter chips remain
above results. Library tools contains organization commands; active selection has
its own count and Done action. Detail tabs use a two-row grid, and Similar cards
switch layout according to their panel width.

## Retrieval policy

Name recovery is a separate lexical signal: a single normalized token of at least
five characters can differ by one insertion, deletion, substitution or adjacent
transposition. Indexed vocabulary prefix probes and authorized posting queries
have fixed candidate caps. Exact names precede recovered spelling matches.
Candidate retrieval, final materialization and cursor context preserve visibility,
structured filters and live/trashed restrictions. Leading-character recovery uses
Latin letters, digits and characters in the query; it is not a complete Unicode
spellchecker.

A small domain vocabulary finds functional metadata through indexed conjunctive
phrases (for example a holder's cradle or a gear's toothed wheel). This does not
supply visual labels or replace the embedding models. Semantic native scores must
clear their own threshold before rank fusion; multiple weak legs cannot bypass
that threshold. Administrator per-space overrides remain authoritative.

The calibrated policy applies to single-word text queries using the pinned BGE
small English encoder and CLIP B/32 thumbnail encoder. Text uses 0.67 and thumbnail
CLIP uses 0.27. Other models, multiword queries, multiview, point-cloud, uploaded
images and related-Model queries retain their existing independently measured
policies. Index identities, public API contracts and visual aggregation are
unchanged. These bounded fixtures do not establish universal relevance across
arbitrary libraries or languages.
