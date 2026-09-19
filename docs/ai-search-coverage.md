# AI Search implementation coverage

Work in progress for #166, based on the [owner’s independent plan](https://gist.github.com/xiao-villamor/e4daf5562e6a0819c4b7ce3a915bc19c) and [jorgehermo9’s attachment](https://gist.github.com/jorgehermo9/0b348e4411c0b455be7964a1de5f588c). One branch and one eventual PR. No AI Search availability claim yet.

The branch implements W1–W15, including W4b and W14. The matrix below retains
original acceptance rows alongside detailed stage evidence. Unresolved rows are
acceptance work, including scale/hardware measurements and independent human/photo
quality evaluation; implementation alone does not mark them complete.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| A001 | renders a passage with every configured field in template order | Happy | model with name, description, tags, collection | returned text matches the golden template | Unit | ✅ `core/search/test_passages.py::TestRenderPassages::test_renders_every_recipe_field_in_stable_order` |
| A002 | chunks a document body at the token cap with overlap | Edge | body above the cap | N passages, contiguous, overlap preserved | Unit | ✅ `core/search/test_passages.py::TestRenderPassages::test_chunks_a_document_body_at_the_token_cap_with_overlap` |
| A003 | caps the number of chunks for an oversized document | Edge | body far above the cap | chunk count == cap; no error | Unit | ✅ `core/search/test_passages.py::TestRenderPassages::test_caps_the_number_of_chunks_for_an_oversized_document` |
| A004 | changes the content hash when an indexed field changes | Happy | model description edited | `content_hash` differs; stale vectors deleted | Integration | ✅ `integration/modules/search/test_passages.py::TestSyncSubject::test_invalidates_vectors_when_indexed_content_changes` |
| A005 | leaves the content hash untouched for a non-indexed edit | Edge | `updated_at` bumped, fields equal | hash unchanged; no re-embed enqueued | Integration | ✅ `integration/modules/search/test_passages.py::TestSyncSubject::test_preserves_vectors_for_nonindexed_edits` |
| A006 | removes passages when a subject is trashed | Edge | model trashed | no passages; no vectors; not returned by search | Integration | ✅ `integration/modules/search/test_passages.py::TestSyncSubject::test_removes_every_recipe_for_a_trashed_subject` |
| A007 | restores passages when a subject is restored | Edge | trashed model restored | passages exist again; searchable | Integration | ✅ `integration/modules/search/test_passages.py::TestSyncSubject::test_restores_passages_for_a_restored_subject` |
| A008 | re-derives a passage the mutation seam missed | Error | row updated bypassing the seam | watermark sweep repairs it | Integration | ✅ `integration/modules/search/test_reconciliation.py::TestReconciliation::test_repairs_a_body_change_without_a_timestamp` |
| A009 | ranks an exact title match above a body mention | Happy | two models, FTS5 | ordering asserted | Integration | ✅ `integration/modules/search/test_lexical_query.py::TestLexicalQuery::test_ranks_exact_title_above_body` |
| A010 | ranks the controlled BM25 fixture consistently on PostgreSQL | Happy | Mismo tokenizer, corpus y pesos; postgres marker | Orden de referencia BM25, no ts_rank etiquetado como BM25 | Integration | ✅ `integration/postgres/test_search_passages.py::TestSearchPassages::test_ranks_postgres_with_real_bm25` |
| A011 | falls back to ranked LIKE when FTS is unavailable | Error | probe forced to fail | results still returned; capability reports the fallback | Integration | ✅ `integration/modules/search/test_lexical_query.py::TestLexicalQuery::test_falls_back_when_fts_is_unavailable` |
| A012 | prepares a generation at its index dimension | Happy | Space nativa 1024; MRL index 128 | Tabla derivada de 128; floats durables de 1024 | Integration | ✅ `integration/modules/search/test_generations.py::TestPrepare::test_preserves_native_floats_when_switching_to_reviewed_mrl` |
| A013 | drops the typed table when a generation is retired | Happy | retired generation | table gone; durable vectors deleted in batches | Integration | ✅ `integration/modules/search/test_vector_index.py::TestVectorIndex::test_retirement_preserves_full_float_rows`; `test_generations.py::TestPruneOne::test_prunes_vectors_in_bounded_batches` (table removal, then retention-aware bounded float pruning) |
| A014 | keeps autogenerate empty while a generation is live | Edge | live generation, `alembic revision --autogenerate` | empty diff | Integration | ✅ `integration/modules/search/test_vector_index.py::TestVectorIndex::test_excludes_registered_vector_objects` |
| A015 | preserves vectors across SQLite→PostgreSQL migration | Happy | seeded generation | same vectors, index rebuilt, no re-embed | Integration | ✅ `integration/modules/administration/test_database_transfer.py::TestDatabaseTransfer::test_copies_sqlite_to_postgres_without_inference` |
| A016 | serves search after a backup restore without a reindex | Happy | backup, wipe, restore | identical results | E2E | ✅ `e2e/test_postgres_backup.py::TestPostgresBackup::test_restores_searchable_documents_through_the_api` |
| A017 | returns brute-force results when the vector extension is missing | Error | extension probe fails | same top-k as the native path | Integration | ✅ `integration/postgres/test_vector_index.py::TestPostgresVectorIndex::test_restores_without_pgvector` |
| A018 | embeds a batch through the remote provider | Happy | contract-enforcing fake endpoint over loopback | returned vectors match inputs and declared dimension; persistence covered separately | Contract | ✅ `contract/modules/inference/test_remote.py::TestRemoteEmbeddingProvider::test_embeds_ordered_text_batches` |
| A019 | refuses a remote dimension mismatch | Error | endpoint returns 512 for a 384 space | generation fails with a stable code; active untouched | Contract | ✅ `contract/modules/inference/test_remote.py::TestRemoteEmbeddingProvider::test_rejects_invalid_embedding_outputs`; `integration/modules/search/test_indexing.py::TestIndexProcessor::test_keeps_active_ready_when_the_replacement_probe_fails[dimension]` |
| A020 | degrades to lexical when the endpoint times out | Error | endpoint hangs | 200 with `legs: ["lexical"]`; no 5xx | Integration | ✅ `integration/modules/search/test_retrieval.py::TestSearch::test_returns_lexical_results_by_query_deadline` — real HTTP 200, lexical-only result while provider remains blocked |
| A021 | retries a 429 with backoff | Error | endpoint returns 429 then 200 | batch completes; attempt count recorded | Contract | ✅ `contract/modules/inference/test_remote.py::TestRemoteEmbeddingProvider::test_retries_bounded_rate_limits` |
| A022 | never logs the endpoint API key | Error | provider error path | key absent from job status, logs and response | Integration | ✅ `contract/modules/inference/test_remote.py::TestRemoteEmbeddingProvider::test_redacts_failed_request_secrets`; durable indexing errors remain stable codes in `test_indexing.py` |
| A023 | resumes a backfill after a process restart | Edge | killed mid-backfill | resumes from the last committed page | E2E | ✅ `e2e/test_search_generations.py::TestSearchGenerationLifecycle::test_resumes_committed_work_after_process_loss` |
| A024 | cancels a backfill between batches | Happy | cancel requested | terminal state; active generation untouched | Integration | ✅ `integration/modules/search/test_generations.py::TestCancel::test_cancels_durable_work` |
| A025 | quarantines a repeatedly failing unit | Error | passage that always throws | quarantined after N attempts; worker continues | Integration | ✅ `integration/modules/search/test_indexing.py::TestIndexProcessor::test_quarantines_poison_inputs_without_repeating_healthy_work` |
| A026 | writes new ingests into a building generation | Edge | model ingested mid-backfill | vector present in the building generation | Integration | ✅ `integration/modules/search/test_indexing.py::TestIndexProcessor::test_indexes_content_added_during_backfill` |
| A027 | keeps serving the old generation during a backfill | Happy | backfill in progress | results come from the active generation | Integration | ✅ `integration/modules/search/test_retrieval.py::TestSearch::test_preserves_an_inflight_generation`; `e2e/test_search_independence.py` continuously queries during both rebuilds |
| A028 | activates atomically | Happy | ready generation | one transaction flips both rows; never two active | Integration | ✅ `integration/modules/search/test_generations.py::TestActivate::test_replaces_the_old_active_atomically` |
| A029 | refuses activation when verification fails | Error | Smoke query inválida | building/verify_failed o failed; active preservada | Integration | ✅ `integration/modules/search/test_indexing.py::TestIndexProcessor::test_keeps_active_ready_when_the_replacement_probe_fails` |
| A030 | refuses a second concurrent generation for one modality | Edge | two starts | second rejected | Integration | ✅ `integration/postgres/test_search_generations.py::TestPrepare::test_fences_concurrent_proposals` |
| A031 | fuses two legs by weighted RRF | Happy | known rank lists | expected fused order | Unit | ✅ `packages/printstash-core/tests/search/test_fusion.py::TestFuse::test_fuses_ranked_subjects` |
| A032 | drops results below the similarity floor | Edge | out-of-domain query | empty "no strong matches", not nearest neighbours | Integration | ✅ `integration/modules/search/test_retrieval.py::TestSearch::test_rejects_weak_dense_neighbors` |
| A033 | excludes trashed subjects from fused results | Edge | trashed model with a vector | absent | Integration | ✅ `integration/modules/search/test_retrieval.py::TestSearch::test_excludes_trashed_subjects` |
| A034 | applies visibility through browse authorization | Error | User sin acceso a vecinos principales | Solo resultados autorizados tras refetch bounded; página puede ser corta | Integration | ✅ `integration/modules/search/test_retrieval.py::TestSearch::test_rechecks_permissions_after_inference`; ✅ `integration/modules/search/test_vector_index.py::TestVectorIndex::test_queries_only_authorized_native_units` |
| A035 | rejects search from a share-link context | Error | share token | 403; no retrieval performed | Integration | ✅ `integration/api/v1/test_search.py::TestSearch::test_rejects_share_context_search` |
| A036 | returns per-result match evidence | Happy | hybrid query | each result names its leg and field | Integration | ✅ `integration/modules/search/test_retrieval.py::TestSearch::test_retrieves_semantic_matches` |
| A037 | verifies a downloaded model digest before use | Error | tampered file | discarded; stable error; nothing installed | Contract | ✅ `contract/modules/inference/test_model_acquisition.py::TestAcquisition::test_rejects_invalid_downloads[corrupt]` |
| A038 | never downloads without the opt-in | Error | download disabled | no egress attempted | Integration | ✅ `integration/api/v1/test_inference_models.py::TestInferenceModels::test_never_downloads_without_acquisition_opt_in` |
| A039 | uses a pre-placed model without network access | Happy | files in the cache dir | loads; no request made | Integration | ✅ `integration/modules/inference/test_model_cache.py::TestModelCache::test_discovers_preplaced_models_offline` |
| A040 | refuses to prune a model referenced by a live generation | Edge | delete request | 409; file retained | Integration | ✅ `integration/modules/inference/test_model_cache.py::TestModelCache::test_refuses_pruning_referenced_models` |
| A041 | prunes the least recently used model above the cache cap | Edge | cache over cap | unreferenced LRU removed | Integration | ✅ `integration/modules/inference/test_model_cache.py::TestModelCache::test_prunes_unreferenced_models_by_lru` |
| A042 | reports the local provider unavailable in lite | Edge | import probe fails | capability false; remote still offered | Integration | ✅ `integration/api/v1/test_inference_models.py::TestInferenceModels::test_reports_local_unavailable_while_offering_remote` |
| A043 | embeds a multiview subject into per-view vectors | Happy | mesh, `multiview` | N vectors at distinct `unit_index` | Integration | ✅ `integration/modules/search/test_visual_index.py::TestVisualIndex::test_reuses_native_views_after_an_aggregation_change` — six durable views with distinct unit keys |
| A044 | retrieves by max-sim over views | Happy | subject matching one view only | retrieved | Integration | ✅ `core/inference/test_vectors.py::TestCosineNeighbors::test_keeps_best_unit_per_subject`; real max-recipe replay in `integration/modules/search/retrieval/test_visual_quality.py` |
| A045 | falls back to the thumbnail profile when rendering is unavailable | Error | render capability off | profile degrades with a visible reason | Integration | ✅ `integration/modules/search/test_visual_index.py::TestVisualIndex::test_prepares_a_reusable_thumbnail_fallback` |
| A046 | embeds an uploaded query image without storing it | Error | image query | results returned; nothing written to storage | Integration | ✅ `integration/modules/search/test_visual_index.py::TestVisualQuery::test_searches_uploaded_images_without_temporary_files_or_query_cache` |
| A047 | rejects an oversized or non-image query upload | Error | 50 MB / a zip | 413/415; no embedding attempted | Integration | ✅ `integration/api/v1/test_search.py::TestSearch::test_rejects_unsupported_image_uploads_before_retrieval` — 50 MiB declaration and ZIP rejected before retrieval |
| A048 | keeps a machine caption out of the user description field | Edge | caption generated | `description` unchanged; caption in its own row | Integration | ✅ `integration/modules/search/test_captions.py::TestCaptions::test_keeps_generated_text_separate_from_human_description` |
| A049 | stops regenerating a dismissed caption | Edge | caption dismissed | not regenerated on the next pass | Integration | ✅ `integration/modules/search/test_captions.py::TestCaptions::test_keeps_dismissal_when_the_endpoint_changes` |
| A050 | reaches labelled semantic retrieval quality | Happy | Modelo real preplaced o corpus vectorial real versionado | recall@5 hybrid >=0.9 y lexical >=0.6; critical | Integration | ✅ `integration/modules/search/retrieval/test_text_quality.py::TestTextQuality::test_measures_real_text_retrieval_quality` |
| A051 | reaches visual recall on stripped descriptions | Happy | Real visual model and independently labelled held-out corpus with stripped descriptions | recall@10 >=0.7; critical | Integration | ❌ external evidence missing — frozen engineering/photo studies do not substitute for independent human labels |
| A052 | switches the model end to end with no search downtime | Happy | seeded library, model change | search answers throughout; new results after the flip (`critical`) | E2E | ✅ `e2e/test_search_generations.py::TestSearchGenerationLifecycle::test_serves_continuous_readers_during_a_transform_switch[model]` — distinct loopback endpoint/model, 4→8 dimensions; continuous HTTP readers before and after activation |
| A053 | keeps ingestion latency flat under backfill load | Edge | ingest during resumed backfill over a cloned 10,020-passage library | All 40 ingests complete; same payloads; indexed count 88→104; p95 227→233 ms (1.025× ≤1.25×) | E2E | ✅ measured prepared-fixture comparison; raw observations in `backend/tests/fixtures/search/ingest-backfill-10k-x86-priority.json`; earlier fresh-install failures and protocol limits retained in the performance report |
| A054 | exposes exactly one provider seam in the tree | Edge | architecture check | no second ONNX session manager, cache or vector store | Repo / E2E | ✅ `repo/test_architecture.py::TestArchitecture::test_keeps_native_sessions_in_the_inference_owner`; shared provider/store consumer: IC001–IC008 |
| A055 | audit-logs an embedding configuration change | Happy | settings patched | audit row with actor and change | Integration | ✅ `integration/api/v1/test_inference.py::TestUpdateSettings::test_audits_search_policy_changes` |
| A056 | shows the remote-egress disclosure whenever a remote modality is on | Happy | remote configured | notice present, names the host | Playwright | ✅ `frontend/tests/e2e-real/ai-search/nl-filters.spec.ts` — real configured loopback chat host disclosed before personal opt-in; remote embedding disclosure covered by Search UI tests |
| A057 | finds a just-uploaded model by name immediately | Happy | upload then search | found by lexical before any embedding | Playwright | ✅ `frontend/tests/e2e-real/ai-search/nl-filters.spec.ts` — just-uploaded Model appears in instant suggestions with lexical-only legs and no generation |
| A058 | returns schema-valid output from a json_schema endpoint | Happy | fake chat endpoint, schema dialect | parsed object matches the schema | Contract | ✅ `contract/modules/inference/test_chat.py::TestRemoteChatProvider::test_returns_a_locally_validated_object` |
| A059 | falls back to parse-and-repair without schema support | Error | probe reports no schema, no tools | usable result; reduced guarantee reported | Contract | ✅ `contract/modules/inference/test_chat.py::TestRemoteChatProvider::test_repairs_json_once` |
| A060 | probes the Responses API dialect without assuming it | Edge | endpoint lacking it | detected absent; chat-completions used | Contract | ✅ `contract/modules/inference/test_chat.py::TestRemoteChatProvider::test_probes_responses_without_assuming_availability` |
| A061 | never logs the chat API key | Error | chat provider error path | key absent from logs, status and response | Integration | ✅ `contract/modules/inference/test_chat.py::TestRemoteChatProvider::test_redacts_chat_credentials_after_failure`; caption retry/status contract in `test_captions.py` |
| A062 | parses natural language into typed filters | Happy | benchy printed last month under 3 hours | Filtros de fecha/duración/outcome válidos más residual benchy | Integration | ✅ `integration/modules/search/test_parsing.py::TestParse::test_normalizes_real_duration_with_absolute_calendar_bounds` |
| A063 | rejects a parsed field outside the filter vocabulary | Error | provider emits an unknown field | discarded; query still runs | Integration | ✅ `integration/modules/search/test_parsing.py::TestParse::test_rejects_untrusted_filters` |
| A064 | keeps natural-language parsing off by default | Error | chat endpoint configured, switch untouched | no parse attempted; plain hybrid search | Integration | ✅ `integration/modules/search/test_parsing.py::TestParse::test_keeps_parsing_off_with_a_configured_chat_endpoint` |
| A065 | runs captions with natural-language parsing disabled | Edge | one generative use on, the other off | Caption generado sin invocar parsing de consultas | Integration | ✅ `integration/modules/search/test_captions.py::TestCaptions::test_keeps_generated_text_separate_from_human_description` |
| A066 | honours a per-user opt-out of query parsing | Error | user preference off, instance on | no parse for that user; others unaffected | Integration | ✅ `integration/modules/search/test_parsing.py::TestParse::test_requires_both_optins_before_egress[user]; TestPreferences::test_keeps_personal_preferences_isolated` |
| A067 | retains_all_four_subject_types | Happy | Model/Collection/Multipart/Document | Resultados discriminados y autorizados por owner | E2E | ✅ `e2e/test_search_independence.py::TestSearchIndependence::test_runs_without_related_feature_packages` |
| A068 | indexes_every_model_context_field | Happy | Nombre/desc/tags/path/files/Revision/provenance; contexto opcional solo si está registrado | Passage contiene field list completa y autorizada de su receta, sin requerir Family | Integration | ✅ `integration/modules/search/test_sources.py::TestProjectSubject` (fields, inherited tags, Revision notes and effective provenance); independent optional-package installation in A142 |
| A069 | indexes_binary_document_metadata_only | Edge | PDF con body no extraído | Search encuentra nombre/filename, no promete texto completo | Integration | ✅ `integration/modules/search/test_sources.py::TestProjectSubject::test_indexes_only_binary_document_metadata` |
| A070 | repairs_ancestor_context_changes | Edge | Rename/move Collection sin tocar Model.updated_at | Passages descendientes corregidos por watermark | Integration | ✅ `integration/modules/search/test_reconciliation.py::TestReconciliation::test_repairs_ancestor_context_without_touching_model_timestamp` — public rename and move with notification disconnected, then bounded repair |
| A071 | repairs_deleted_contributor_links | Edge | Borrado de relación sin seam | Sweep elimina contribución obsoleta | Integration | ✅ `integration/modules/search/test_projection.py::TestContentProjection::test_refreshes_removed_relationships` |
| A072 | retains_active_recipe_during_reindex | Edge | Nueva recipe mientras active antigua | Ambas recetas coherentes hasta flip | Integration | ✅ `integration/modules/search/test_captions.py::TestCaptions::test_upgrades_active_text_to_caption_recipe` |
| A073 | does_not_leak_hidden_contributor_text | Error | Multipart visible con Model Choice oculto; contributor opcional de prueba | Ni ranking ni snippets/vector accesible dependen de texto oculto | Integration | ✅ `integration/postgres/test_semantic.py::TestSearch::test_excludes_hidden_contributors_from_dense_evidence`; ✅ `integration/postgres/test_semantic.py::TestSearch::test_never_explains_a_hidden_keyword_match`; SQLite HTTP member-segment authorization in `integration/api/v1/test_search.py` |
| A074 | applies_permission_revocation_immediately | Error | Permiso cambia tras index/cache | Resultado y evidence ocultos en consulta siguiente | Integration | ✅ `integration/modules/search/test_retrieval.py::TestSearch::test_reauthorizes_cached_query_vectors` |
| A075 | rolls_back_lexical_projection_with_mutation | Error | Transacción de Model falla | Sin Passage/FTS huérfano | Integration | ✅ `integration/modules/search/test_projection.py::TestContentProjection::test_rolls_back_a_content_notification` |
| A076 | keeps_ingest_searchable_when_fts_fails | Error | FTS no disponible en commit | Texto durable encuentra upload vía ranked fallback | Integration | ✅ `e2e/test_search_passages.py::TestSearchPassageLifecycle::test_searches_all_public_subject_types[lost-fts5]` — real G-code ingest after native table loss, all four Subjects found through ranked fallback |
| A077 | escapes_lexical_query_syntax | Error | Comillas, operadores, %, _, unicode | Sin SQL/FTS injection ni 500 | Integration | ✅ `core/search/test_lexical.py::TestLexical::test_tokenizes_unicode_query_without_operators`; `integration/modules/search/test_lexical_query.py::TestLexicalQuery::test_escapes_like_wildcards` |
| A078 | updates_bm25_corpus_statistics | Edge | Create/edit/delete Passage; ambos motores | Ranking y df/longitudes coherentes | Integration | ✅ `integration/modules/search/test_lexical_query.py::TestLexicalQuery::test_maintains_statistics_through_the_content_lifecycle`; `integration/postgres/test_search_passages.py::TestSearchPassages::test_maintains_postgres_statistics_after_delete` now includes an edit |
| A079 | uses_native_float_vector_codec | Happy | Vector dimensionado nativo | Bytes LE roundtrip sin pérdida aparte float32 declarado | Unit | ✅ `core/inference/test_vectors.py::TestNormalize::test_encodes_little_endian_float32` |
| A080 | rejects_nonfinite_provider_vectors | Error | NaN/inf/cero no permitido/mala longitud | Código estable, no vector activo corrupto | Contract | ✅ `contract/modules/inference/test_remote.py::TestRemoteEmbeddingProvider::test_rejects_invalid_embedding_outputs` |
| A081 | distinguishes_artifact_component_units | Edge | Dos Artifacts del Model con component 0 | Dos unidades durables distintas | Integration | ✅ `integration/modules/similarity/test_vector_sources.py::TestNativeStore::test_indexes_distinct_component_inputs` — three distinct units across two Artifacts, including component 0 on each |
| A082 | rejects_cross_space_vector_comparison | Error | Misma dimension pero CLIP/BGE distintos | Comparación denegada, sin ranking inventado | Unit | ✅ `unit/modules/inference/test_remote.py::TestRemoteEmbeddingProvider::test_rejects_changed_request_spaces` — same-dimension foreign model/revision rejected |
| A083 | truncates_only_registry_approved_mrl_points | Error | Dimensión no soportada o modelo no MRL | Propuesta inválida; active intacta | Integration | ✅ `integration/modules/search/test_generations.py::TestPrepare::test_rejects_unreviewed_truncation` |
| A084 | normalizes_mrl_prefix | Happy | Prefix válido de vector nativo | Norma unitaria y dimensión elegida | Unit | ✅ `packages/printstash-core/tests/inference/test_transforms.py::TestIndexTransform::test_normalizes_an_approved_prefix` |
| A085 | roundtrips_int8_transform_metadata | Happy | Calibración/version fijas | Transformada reproducible al reconstruir índice | Unit | ✅ `packages/printstash-core/tests/inference/test_transforms.py::TestIndexTransform::test_roundtrips_a_versioned_int8_recipe` |
| A086 | rescales_quantized_shortlist_with_float_vectors | Happy | Index int8/binary; float source | Top-k dentro de tolerancia medida del baseline | Integration | ✅ `integration/modules/search/test_code_index.py::TestRankingRecall::test_measures_compressed_ranking_recall` |
| A087 | retains_native_vectors_after_truncation | Edge | Generación 128 de Space 1024 | Floats de 1024 siguen disponibles para rebuild | Integration | ✅ `integration/modules/search/test_generations.py::TestPrepare::test_preserves_native_floats_when_switching_to_reviewed_mrl` |
| A088 | switches_index_backend_without_embedding | Happy | NumPy↔sqlite-vec o pgvector↔NumPy | Flip continuo; ningún nuevo input recibido por fake provider | E2E | ✅ `e2e/test_search_generations.py::TestSearchGenerationLifecycle::test_serves_continuous_readers_during_a_transform_switch` |
| A089 | serves_queries_during_startup_rebuild | Edge | Restart con derivados ausentes | Búsqueda sirve antes de completar rebuild | E2E | ✅ `e2e/test_search_local.py::TestLocalSearch::test_keeps_search_available_during_restart_warmup` |
| A090 | preserves_inflight_generation_readers | Edge | Activate durante query antigua | Query completa con su Space; cleanup espera drain | Integration | ✅ `integration/modules/search/test_retrieval.py::TestSearch::test_preserves_an_inflight_generation` |
| A091 | refuses_insufficient_swap_capacity | Error | Cache/disco no admite old+new | 409/resource code; active utilizable | Integration | ✅ `integration/modules/search/test_generations.py::TestPrepare::test_rejects_capacity_overcommit` |
| A092 | rejects_stale_backfill_publication | Edge | Passage cambia durante inference | No vector viejo publicado como current | Integration | ✅ `integration/modules/search/test_indexing.py::TestIndexProcessor::test_rejects_source_edits_during_inference` |
| A093 | refuses_unexplained_quarantine_at_activation | Error | Fallos de inferencia pendientes al verify | No activa generación incompleta sin explicación | Integration | ✅ `integration/modules/search/test_indexing.py::TestIndexProcessor::test_quarantines_poison_inputs_without_repeating_healthy_work` |
| A094 | detects_unmanaged_schema_drift | Error | Tabla ajena termina en _data | Autogenerate la detecta; no exclusión genérica | Integration | ✅ `integration/modules/search/test_vector_index.py::TestVectorIndex::test_excludes_registered_vector_objects` |
| A095 | disables_sqlite_extension_loading_after_connect | Edge | Conexiones sync/async; carga falla | Load_extension deshabilitado al terminar hook | Integration | ✅ `integration/db/test_vector_extensions.py::TestVectorExtensions::test_disables_loading_after_async_connect` |
| A096 | degrades_when_pg_extension_cannot_be_created | Error | Disponible pero usuario sin permiso | NumPy fallback; startup no falla | Integration | ✅ `integration/postgres/test_vector_index.py::TestPostgresVectorIndex::test_degrades_when_pg_extension_cannot_be_created` |
| A097 | restores_without_vector_extension | Edge | Backup nativo restaurado sin extensión | Datos/vector durables consultables; rebuild background | E2E | ✅ `e2e/test_search_generations.py::TestSearchGenerationLifecycle::test_restores_native_search_without_the_extension` — public backup/restore, no vec0 function, original float bytes and semantic results retained |
| A098 | probes_chat_tool_calling_fallback | Happy | Endpoint tools sin json_schema | Objeto validado con guarantee reportada | Contract | ✅ `contract/modules/inference/test_chat.py::TestRemoteChatProvider::test_returns_a_locally_validated_object[vllm-tools]` |
| A099 | rejects_invalid_chat_schema_output | Error | Endpoint devuelve key extra/type erróneo | Nada ejecutado como filtro; fallback limpio | Contract | ✅ `contract/modules/inference/test_chat.py::TestRemoteChatProvider::test_rejects_schema_violations_from_a_real_endpoint` — extra property and wrong type across all three dialects; filter fallback in `test_parsing.py` |
| A100 | bounds_chat_parse_repair | Error | JSON inválido repetido | Se detiene en límite; no loop de llamadas | Contract | ✅ `contract/modules/inference/test_chat.py::TestRemoteChatProvider::test_rejects_a_failed_repair` |
| A101 | opens_circuit_after_provider_failures | Error | Endpoint devuelve fallos repetidos | Operaciones se degradan dentro del deadline | Contract | ✅ `contract/modules/inference/test_remote.py::TestRemoteEmbeddingProvider::test_opens_a_failed_endpoint_circuit` |
| A102 | requires_declared_image_embedding_contract | Error | Endpoint solo compatible texto | Imagen unavailable, no payload adivinado | Contract | ✅ `contract/modules/inference/test_remote.py::TestRemoteEmbeddingProvider::test_never_sends_undeclared_visual_modalities[image]` |
| A103 | separates_remote_modality_consent | Error | Texto consentido, visual/caption no | No imágenes recibidas por fake host | Contract | ✅ `contract/modules/inference/test_chat.py::TestRemoteChatProvider::test_keeps_text_chat_separate_from_image_permission`; caption instance-consent fences in `test_captions.py` |
| A104 | never_sends_raw_geometry_to_provider | Error | Consulta/componente de malla | Recorder contiene solo modalidades permitidas | Contract | ✅ `contract/modules/inference/test_remote.py::TestRemoteEmbeddingProvider::test_never_sends_undeclared_visual_modalities[point_cloud]`; bounded rendered-JPEG caption contract in `e2e/test_search_captions.py` |
| A105 | encrypts_sensitive_extra_headers | Error | Header Authorization personalizado | DB/readback/logs no contienen secreto claro | Integration | ✅ `integration/api/v1/test_inference.py::TestCreateEndpoint::test_keeps_inference_credentials_encrypted`; R012 covers wire-log redaction |
| A106 | hides_private_hosts_from_public_health | Error | Endpoint LAN configurado | Public health sin host privado; admin disclosure presente | Integration | ✅ `integration/api/v1/test_inference.py::TestReadSettings::test_discloses_hosts_without_credentials` — admin host disclosure plus public liveness response redaction |
| A107 | rejects_download_redirect_outside_policy | Error | HF/mirror redirige a host no permitido | Archivo no instalado y egress denegado | Contract | ✅ `contract/modules/inference/test_model_acquisition.py::TestAcquisition::test_rejects_invalid_downloads[redirect]` |
| A108 | caps_actual_download_bytes | Error | Content-Length engañoso | Descarga abortada y temp eliminado | Contract | ✅ `contract/modules/inference/test_model_acquisition.py::TestAcquisition::test_rejects_invalid_downloads[oversize]` |
| A109 | installs_model_files_atomically | Edge | Crash entre dos archivos de manifest | Cache no presenta modelo incompleto como usable | Contract | ✅ `contract/modules/inference/test_model_acquisition.py::TestAcquisition::test_recovers_an_abandoned_install` |
| A110 | rejects_model_cache_path_escape | Error | Manifest traversal/symlink | Sin escritura fuera de cache root | Integration | ✅ `integration/modules/inference/test_model_cache.py::TestModelCache::test_rejects_cache_path_escape` |
| A111 | refuses_custom_onnx_operators | Error | Custom graph necesita operator library | Capability rechazada antes de ejecución | Integration | ✅ `integration/modules/inference/local/test_text.py::TestLocalText::test_rejects_undeclared_onnx_graph_contracts` |
| A112 | pins_cache_during_concurrent_load | Edge | LRU prune corre durante load | Modelo en uso no borrado | Integration | ✅ `integration/modules/inference/test_model_cache.py::TestModelCache::test_protects_models_during_concurrent_load` |
| A113 | honours_shared_render_budget | Edge | Thumbnail y multiview concurrentes más consumidor de prueba del mismo permiso | Concurrencia/RSS global dentro del cap sin instalar Similar Models | Integration | ✅ `integration/modules/search/test_visual_index.py::TestVisualIndex::test_respects_an_independent_consumers_render_permit`; aggregate native RSS enforcement: `integration/modules/inference/test_worker_pool.py::TestSharedNativeBudget::test_external_renderer_evicts_idle_encoders_under_one_memory_budget`; package independence: IC001 |
| A114 | aggregates_normalized_multiview_mean | Happy | Vistas válidas con normas distintas | Vector agregado conforme receta | Unit | ✅ `core/search/test_visual_inputs.py::TestMeanPool::test_returns_a_normalized_mean_of_unit_views` |
| A115 | retrieves_printed_part_photo | Happy | Foto real fixture; Space compatible | Source Model en top10 | Integration | ✅ `integration/modules/search/retrieval/test_visual_quality.py::TestPrintedPhotoRanking::test_replays_printed_photo_rank`; original photo measured with pinned CLIP |
| A116 | rejects_image_decompression_bomb | Error | Pocos bytes, exceso de píxeles | 413/422 antes de tensor gigante | Integration | ✅ `integration/api/v1/test_search.py::TestSearch::test_rejects_unsupported_image_uploads_before_retrieval[pixels-413]` — tiny PNG header declares 65535-square image; retrieval never runs |
| A117 | strips_query_image_exif | Edge | Foto con GPS/orientation | Provider recibe imagen orientada sin EXIF | Contract | ✅ `core/inference/test_images.py::TestDecodeImage::test_returns_oriented_rgb_without_metadata`; visual provider accepts only decoded RGB; undeclared remote image wire input refused by A102 |
| A118 | avoids_query_upload_disk_spooling | Error | Multipart a través del proxy configurado | Sin archivo temporal persistido del upload | E2E | ✅ `e2e/test_search_image_proxy.py::TestSearchImageProxy::test_streams_image_requests_without_disk` — real nginx and read-only body directory |
| A119 | degrades_visual_only_to_ready_generation | Error | Pointcloud/multiview capability falla | Siguiente rung listo o texto con motivo real | Integration | ✅ `integration/modules/search/test_visual_index.py::TestVisualIndex::test_prepares_a_reusable_thumbnail_fallback`; `visual_index/test_point.py::test_preserves_thumbnail_results_when_point_encoding_fails` |
| A120 | preserves_edited_caption_on_worker_completion | Edge | Usuario edita durante llamada VLM | Caption humana editada no sobrescrita | Integration | ✅ `integration/modules/search/test_captions.py::TestCaptions::test_fences_a_late_completion[edit]` |
| A121 | removes_dismissed_caption_from_search | Edge | Dismiss de caption ya indexada | No contribuye a resultados posteriores | Integration | ✅ `integration/modules/search/test_captions.py::TestCaptions::test_keeps_one_lexical_recipe_during_semantic_coexistence` |
| A122 | keeps_caption_disabled_by_default | Error | Chat endpoint configurado | No generación sin switch caption | Integration | ✅ `integration/modules/search/test_captions.py::TestCaptions::test_defaults_off_with_a_configured_endpoint` |
| A123 | runs_nl_filters_with_caption_disabled | Happy | Solo NL habilitado | Parse funciona sin generar captions | Integration | ✅ `integration/modules/search/test_parsing.py::TestParse::test_normalizes_real_duration_with_absolute_calendar_bounds` |
| A124 | filters_one_qualifying_print_job | Edge | Un job cumple fecha; otro duración | Model excluido si ninguno cumple conjunto | Integration | ✅ `backend/tests/integration/modules/library/model_views/test_structured_filters.py::TestPrintHistoryFilters::test_requires_one_job_for_the_combined_history_predicates` |
| A125 | handles_print_date_timezone_boundaries | Edge | Mes pasado con DST y bordes de medianoche | Límites UTC de calendario correctos | Unit | ✅ `backend/tests/unit/modules/search/test_calendar.py::TestBounds::test_resolves_calendar_boundaries` |
| A126 | does_not_use_estimated_duration_as_actual | Edge | Slicer duration cumple; actual_duration_s null | Filtro de duración real no incluye el Model | Integration | ✅ `backend/tests/integration/modules/library/model_views/test_structured_filters.py::TestPrintHistoryFilters::test_uses_actual_duration_with_half_open_date_boundaries` |
| A127 | rejects_unauthorized_parsed_identifiers | Error | Chat emite Collection/printer no visible | Filtro descartado sin disclosure | Integration | ✅ `backend/tests/integration/modules/search/test_parsing.py::TestParse::test_rejects_untrusted_filters`; ✅ `backend/tests/integration/modules/search/test_parsing.py::TestParse::test_excludes_hidden_collection_context` |
| A128 | restores_nl_chips_from_canonical_url | Happy | Parse y recarga URL | Filtros y residual restaurados | Playwright | ✅ `frontend/tests/e2e-real/ai-search/nl-filters.spec.ts::persists editable filters without repeated parsing` |
| A129 | removes_one_nl_filter_chip | Happy | Quitar duración de parse | Solo esa restricción desaparece; no reparse automático | Playwright | ✅ `frontend/tests/e2e-real/ai-search/nl-filters.spec.ts::persists editable filters without repeated parsing` |
| A130 | saves_nl_filters_without_freezing_ranking | Happy | Guardar resultado en Saved View | Typed filters y residual persisten; ranking no | Playwright | ✅ `frontend/tests/e2e-real/ai-search/nl-filters.spec.ts::persists editable filters without repeated parsing` |
| A131 | avoids_query_text_in_access_logs | Error | Query normal y error por proxy/ASGI | q/prompt ausentes de logs y jobs | E2E | ✅ `backend/tests/e2e/test_search_query_logs.py::TestSearchQueryLogs::test_omits_queries_when_upstream_is_unavailable`; `integration/api/v1/test_search.py` asserts ASGI failure-log redaction |
| A132 | disables_all_ai_affordances_with_master | Edge | Master off con generations existentes | Lexical utilizable y UI sin controles AI activos | Playwright | ✅ `frontend/tests/e2e-real/ai-search/search.spec.ts` — master disabled after real BGE activation: AI submit hidden, image/camera disabled, natural-language preferences hidden, lexical Document still found |
| A133 | bounds_query_execution_deadline | Error | ONNX worker ocupado/endpoint lento | Respuesta léxica antes de deadline; event loop responde | Integration | ✅ `integration/modules/search/test_retrieval.py::TestSearch::test_returns_lexical_results_by_query_deadline` — concurrent health and search share one ASGI event loop |
| A134 | measures_query_p95_on_100k_passages | Happy | 4-core, Space default, corpus etiquetado | p95 end-to-end <300ms; el gate falla si supera objetivo | E2E | ❌ measured failure — portable p95 3.024 s; populated native p95 2.136 s with 100,000 durable/native rows verified before and after all 32 real local BGE HTTP queries; four-vCPU QEMU/KVM; 300 ms budget unchanged |
| A135 | measures_pi5_default_backfill_budget | Happy | Physical Pi 5, 100k Passages and default real encoder | Backfill <1h; gate fails above target | E2E | ❌ external evidence missing — physical Pi 5 unavailable in this workspace |
| A136 | versions_sparse_expansion_independently | Edge | Toggle/model de expansión cambia | Lexical original sigue disponible durante rebuild | Integration | ✅ `integration/modules/search/test_expansion_worker.py::TestExpansionProcessor::test_retains_original_search_during_backfill`; independent recipe/hash and late-result fences in the same suite |
| A137 | measures_sparse_expansion_cost | Happy | Corpus con expansión opt-in | Tamaño y recall comparados con lexical base | Integration | ✅ `integration/modules/inference/preplaced_sparse.py::TestPreplacedSparse::test_measures_the_pinned_sparse_profile` |
| A138 | localizes_ai_search_states | Edge | en/es; pending/degraded/empty/caption/NL | Texto traducido, foco y controles accesibles | Frontend unit | ✅ `frontend/src/pages/__tests__/search.test.tsx`, `frontend/src/components/__tests__/subject-caption.test.tsx`, `frontend/src/components/__tests__/search-preferences.test.tsx` — 31 tests pass; en/es, focus and Escape checked |
| A139 | roundtrips_search_schema_with_existing_data | Happy | Upgrade/downgrade/upgrade; SQLite/PostgreSQL poblados | Tablas/config nuevas reversibles; datos previos conservados | Integration | ✅ `integration/db/migrations/test_search_schema_migration.py::TestSearchSchemaMigration::test_roundtrips_populated_library` — real SQLite and PostgreSQL, four Subjects and settings |
| A140 | switches_visual_profile_independently | Happy | Text active mientras se cambia perfil visual | Text Generation intacta; visual flip propio | Integration | ✅ `integration/modules/search/test_visual_index.py::TestVisualIndex::test_visual_cutover_leaves_active_text_generation_unchanged` |
| A141 | measures_profile_rung_quality_gain | Happy | Mismo held-out corpus y modelo por recipe | Cada rung ofrecido mejora métrica sobre anterior; salida S1 documentada | Integration | ❌ measured limitation — frozen text-query recall@10: thumbnail 0.84375, matte 0.875, multiview mean 0.75; the real photo improves with multiview, but this does not establish improvement at every rung |
| A142 | delivers_search_without_related_features | Happy | Main con esta PR; modules similarity y families ausentes | Migración, upload, búsqueda y swap funcionan con los cuatro Subject types propios | E2E | ✅ `e2e/test_search_independence.py::TestSearchIndependence::test_runs_without_related_feature_packages[similarity-families]` |

`critical-capabilities.json` gains `ai-search-model-swap-no-downtime` (row 52), `ai-search-retrieval-quality` (rows 50–51) and `ai-search-disabled-degradation` (rows 11 and 20). Coverage floors are raised for both new modules in the same PR, per the two-sided floor rule.

## W1 subcontracts verified in this increment

The original acceptance rows above retain their full outcomes: rows requiring
automatic synchronization, vectors or retrieval remain missing even where the
internal persistence subcontracts below now pass. `core/` paths refer to
`backend/packages/printstash-core/tests/`; other paths refer to `backend/tests/`.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| P001 | accepts each supported subject | Happy | Each registered Subject type, id=1 | Typed Subject identity retained | Unit | ✅ `core/search/test_passages.py::TestSearchSubject::test_accepts_each_supported_subject` |
| P002 | rejects invalid subject ids | Error | Zero, negative, overflow, boolean, float or string ID | ValueError identifies invalid Subject ID | Unit | ✅ `core/search/test_passages.py::TestSearchSubject::test_rejects_invalid_subject_ids` |
| P003 | rejects an unsupported subject type | Error | Artifact used as Subject type | ValueError rejects unsupported type | Unit | ✅ `core/search/test_passages.py::TestSearchSubject::test_rejects_an_unsupported_subject_type` |
| P004 | canonicalizes conjunctive access dependencies | Happy | Repeated and reordered contributor Subjects | Canonical conjunctive JSON and identical segment digest | Unit | ✅ `core/search/test_passages.py::TestAccessIdentity::test_canonicalizes_conjunctive_access_dependencies` |
| P005 | distinguishes subject types with equal ids | Edge | Model and Collection share numeric ID | Different access segment identities | Unit | ✅ `core/search/test_passages.py::TestAccessIdentity::test_distinguishes_subject_types_with_equal_ids` |
| P006 | represents a subject only segment | Edge | No additional contributors | Empty dependency array; Subject access still required | Unit | ✅ `core/search/test_passages.py::TestAccessIdentity::test_represents_a_subject_only_segment` |
| P007 | renders every recipe field in stable order | Happy | Every recipe-v1 field populated | Exact labelled text in fixed field order | Unit | ✅ `core/search/test_passages.py::TestRenderPassages::test_renders_every_recipe_field_in_stable_order` |
| P008 | preserves unicode text | Edge | Accents, CJK, control characters and Markdown | NFC text and whitespace retained without NUL | Unit | ✅ `core/search/test_passages.py::TestRenderPassages::test_preserves_unicode_text` |
| P009 | omits empty fields | Edge | Title with absent optional metadata | No empty labels | Unit | ✅ `core/search/test_passages.py::TestRenderPassages::test_omits_empty_fields` |
| P010 | deduplicates repeated list values | Edge | Duplicate and blank tag values | Stable distinct nonempty values | Unit | ✅ `core/search/test_passages.py::TestRenderPassages::test_deduplicates_repeated_list_values` |
| P011 | chunks a document body at the token cap with overlap | Edge | 500-word Markdown body | Two windows with 40 recipe-token overlap and retained endpoints | Unit | ✅ `core/search/test_passages.py::TestRenderPassages::test_chunks_a_document_body_at_the_token_cap_with_overlap` |
| P012 | keeps a body at the token boundary in one passage | Edge | Exactly 400 recipe tokens including body label | One complete passage without truncation | Unit | ✅ `core/search/test_passages.py::TestRenderPassages::test_keeps_a_body_at_the_token_boundary_in_one_passage` |
| P013 | caps the number of chunks for an oversized document | Edge | Body exceeding 16 passage windows | Exactly 16 passages, all marked truncated | Unit | ✅ `core/search/test_passages.py::TestRenderPassages::test_caps_the_number_of_chunks_for_an_oversized_document` |
| P014 | reports bounded truncation | Edge | Oversized title, list, body, list entry or indivisible token | Explicit truncation and 16,384-character passage cap | Unit | ✅ `core/search/test_passages.py::TestRenderPassages::test_reports_bounded_truncation` |
| P015 | reserves body space when metadata fills its budget | Edge | Metadata exceeds half the passage budget | Body text survives; truncation reported | Unit | ✅ `core/search/test_passages.py::TestRenderPassages::test_reserves_body_space_when_metadata_fills_its_budget` |
| P016 | changes the hash when text changes | Happy | Title changes | Different deterministic content hash | Unit | ✅ `core/search/test_passages.py::TestRenderPassages::test_changes_the_hash_when_text_changes` |
| P017 | keeps identical text hashes stable | Edge | Equivalent normalized title text | Identical passages and hashes | Unit | ✅ `core/search/test_passages.py::TestRenderPassages::test_keeps_identical_text_hashes_stable` |
| P018 | accepts empty content | Edge | All fields empty | One empty, nontruncated passage | Unit | ✅ `core/search/test_passages.py::TestRenderPassages::test_accepts_empty_content` |
| P019 | persists model text | Happy | Model with description | Durable labelled text with Subject-only access segment | Integration | ✅ `integration/modules/search/test_passages.py::TestSyncSubject::test_persists_model_text` |
| P020 | preserves idempotent passage identity | Edge | Repeated identical projection | Same IDs, hashes and content watermark; no changes | Integration | ✅ `integration/modules/search/test_passages.py::TestSyncSubject::test_preserves_idempotent_passage_identity` |
| P021 | ignores nonindexed edits for content freshness | Edge | Only thumbnail and source timestamp change | Content identity unchanged; no content update | Integration | ✅ `integration/modules/search/test_passages.py::TestSyncSubject::test_ignores_nonindexed_edits_for_content_freshness` |
| P022 | replaces changed passage content | Happy | Model description edited | Stable ID with new text and content hash | Integration | ✅ `integration/modules/search/test_passages.py::TestSyncSubject::test_replaces_changed_passage_content` |
| P023 | removes obsolete passage windows | Edge | Long Document shortened | Surplus passage removed; first window replaced | Integration | ✅ `integration/modules/search/test_passages.py::TestSyncSubject::test_removes_obsolete_passage_windows` |
| P024 | preserves other live recipes | Edge | A different recipe remains live | Other recipe text preserved | Integration | ✅ `integration/modules/search/test_passages.py::TestSyncSubject::test_preserves_other_live_recipes` |
| P025 | removes every recipe for a trashed subject | Edge | Trashed Model with two stored recipes | Every recipe removed | Integration | ✅ `integration/modules/search/test_passages.py::TestSyncSubject::test_removes_every_recipe_for_a_trashed_subject` |
| P026 | removes passages for a purged subject | Edge | Model physically deleted | Orphan passage removed | Integration | ✅ `integration/modules/search/test_passages.py::TestSyncSubject::test_removes_passages_for_a_purged_subject` |
| P027 | restores passages for a restored subject | Edge | Trashed Model restored | Current recipe recreated | Integration | ✅ `integration/modules/search/test_passages.py::TestSyncSubject::test_restores_passages_for_a_restored_subject` |
| P028 | rolls projection back with content | Error | Content edit and projection followed by rollback | Neither edit survives | Integration | ✅ `integration/modules/search/test_passages.py::TestSyncSubject::test_rolls_projection_back_with_content` |
| P029 | does not create rows for a missing subject | Edge | Missing Model ID | No passage created | Integration | ✅ `integration/modules/search/test_passages.py::TestSyncSubject::test_does_not_create_rows_for_a_missing_subject` |
| P030 | projects model fields | Happy | Model with Collection and G-code Revision | Name, description, path, filename, Revision label and notes projected | Integration | ✅ `integration/modules/search/test_sources.py::TestProjectSubject::test_projects_model_fields` |
| P031 | uses effective tags | Happy | Direct, ancestor and Artifact tags; duplicate association | Sorted distinct effective tags | Integration | ✅ `integration/modules/search/test_sources.py::TestProjectSubject::test_uses_effective_tags` |
| P032 | excludes trashed artifact text | Edge | Trashed Artifact carries filename and notes | No Artifact text projected | Integration | ✅ `integration/modules/search/test_sources.py::TestProjectSubject::test_excludes_trashed_artifact_text` |
| P033 | ignores nonrevision notes | Edge | STL with legacy Revision notes | Non-Revision notes excluded | Integration | ✅ `integration/modules/search/test_sources.py::TestProjectSubject::test_ignores_nonrevision_notes` |
| P034 | honors provenance overrides | Happy | User overrides source title and description | Effective title, summary and source tags projected | Integration | ✅ `integration/modules/search/test_sources.py::TestProjectSubject::test_honors_provenance_overrides` |
| P035 | honors cleared provenance text | Edge | User clears captured title to null | Suppressed captured title excluded | Integration | ✅ `integration/modules/search/test_sources.py::TestProjectSubject::test_honors_cleared_provenance_text` |
| P036 | reports excess source tags | Edge | Source tag list exceeds recipe cap | Extraction truncation reported | Integration | ✅ `integration/modules/search/test_sources.py::TestProjectSubject::test_reports_excess_source_tags` |
| P037 | ignores nontext provenance tags | Error | Mixed-type tag list or non-list JSON | Only textual tags retained | Integration | ✅ `integration/modules/search/test_sources.py::TestProjectSubject::test_ignores_nontext_provenance_tags` |
| P038 | projects collection fields | Happy | Collection with Markdown readme | Name, path and description projected | Integration | ✅ `integration/modules/search/test_sources.py::TestProjectSubject::test_projects_collection_fields` |
| P039 | projects multipart parts | Happy | Multipart Model with named Multipart Part | Grouping metadata and Part name projected | Integration | ✅ `integration/modules/search/test_sources.py::TestProjectSubject::test_projects_multipart_parts` |
| P040 | segments multipart member context | Happy | Grouping references member in another Collection | Member name and label only in a Model-dependent segment | Integration | ✅ `integration/modules/search/test_sources.py::TestProjectSubject::test_segments_multipart_member_context` |
| P041 | projects markdown body | Happy | Markdown Document | Body retained; binary filename not projected | Integration | ✅ `integration/modules/search/test_sources.py::TestProjectSubject::test_projects_markdown_body` |
| P042 | indexes only binary document metadata | Edge | PDF or other binary with stray body field | Only name and filename projected; no body extraction | Integration | ✅ `integration/modules/search/test_sources.py::TestProjectSubject::test_indexes_only_binary_document_metadata` |
| P043 | excludes trashed subjects | Edge | Trashed Model, Collection or Document | No Subject projection | Integration | ✅ `integration/modules/search/test_sources.py::TestProjectSubject::test_excludes_trashed_subjects` |
| P044 | omits missing subjects | Edge | Missing Subject in each registered type | No Subject projection | Integration | ✅ `integration/modules/search/test_sources.py::TestProjectSubject::test_omits_missing_subjects` |
| P045 | excludes the external print sentinel | Edge | External-print sentinel Model | Sentinel excluded | Integration | ✅ `integration/modules/search/test_sources.py::TestProjectSubject::test_excludes_the_external_print_sentinel` |
| P046 | upgrades without losing library content | Happy | Previous SQLite schema with a real Model | Additive table upgrade retains library content | Integration | ✅ `integration/db/migrations/test_search_passages_migration.py::TestSearchPassagesMigration::test_upgrades_without_losing_library_content` |
| P047 | downgrades without losing library content | Happy | SQLite schema downgrade with a real Model | Only passage table removed | Integration | ✅ `integration/db/migrations/test_search_passages_migration.py::TestSearchPassagesMigration::test_downgrades_without_losing_library_content` |
| P048 | emits portable offline schema | Happy | SQLite and PostgreSQL offline DDL | Passage table and constraints rendered | Integration | ✅ `integration/db/migrations/test_search_passages_migration.py::TestSearchPassagesMigration::test_emits_portable_offline_schema` |
| P049 | projects existing models after upgrade | Happy | Existing PostgreSQL Model upgraded to passage schema | Durable Unicode passage after projection | Integration | ✅ `integration/postgres/test_search_passages.py::TestSearchPassages::test_projects_existing_models_after_upgrade` |
| P050 | rejects duplicate passage identity | Error | Duplicate identity on PostgreSQL | Unique constraint rejects duplicate; original retained | Integration | ✅ `integration/postgres/test_search_passages.py::TestSearchPassages::test_rejects_duplicate_passage_identity` |
| P051 | indexes a new document before commit returns | Happy | Document created through the real HTTP API | Current passage visible in a fresh database session without explicit indexing | E2E | ✅ `e2e/test_search_passages.py::TestSearchPassageLifecycle::test_indexes_a_new_document_before_commit_returns` |
| P052 | search passage builder preserves subject identity | Happy | Factory receives a real Model Subject | Persisted owner, access requirements and default text match | Integration | ✅ `repo/test_factories.py::TestGeneratedIdentities::test_search_passage_builder_preserves_subject_identity` |

## W1 transaction and reconciliation checks

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| P053 | projects an authorized model edit | Happy | Authorized Model rename | Passage updated before operation returns | Integration | ✅ `integration/modules/search/test_projection.py::TestContentProjection::test_projects_an_authorized_model_edit` |
| P054 | refreshes descendants after an ancestor tag change | Edge | Ancestor tag assignment | Descendant Model passage gains effective tag | Integration | ✅ `integration/modules/search/test_projection.py::TestContentProjection::test_refreshes_descendants_after_an_ancestor_tag_change` |
| P055 | refreshes removed relationships | Edge | Direct tag link removed | Stale tag removed from passage | Integration | ✅ `integration/modules/search/test_projection.py::TestContentProjection::test_refreshes_removed_relationships` |
| P056 | rolls back a content notification | Error | Content notification then rollback | Source projection restored atomically | Integration | ✅ `integration/modules/search/test_projection.py::TestContentProjection::test_rolls_back_a_content_notification` |
| P057 | leaves content usable without a projection | Edge | Optional projection unbound | Content commit succeeds without passages | Integration | ✅ `integration/modules/search/test_projection.py::TestContentProjection::test_leaves_content_usable_without_a_projection` |
| P058 | repairs a body change without a timestamp | Edge | Document changed outside mutation port | Partition sweep corrects text | Integration | ✅ `integration/modules/search/test_reconciliation.py::TestReconciliation::test_repairs_a_body_change_without_a_timestamp` |
| P059 | persists the partition cursor | Happy | First bounded page commits | Durable cursor names last visited Subject | Integration | ✅ `integration/modules/search/test_reconciliation.py::TestReconciliation::test_persists_the_partition_cursor` |
| P060 | removes a passage after an unobserved deletion | Edge | Subject removed without notification | Orphan passage removed | Integration | ✅ `integration/modules/search/test_reconciliation.py::TestReconciliation::test_removes_a_passage_after_an_unobserved_deletion` |
| P061 | limits work to the current partition | Edge | Four Subjects and page limit two | Only current two Subjects projected | Integration | ✅ `integration/modules/search/test_reconciliation.py::TestReconciliation::test_limits_work_to_the_current_partition` |
| P062 | reaches the next partition | Happy | Two successive bounded pages | All four Subjects eventually projected | Integration | ✅ `integration/modules/search/test_reconciliation.py::TestReconciliation::test_reaches_the_next_partition` |

## W1 content-owner lifecycle validation

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| P063 | indexes_a_new_document_before_commit_returns | Happy | create Markdown through the API | durable title and body passage | E2E | ⏭️ N/A — same headline lifecycle now covered by P051 |
| P064 | replaces_a_document_body_after_edit | Happy | edit a projected Markdown body | only current body remains | Integration | ✅ `integration/modules/search/projection/test_content_lifecycle.py::TestDocumentProjection::test_replaces_a_document_body_after_edit` |
| P065 | removes_a_trashed_document | Happy | trash a projected Document | no passage remains | Integration | ✅ `integration/modules/search/projection/test_content_lifecycle.py::TestDocumentProjection::test_removes_a_trashed_document` |
| P066 | reindexes_a_restored_document | Happy | restore a trashed Document | current passage returns | Integration | ✅ `integration/modules/search/projection/test_content_lifecycle.py::TestDocumentProjection::test_reindexes_a_restored_document` |
| P067 | removes_a_purged_document | Happy | permanently delete a projected Document | passages and dependencies removed | Integration | ✅ `integration/modules/search/projection/test_content_lifecycle.py::TestDocumentProjection::test_removes_a_purged_document` |
| P068 | indexes_an_uploaded_markdown_document | Happy | upload Markdown bytes | body searchable in durable passage | Integration | ✅ `integration/modules/search/projection/test_content_lifecycle.py::TestDocumentProjection::test_indexes_an_uploaded_markdown_document` |
| P069 | indexes_an_uploaded_binary_filename | Happy | upload PDF bytes | filename metadata indexed without binary content | Integration | ✅ `integration/modules/search/projection/test_content_lifecycle.py::TestDocumentProjection::test_indexes_an_uploaded_binary_filename` |
| P070 | refreshes_models_after_collection_rename | Happy | rename an ancestor Collection | descendant Model passage contains new path | Integration | ✅ `integration/modules/search/projection/test_content_lifecycle.py::TestTaxonomyProjection::test_refreshes_models_after_collection_rename` |
| P071 | replaces_collection_readme | Happy | edit Collection landing text | current README passage | Integration | ✅ `integration/modules/search/projection/test_content_lifecycle.py::TestTaxonomyProjection::test_replaces_collection_readme` |
| P072 | refreshes_models_after_collection_tags | Happy | replace inherited tags through API | Model passage contains replacement tag | Integration | ✅ `integration/modules/search/projection/test_content_lifecycle.py::TestTaxonomyProjection::test_refreshes_models_after_collection_tags` |
| P073 | removes_deleted_tag_text | Edge | delete a tag after projection | dependent Model omits deleted tag | Integration | ✅ `integration/modules/search/projection/test_content_lifecycle.py::TestTaxonomyProjection::test_removes_deleted_tag_text` |
| P074 | indexes_a_new_multipart_model | Happy | create a multipart aggregate through API | shared title passage | Integration | ✅ `integration/modules/search/projection/test_content_lifecycle.py::TestMultipartProjection::test_indexes_a_new_multipart_model` |
| P075 | refreshes_multipart_metadata | Happy | rename aggregate | current shared title passage | Integration | ✅ `integration/modules/search/projection/test_content_lifecycle.py::TestMultipartProjection::test_refreshes_multipart_metadata` |
| P076 | removes_a_deleted_multipart_model | Happy | delete aggregate | no aggregate passages | Integration | ✅ `integration/modules/search/projection/test_content_lifecycle.py::TestMultipartProjection::test_removes_a_deleted_multipart_model` |
| P077 | refreshes_batch_model_moves | Happy | move selected Models | current collection path | Integration | ✅ `integration/modules/search/projection/test_content_lifecycle.py::TestModelProjection::test_refreshes_batch_model_moves` |
| P078 | refreshes_batch_model_tags | Happy | replace selected Model tags | current effective tag text | Integration | ✅ `integration/modules/search/projection/test_content_lifecycle.py::TestModelProjection::test_refreshes_batch_model_tags` |
| P079 | removes_a_trashed_model | Happy | trash a projected Model | no Model passages | Integration | ✅ `integration/modules/search/projection/test_content_lifecycle.py::TestModelProjection::test_removes_a_trashed_model` |
| P080 | reindexes_a_restored_model | Happy | restore Model | current Model passage | Integration | ✅ `integration/modules/search/projection/test_content_lifecycle.py::TestModelProjection::test_reindexes_a_restored_model` |
| P081 | refreshes_revision_notes | Happy | edit Revision notes | current Revision text in Model passage | Integration | ✅ `integration/modules/search/projection/test_content_lifecycle.py::TestModelProjection::test_refreshes_revision_notes` |
| P082 | removes_a_trashed_revision_filename | Happy | trash a Revision | removed filename absent from Model passage | Integration | ✅ `integration/modules/search/projection/test_content_lifecycle.py::TestModelProjection::test_removes_a_trashed_revision_filename` |
| P083 | refreshes_artifact_tags | Happy | replace Artifact tags | Model effective tag text updated | Integration | ✅ `integration/modules/search/projection/test_content_lifecycle.py::TestModelProjection::test_refreshes_artifact_tags` |
| P084 | refreshes_provenance_override | Happy | edit captured title override | effective override appears in Model passage | Integration | ✅ `integration/modules/search/projection/test_content_lifecycle.py::TestModelProjection::test_refreshes_provenance_override` |
| P085 | indexes_ingested_artifact_metadata | Happy | persist a new Artifact | filename in durable Model passage | Integration | ✅ `integration/modules/search/projection/test_content_lifecycle.py::TestModelProjection::test_indexes_ingested_artifact_metadata` |
| P086 | omits_derived_passages_from_audit | Edge | content projection while audit installed | no duplicate text in audit derivative rows | Integration | ✅ `integration/modules/search/projection/test_content_lifecycle.py::TestModelProjection::test_omits_derived_passages_from_audit` |
| P087 | defers_projection_repair_during_restore | Edge | restore maintenance active | no repair checkpoint created | Integration | ✅ `integration/runtime/test_search.py::TestSearchRuntime::test_defers_projection_repair_during_restore` |
| P088 | commits_a_projection_repair_partition | Happy | repair existing Document in worker | fresh session sees passage | Integration | ✅ `integration/runtime/test_search.py::TestSearchRuntime::test_commits_a_projection_repair_partition` |
| P089 | cancellation_waits_for_projection_repair | Edge | cancel scheduler during admitted unit | unit finishes before cancellation completes | Integration | ✅ `integration/runtime/test_search.py::TestSearchRuntime::test_cancellation_waits_for_projection_repair` |
| P090 | preserves_content_when_projection_fails | Error | projection raises during Model edit | source edit and projection roll back | Integration | ✅ `integration/modules/search/test_projection.py::TestContentProjection::test_preserves_content_when_projection_fails` |
| P091 | rejects_an_invalid_repair_limit | Error | limit zero or above maximum | ValueError before writes | Integration | ✅ `integration/modules/search/test_reconciliation.py::TestReconciliation::test_rejects_an_invalid_repair_limit` |
| P092 | upgrades_projection_checkpoints_with_content | Happy | existing library at passage migration | upgrade preserves content and supports checkpoints | Integration | ✅ `integration/db/migrations/test_search_checkpoints_migration.py::TestSearchCheckpointsMigration::test_upgrades_projection_checkpoints_with_content` |
| P093 | downgrades_projection_checkpoints_with_content | Happy | library at checkpoint migration | downgrade preserves library and passage rows | Integration | ✅ `integration/db/migrations/test_search_checkpoints_migration.py::TestSearchCheckpointsMigration::test_downgrades_projection_checkpoints_with_content` |

## W2 lexical ranking and failure handling

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| L001 | tokenizes_unicode_query_without_operators | Edge | punctuation, accents, FTS operators | bounded literal tokens | Unit | ✅ `core/search/test_lexical.py::TestLexical::test_tokenizes_unicode_query_without_operators` |
| L002 | computes_reference_bm25 | Happy | fixed corpus counts and field frequencies | numeric BM25 reference | Unit | ✅ `core/search/test_lexical.py::TestLexical::test_computes_reference_bm25` |
| L003 | ranks_exact_title_above_body | Happy | identical word in title versus description | title Subject first | Integration | ✅ `integration/modules/search/test_lexical_query.py::TestLexicalQuery::test_ranks_exact_title_above_body` |
| L004 | searches_current_content_after_edit | Happy | already indexed Model renamed | new query matches; old does not | Integration | ✅ `integration/modules/search/test_lexical_query.py::TestLexicalQuery::test_searches_current_content_after_edit` |
| L005 | removes_deleted_content_from_native_index | Edge | indexed Subject trashed | old query returns no result | Integration | ✅ `integration/modules/search/test_lexical_query.py::TestLexicalQuery::test_removes_deleted_content_from_native_index` |
| L006 | rolls_back_lexical_edits | Error | source transaction rolled back | original matches preserved | Integration | ✅ `integration/modules/search/test_lexical_query.py::TestLexicalQuery::test_rolls_back_lexical_edits` |
| L007 | falls_back_when_fts_is_unavailable | Error | native capability absent | weighted LIKE returns result with fallback status | Integration | ✅ `integration/modules/search/test_lexical_query.py::TestLexicalQuery::test_falls_back_when_fts_is_unavailable` |
| L008 | preserves_content_when_native_update_fails | Error | FTS update fails | source and durable passage commit; LIKE finds new name | Integration | ✅ `integration/modules/search/test_lexical_query.py::TestLexicalQuery::test_preserves_content_when_native_update_fails` |
| L009 | repairs_native_index_in_bounded_pages | Edge | missing index over existing passages | cursor advances; queries work after rebuild | Integration | ✅ `integration/modules/search/test_lexical_query.py::TestLexicalQuery::test_repairs_native_index_in_bounded_pages` |
| L010 | ranks_postgres_with_real_bm25 | Happy | controlled corpus on PostgreSQL | reference ranking using term statistics | Integration | ✅ `integration/postgres/test_search_passages.py::TestSearchPassages::test_ranks_postgres_with_real_bm25` |
| L011 | maintains_postgres_statistics_after_delete | Edge | indexed passage deleted | df, corpus length and document count updated | Integration | ✅ `integration/postgres/test_search_passages.py::TestSearchPassages::test_maintains_postgres_statistics_after_delete` |
| L012 | searches_all_public_subject_types | Happy | Model, Collection, Multipart Model, Document | discriminated authorized results | E2E | ✅ `e2e/test_search_passages.py::TestSearchPassageLifecycle::test_searches_all_public_subject_types` |
| L013 | rejects_unauthenticated_search | Error | no credentials | 401 before retrieval | Integration | ✅ `integration/api/v1/test_search.py::TestSearch::test_rejects_unauthenticated_search` |
| L014 | rejects_share_context_search | Error | share token | denied before retrieval | Integration | ✅ `integration/api/v1/test_search.py::TestSearch::test_rejects_share_context_search` |
| L015 | hides_unauthorized_member_segments | Error | aggregate readable; member private | private member terms yield no aggregate result | Integration | ✅ `integration/api/v1/test_search.py::TestSearch::test_hides_unauthorized_member_segments` |
| L016 | applies_permission_revocation_immediately | Edge | access revoked after indexing | no restricted result or highlight | Integration | ✅ `integration/api/v1/test_search.py::TestSearch::test_applies_permission_revocation_immediately` |
| L017 | escapes_like_wildcards | Edge | literal percent and underscore query | no wildcard widening | Integration | ✅ `integration/modules/search/test_lexical_query.py::TestLexicalQuery::test_escapes_like_wildcards` |
| L018 | returns_bounded_plain_text_evidence | Edge | hostile HTML in indexed content | escaped-by-consumer text plus match ranges; no raw HTML | Integration | ✅ `integration/api/v1/test_search.py::TestSearch::test_returns_bounded_plain_text_evidence` |
| L019 | ranks_library_browse_through_read_port | Happy | exact title and body matches | Model browse order agrees with lexical relevance | Integration | ✅ `integration/modules/search/test_lexical_query.py::TestLexicalQuery::test_ranks_library_browse_through_read_port` |
| L020 | detects_unmanaged_schema_drift | Error | unrelated table resembling a shadow | autogenerate reports it | Integration | ✅ `integration/db/derived_objects/test_search_fts.py::TestSearchDerivedObjects::test_detects_unmanaged_schema_drift` |
| L021 | excludes_only_registered_fts_objects_from_autogenerate | Edge | populated native FTS index | no derivative drift | Integration | ✅ `integration/db/derived_objects/test_search_fts.py::TestSearchDerivedObjects::test_excludes_only_registered_fts_objects_from_autogenerate` |

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| P094 | removes_an_orphan_dependency_without_a_passage | Edge | source and passage removed outside owner | dependency inventory removed by bounded repair | Integration | ✅ `integration/modules/search/test_reconciliation.py::TestReconciliation::test_removes_an_orphan_dependency_without_a_passage` |
| L022 | hides_private_member_context_on_postgres | Error | readable aggregate with unreadable member on PostgreSQL | no private term match | Integration | ✅ `integration/postgres/test_search_passages.py::TestSearchPassages::test_hides_private_member_context_on_postgres` |
| L023 | paginates_ranked_library_browse | Happy | multiple ranked Model matches | no repeats, exact total, terminal cursor | Integration | ✅ `integration/modules/search/test_lexical_query.py::TestLexicalQuery::test_paginates_ranked_library_browse` |
| L024 | rejects_a_cursor_from_another_query | Error | signed cursor reused with different query | stable invalid-cursor error | Integration | ✅ `integration/api/v1/test_search.py::TestSearch::test_rejects_a_cursor_from_another_query` |
| L025 | paginates_without_repeating_a_subject | Happy | more Subjects than page size | each Subject once across pages | Integration | ✅ `integration/api/v1/test_search.py::TestSearch::test_paginates_without_repeating_a_subject` |
| L026 | browse_falls_back_after_native_table_loss | Error | native FTS table removed after initialization | ranked browse still returns current Model | Integration | ✅ `integration/modules/search/test_lexical_query.py::TestLexicalQuery::test_browse_falls_back_after_native_table_loss` |

## Shared vector platform

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| V001 | preserves_legacy_space_hash | Edge | persisted v1 CLIP contract | identical identity after adoption | Core | ✅ `core/inference/test_embedding.py::TestEmbeddingSpace::test_preserves_legacy_space_hash` |
| V002 | isolates_inference_identity | Error | equal dimensions, different aligned model/config | distinct immutable Space | Core | ✅ `core/inference/test_embedding.py::TestEmbeddingSpace::test_isolates_inference_identity` |
| V003 | keeps_subject_types_distinct | Edge | Model and Document share integer ID | two separate neighbors | Core | ✅ `core/inference/test_vectors.py::TestCosineNeighbors::test_keeps_subject_types_distinct` |
| V004 | publishes_a_document_without_similarity | Happy | plain Space, generation and Document passage | source-fenced native vector and authorized neighbor | Integration | ✅ `integration/modules/search/test_vector_store.py::TestVectorStore::test_publishes_a_document_without_similarity` |
| V005 | refuses_stale_source_publication | Edge | passage changes while embedding | old hash cannot publish | Integration | ✅ `integration/modules/search/test_vector_store.py::TestVectorStore::test_refuses_stale_source_publication` |
| V006 | preserves_legacy_vectors_on_upgrade | Edge | seeded pre-166 native blobs and unit keys | same bytes/IDs, typed Model mapping | Integration | ✅ `integration/db/migrations/test_search_vectors_migration.py::TestSearchVectorsMigration::test_preserves_legacy_vectors_on_upgrade` |
| V007 | excludes_registered_vector_objects | Edge | live native generation and unrelated similarly named table | only exact registered derivatives excluded | Integration | ✅ `integration/modules/search/test_vector_index.py::TestVectorIndex::test_excludes_registered_vector_objects` |
| V008 | restores_native_vectors_without_extension | Error | native database backup, extension absent on restore | portable full-float neighbors, no embedding call | Integration | ✅ `integration/modules/search/test_vector_index.py::TestVectorIndex::test_restores_native_vectors_without_extension` |
| V009 | copies_sqlite_to_postgres_without_inference | Happy | seeded current SQLite with library/vector data | same durable counts/hashes/IDs on empty PostgreSQL | Integration | ✅ `integration/modules/administration/test_database_transfer.py::TestDatabaseTransfer::test_copies_sqlite_to_postgres_without_inference` |
| V010 | refuses_nonempty_migration_target | Error | target already contains user data | refuses before any mutation | Integration | ✅ `integration/modules/administration/test_database_transfer.py::TestDatabaseTransfer::test_refuses_nonempty_migration_target` |
| V011 | previews_database_migration | Edge | dry-run against empty target | validated copy plan, target stays empty | Integration | ✅ `integration/modules/administration/test_database_transfer.py::TestDatabaseTransfer::test_previews_database_migration` |
| V012 | stages_vectors_without_committing_source | Edge | authorized CTE publication then rollback | no vector survives outside caller transaction | Integration | ✅ `integration/modules/search/test_vector_store.py::TestVectorStore::test_stages_vectors_without_committing_source` |
| V013 | queries_only_authorized_native_units | Error | hidden higher-rank native unit | only authorized vector enters result | Integration | ✅ `integration/modules/search/test_vector_index.py::TestVectorIndex::test_queries_only_authorized_native_units` |
| V014 | falls_back_after_native_table_loss | Error | native table removed after ready | bounded NumPy neighbors from durable floats | Integration | ✅ `integration/modules/search/test_vector_index.py::TestVectorIndex::test_falls_back_after_native_table_loss` |
| V015 | retirement_preserves_full_float_rows | Edge | active then retired generation | active drop rejected; retirement keeps durable bytes | Integration | ✅ `integration/modules/search/test_vector_index.py::TestVectorIndex::test_retirement_preserves_full_float_rows` |
| V016 | rolls_back_native_preparation | Error | rebuild starts then transaction rolls back | previous native data and readiness survive | Integration | ✅ `integration/modules/search/test_vector_index.py::TestVectorIndex::test_rolls_back_native_preparation` |
| V017 | refuses_untrusted_generation_identifiers | Error | invalid/bool/SQL-like generation IDs | rejected before constructing DDL | Integration | ✅ `integration/modules/search/test_vector_index.py::TestVectorIndex::test_refuses_untrusted_generation_identifiers` |
| V018 | repairs_native_tables_without_inference | Edge | restored native table absent | worker restores native neighbors from floats | Integration | ✅ `integration/modules/search/test_vector_index.py::TestVectorIndex::test_repairs_native_tables_without_inference` |
| V019 | disables_sqlite_extension_loading_after_connect | Error | installed wheel loaded | SQL extension loading is then unauthorized | Integration | ✅ `integration/db/test_vector_extensions.py::TestVectorExtensions::test_disables_sqlite_extension_loading_after_connect` |
| V020 | disables_loading_after_failure | Error | wheel loader raises | loading gate closed; normal SQL usable | Integration | ✅ `integration/db/test_vector_extensions.py::TestVectorExtensions::test_disables_loading_after_failure` |
| V021 | disables_loading_after_async_connect | Edge | async SQLite pool opens connection | native functions available, loading disabled | Integration | ✅ `integration/db/test_vector_extensions.py::TestVectorExtensions::test_disables_loading_after_async_connect` |
| V022 | queries_authorized_native_vectors | Error | actual pgvector/HNSW with restricted SQL set | authorized full-float rescore; no schema drift | Integration | ✅ `integration/postgres/test_vector_index.py::TestPostgresVectorIndex::test_queries_authorized_native_vectors` |
| V023 | degrades_when_pg_extension_cannot_be_created | Error | PostgreSQL role lacks extension permission | NumPy fallback without breaking transaction | Integration | ✅ `integration/postgres/test_vector_index.py::TestPostgresVectorIndex::test_degrades_when_pg_extension_cannot_be_created` |
| V024 | restores_without_pgvector | Edge | native PostgreSQL snapshot copied with extension disabled | same portable neighbors without inference | Integration | ✅ `integration/postgres/test_vector_index.py::TestPostgresVectorIndex::test_restores_without_pgvector` |
| V025 | rolls_back_a_failed_transfer | Error | copy verification fails | destination schema/data rolled back | Integration | ✅ `integration/modules/administration/test_database_transfer.py::TestDatabaseTransfer::test_rolls_back_a_failed_transfer` |
| V026 | restores_native_floats_from_portable_backup | Happy | PostgreSQL backup then data loss | restore replaces data atomically and preserves vectors | Integration | ✅ `integration/modules/backups/backup/test_snapshot.py::TestPostgresSnapshot::test_restores_native_floats_from_portable_backup` |
| V027 | restores_searchable_documents_through_the_api | Happy | PostgreSQL HTTP backup then data loss | restored Document and search result through real HTTP API | E2E | ✅ `e2e/test_postgres_backup.py::TestPostgresBackup::test_restores_searchable_documents_through_the_api` |
| V028 | preserves_encoded_database_urls | Edge | encoded credentials and PostgreSQL options | Alembic receives exact URL | Unit | ✅ `unit/db/test_migrate.py::TestMigrationConfig::test_preserves_encoded_database_urls` |
| V029 | supports_search_modalities | Happy | text and point-cloud contract identities | valid immutable Space values | Core | ✅ `core/inference/test_embedding.py::TestEmbeddingSpace::test_supports_search_modalities` |

## Remote inference

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| R001 | embeds_ordered_text_batches | Happy | loopback endpoint returns vectors out of order | output matches each input index with native normalization | Contract | ✅ `contract/modules/inference/test_remote.py::TestRemoteEmbeddingProvider::test_embeds_ordered_text_batches` |
| R002 | rejects_invalid_embedding_outputs | Error | missing/duplicate indexes, wrong dimension, nonfinite/zero vector | stable failure, no partial batch | Contract | ✅ `contract/modules/inference/test_remote.py::TestRemoteEmbeddingProvider::test_rejects_invalid_embedding_outputs` |
| R003 | bounds_remote_response_bytes | Error | oversized or compressed response | rejected inside response budget | Contract | ✅ `contract/modules/inference/test_remote.py::TestRemoteEmbeddingProvider::test_bounds_remote_response_bytes` |
| R004 | retries_bounded_rate_limits | Error | 429 then success | bounded retry and successful batch | Contract | ✅ `contract/modules/inference/test_remote.py::TestRemoteEmbeddingProvider::test_retries_bounded_rate_limits` |
| R005 | refuses_redirected_embeddings | Error | endpoint redirects to another origin | no redirected request or credential forwarding | Contract | ✅ `contract/modules/inference/test_remote.py::TestRemoteEmbeddingProvider::test_refuses_redirected_embeddings` |
| R006 | opens_a_failed_endpoint_circuit | Error | repeated retryable failures | subsequent call rejected without a socket request | Contract | ✅ `contract/modules/inference/test_remote.py::TestRemoteEmbeddingProvider::test_opens_a_failed_endpoint_circuit` |
| R007 | cancels_remote_inference | Edge | operation cancelled before/during wait | bounded cancellation; no publication | Contract | ✅ `contract/modules/inference/test_remote.py::TestRemoteEmbeddingProvider::test_cancels_remote_inference` |
| R008 | keeps_inference_credentials_encrypted | Error | endpoint saved with key and secret headers | raw DB lacks plaintext and read API redacts secrets | Integration | ✅ `integration/api/v1/test_inference.py::TestCreateEndpoint::test_keeps_inference_credentials_encrypted` |
| R009 | requires_admin_endpoint_configuration | Error | ordinary user submits endpoint | rejected before probe or mutation | Integration | ✅ `integration/api/v1/test_inference.py::TestCreateEndpoint::test_requires_admin_endpoint_configuration` |
| R010 | isolates_endpoint_configuration_versions | Edge | endpoint/model/credential version changes | new immutable provider identity; old Space retained | Integration | ✅ `integration/api/v1/test_inference.py::TestCreateEndpoint::test_isolates_endpoint_configuration_versions` |
| R011 | bounds_the_whole_operation | Error | stalled and trickling server | single deadline, one request | Contract | ✅ `contract/modules/inference/test_remote.py::TestRemoteEmbeddingProvider::test_bounds_the_whole_operation` |
| R012 | keeps_inference_wire_data_out_of_debug_logs | Error | DEBUG logging with private text and response header | query/key/upstream header/URL absent | Contract | ✅ `contract/modules/inference/test_remote.py::TestRemoteEmbeddingProvider::test_keeps_inference_wire_data_out_of_debug_logs` |
| R013 | does_not_retry_rejected_credentials | Error | 401 endpoint | one request and stable code | Contract | ✅ `contract/modules/inference/test_remote.py::TestRemoteEmbeddingProvider::test_does_not_retry_rejected_credentials` |
| R014 | redacts_invalid_configuration_input | Error | invalid secret header | 422 omits submitted credentials | Integration | ✅ `integration/api/v1/test_inference.py::TestCreateEndpoint::test_redacts_invalid_configuration_input` |
| R015 | omits_credentials_from_audit | Happy | saved endpoint | audit has only kind/host/model | Integration | ✅ `integration/api/v1/test_inference.py::TestCreateEndpoint::test_omits_credentials_from_audit` |
| R016 | defaults_to_independent_opt_ins | Happy | fresh settings | all AI uses and outbound images off | Integration | ✅ `integration/api/v1/test_inference.py::TestReadSettings::test_defaults_to_independent_opt_ins` |
| R017 | requires_a_capable_chat_endpoint | Error | generative opt-in without endpoint | rejected, settings remain off | Integration | ✅ `integration/api/v1/test_inference.py::TestUpdateSettings::test_requires_a_capable_chat_endpoint` |
| R018 | configures_a_probed_embedding_endpoint | Happy | admin HTTP setup plus real socket canary | stored capability and secret-free readback | E2E | ✅ `e2e/test_remote_inference.py::TestRemoteInferenceSetup::test_configures_a_probed_embedding_endpoint` |
| R019 | preserves_existing_settings_on_upgrade | Happy | existing library and config at W3 revision | content retained, new opt-ins absent | Integration | ✅ `integration/db/migrations/test_inference_endpoints_migration.py::TestInferenceEndpointsMigration::test_preserves_existing_settings_on_upgrade` |
| R020 | downgrades_without_losing_existing_settings | Edge | current schema | old settings survive downgrade | Integration | ✅ `integration/db/migrations/test_inference_endpoints_migration.py::TestInferenceEndpointsMigration::test_downgrades_without_losing_existing_settings` |
| C001 | returns_a_locally_validated_object | Happy | Ollama-schema/vLLM-tool/llama.cpp-JSON contract variants | typed result and honest guarantee | Contract | ✅ `contract/modules/inference/test_chat.py::TestRemoteChatProvider::test_returns_a_locally_validated_object` |
| C002 | reuses_a_probed_dialect | Edge | previous structured unsupported responses | one subsequent completion request | Contract | ✅ `contract/modules/inference/test_chat.py::TestRemoteChatProvider::test_reuses_a_probed_dialect` |
| C003 | repairs_json_once | Error | strict JSON output malformed once | validated repaired result | Contract | ✅ `contract/modules/inference/test_chat.py::TestRemoteChatProvider::test_repairs_json_once` |
| C004 | rejects_a_failed_repair | Error | two invalid JSON outputs | stable error, bounded calls | Contract | ✅ `contract/modules/inference/test_chat.py::TestRemoteChatProvider::test_rejects_a_failed_repair` |
| C005 | rejects_unsupported_fields_locally | Error | endpoint ignores supplied schema | no extra field accepted | Contract | ✅ `contract/modules/inference/test_chat.py::TestRemoteChatProvider::test_rejects_unsupported_fields_locally` |
| C006 | never_changes_dialect_after_timeout | Error | ambiguous timeout | one dialect, one request | Contract | ✅ `contract/modules/inference/test_chat.py::TestRemoteChatProvider::test_never_changes_dialect_after_timeout` |
| C007 | never_changes_dialect_after_server_failure | Error | 500 responses | bounded retry of original dialect | Contract | ✅ `contract/modules/inference/test_chat.py::TestRemoteChatProvider::test_never_changes_dialect_after_server_failure` |
| C008 | rejects_external_schema_references_before_egress | Error | schema URL reference | rejected without HTTP | Contract | ✅ `contract/modules/inference/test_chat.py::TestRemoteChatProvider::test_rejects_external_schema_references_before_egress` |
| C009 | probes_responses_without_assuming_availability | Edge | optional Responses API absent | falls back to chat-completions | Contract | ✅ `contract/modules/inference/test_chat.py::TestRemoteChatProvider::test_probes_responses_without_assuming_availability` |
| C010 | uses_a_confirmed_responses_endpoint | Happy | Responses API responds to canary | validated schema result with store=false | Contract | ✅ `contract/modules/inference/test_chat.py::TestRemoteChatProvider::test_uses_a_confirmed_responses_endpoint` |
| C011 | does_not_fall_back_after_a_responses_timeout | Error | ambiguous Responses timeout | no second paid dialect request | Contract | ✅ `contract/modules/inference/test_chat.py::TestRemoteChatProvider::test_does_not_fall_back_after_a_responses_timeout` |
| C012 | configures_chat_with_reported_json_guarantees | Happy | admin HTTP setup, JSON-only real fake | reduced guarantee persisted and independent switch | E2E | ✅ `e2e/test_remote_inference.py::TestRemoteInferenceSetup::test_configures_chat_with_reported_json_guarantees` |

## Durable generation lifecycle

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| G001 | prepares_without_mutating_active | Happy | configured endpoint, no active index | immutable building proposal; not prematurely serving | Integration | ✅ `integration/modules/search/test_generations.py::TestPrepare::test_prepares_a_building_generation` |
| G002 | fences_concurrent_proposals | Error | PostgreSQL, same modality/profile | at most one building | Integration | ✅ `integration/postgres/test_search_generations.py::TestPrepare::test_fences_concurrent_proposals` |
| G003 | backfills_current_passages | Happy | seeded four-type library | current native vectors for every public Subject type | Integration | ✅ `integration/modules/search/test_indexing.py::TestIndexProcessor::test_indexes_each_public_subject_type` |
| G004 | rejects_late_publication | Error | content edit during inference | no stale vector committed | Integration | ✅ `integration/modules/search/test_indexing.py::TestIndexProcessor::test_rejects_source_edits_during_inference` |
| G005 | resumes_expired_leases | Edge | interrupted worker | committed work retained, expired unit reclaimed | E2E | ✅ `e2e/test_search_generations.py::TestSearchGenerationLifecycle::test_resumes_committed_work_after_process_loss` |
| G006 | quarantines_poison_units | Error | repeat inference failure | bounded attempts; other units progress; verify refuses incomplete | Integration | ✅ `integration/modules/search/test_indexing.py::TestIndexProcessor::test_quarantines_poison_inputs_without_repeating_healthy_work` |
| G007 | activates_verified_generation_atomically | Happy | ready proposal and correct version | one active, old retired, no serving gap | Integration | ✅ `integration/modules/search/test_generations.py::TestActivate::test_replaces_the_old_active_atomically` |
| G008 | preserves_active_when_verification_fails | Error | smoke failure or unreconciled edits | old active unchanged | Integration | ✅ `integration/modules/search/test_indexing.py::TestIndexProcessor::test_keeps_active_ready_when_the_replacement_probe_fails` |
| G009 | maintains_active_and_building_recipes | Edge | different text prefixes; edits during rebuild | both serving/building hashes current | Integration | ✅ `integration/modules/search/test_indexing.py::TestIndexProcessor::test_refreshes_both_generations_after_an_edit` |
| G010 | drains_readers_before_pruning | Edge | query pinned during flip | old vectors retained until expiry and rollback retention | Integration | ✅ `integration/modules/search/test_generations.py::TestPruneOne::test_retains_old_vectors_while_a_reader_is_pinned` |
| G011 | rejects_capacity_overcommit | Error | old+new exceed storage budget | proposal rejected; old retained | Integration | ✅ `integration/modules/search/test_generations.py::TestPrepare::test_rejects_capacity_overcommit` |
| G012 | switches_index_without_reembedding | Happy | same Space, NumPy → sqlite-vec | durable floats reused, no embedding request | E2E | ✅ `e2e/test_search_generations.py::TestSearchGenerationLifecycle::test_switches_to_native_index_without_reembedding` |
| G013 | rejects_publication_after_cancellation | Error | cancel while inference is running | no vector committed after cancellation | Integration | ✅ `integration/modules/search/test_indexing.py::TestIndexProcessor::test_rejects_publication_after_cancellation` |
| G014 | preserves_a_live_worker_lease | Edge | another worker owns the generation | second claim refused | Integration | ✅ `integration/modules/search/test_indexing.py::TestClaim::test_preserves_a_live_worker_lease` |
| G015 | activates_automatically_after_verification | Happy | default proposal, complete backfill | active without a second administrator action | Integration | ✅ `integration/modules/search/test_indexing.py::TestIndexProcessor::test_activates_automatically_after_verification` |
| G016 | refuses_content_added_after_verification | Error | new passage between ready and activation | activation rejected; old active retained | Integration | ✅ `integration/modules/search/test_generations.py::TestActivate::test_refuses_content_added_after_verification` |
| G017 | rejects_stale_proposal_versions | Error | wrong version token | conflict response | Integration | ✅ `integration/modules/search/test_generations.py::TestActivate::test_rejects_stale_proposal_versions` |
| G018 | refuses_cancellation_after_concurrent_activation | Error | PostgreSQL cancel waits behind activation | active remains serving | Integration | ✅ `integration/postgres/test_search_generations.py::TestCancel::test_refuses_cancellation_after_concurrent_activation` |
| G019 | serializes_concurrent_activation | Edge | two PostgreSQL activations | one active result, one conflict | Integration | ✅ `integration/postgres/test_search_generations.py::TestActivate::test_serializes_concurrent_activation` |
| G020 | retries_quarantined_inputs_after_manual_reset | Happy | quarantined current passage | manual reset leads to a verified vector | Integration | ✅ `integration/modules/search/test_indexing.py::TestIndexProcessor::test_retries_quarantined_inputs_after_manual_reset` |
| G021 | preserves_rollback_retention_after_readers_finish | Edge | retired generation within retention | floats remain stored | Integration | ✅ `integration/modules/search/test_generations.py::TestPruneOne::test_preserves_rollback_retention_after_readers_finish` |
| G022 | prunes_expired_generations_with_their_checkpoints | Happy | retirement retention elapsed | old floats and checkpoints removed; new generation retained | Integration | ✅ `integration/modules/search/test_generations.py::TestPruneOne::test_prunes_expired_generations_with_their_checkpoints` |
| G023 | reports_provider_budget_truncation | Edge | passage exceeds provider input budget | truncation recorded; human source text unchanged | Integration | ✅ `integration/modules/search/test_indexing.py::TestIndexProcessor::test_reports_provider_budget_truncation` |
| G024 | pauses_backfill_when_ai_is_disabled | Edge | opt-in revoked | no inference; durable work retained | Integration | ✅ `integration/modules/search/test_indexing.py::TestIndexProcessor::test_pauses_backfill_when_ai_is_disabled` |
| G025 | does_not_publish_a_rolled_back_job | Error | caller transaction rolls back | job and caller changes absent | Integration | ✅ `integration/runtime/test_jobs.py::TestJobRegistry::test_does_not_publish_a_rolled_back_job` |
| G026 | rehydrates_a_committed_transactional_job | Happy | new registry after commit | pending job recovered from database | Integration | ✅ `integration/runtime/test_jobs.py::TestJobRegistry::test_rehydrates_a_committed_transactional_job` |
| G027 | preserves_generation_backfill_jobs_after_restart | Edge | committed running backfill | startup retains work and progress | Integration | ✅ `integration/runtime/test_jobs.py::TestReconcileInterruptedJobs::test_preserves_generation_backfill_jobs_after_restart` |
| G028 | keeps_the_migrated_postgres_schema_in_sync | Happy | downgrade then upgrade lifecycle schema | autogenerate comparison empty | Integration | ✅ `integration/postgres/test_search_generations.py::TestPrepare::test_keeps_the_migrated_postgres_schema_in_sync` |
| G029 | preserves_legacy_serving_indexes_across_roundtrip | Edge | old Similar Models vectors | bytes and serving identity preserved, work not adopted | Integration | ✅ `integration/db/migrations/test_search_generations_migration.py::TestSearchGenerationsMigration::test_preserves_legacy_serving_indexes_across_roundtrip` |
| G030 | rejects_numeric_overflow_in_structured_output | Error | valid JSON number overflowing float | stable rejection before downstream use | Unit | ✅ `unit/modules/inference/test_chat.py::TestRemoteChatProvider::test_rejects_numeric_overflow_in_structured_output` |
| G031 | rejects_numeric_overflow | Error | overflowing transport JSON number | invalid-JSON error | Unit | ✅ `unit/modules/inference/test_transport.py::TestPostJson::test_rejects_numeric_overflow` |
| G032 | preserves_endpoint_deadline_inside_a_longer_job | Error | short endpoint timeout, long batch deadline | socket cancelled within endpoint deadline | Contract | ✅ `contract/modules/inference/test_remote.py::TestRemoteEmbeddingProvider::test_preserves_endpoint_deadline_inside_a_longer_job` |
| G033 | rejects_unavailable_recipes | Error | unknown recipe/version or invalid budget | stable recipe error | Unit | ✅ `unit/modules/search/test_text_inputs.py::TestTextRecipe::test_rejects_unavailable_recipes` |
| G034 | preserves_document_prefix_inside_budget | Edge | provider cap smaller than passage | prefix retained, body truncated at exact cap | Unit | ✅ `unit/modules/search/test_text_inputs.py::TestDocumentInput::test_preserves_document_prefix_inside_budget` |
| G035 | preserves_short_inputs | Happy | input below provider cap | exact text without truncation | Unit | ✅ `unit/modules/search/test_text_inputs.py::TestDocumentInput::test_preserves_short_inputs` |
| G036 | exposes_a_durable_build_job | Happy | administrator generation proposal | 202 with persisted job/status | Integration | ✅ `integration/api/v1/test_inference.py::TestProposeGeneration::test_exposes_a_durable_build_job` |
| G037 | requires_admin_generation_proposals | Error | ordinary user proposal | 403 without generation creation | Integration | ✅ `integration/api/v1/test_inference.py::TestProposeGeneration::test_requires_admin_generation_proposals` |
| G038 | requires_admin_generation_actions | Error | ordinary user activates/cancels/retries | 403 for each action | Integration | ✅ `integration/api/v1/test_inference.py::TestCancelGeneration::test_requires_admin_generation_actions` |
| G039 | cancels_the_selected_proposal | Happy | matching admin action token | durable cancelled status | Integration | ✅ `integration/api/v1/test_inference.py::TestCancelGeneration::test_cancels_the_selected_proposal` |
| G040 | rejects_unavailable_recipes_at_api | Error | unsupported passage version | 400 with stable error | Integration | ✅ `integration/api/v1/test_inference.py::TestProposeGeneration::test_rejects_unavailable_recipes` |
| G041 | requires_ai_opt_in | Error | AI disabled | proposal refused before work | Integration | ✅ `integration/modules/search/test_generations.py::TestPrepare::test_requires_ai_opt_in` |
| G042 | refuses_unverified_generations | Error | backfill incomplete | activation conflict | Integration | ✅ `integration/modules/search/test_generations.py::TestActivate::test_refuses_unverified_generations` |
| G043 | reclaims_an_expired_worker_lease | Edge | worker lease expired | fresh unique token claims same durable generation | Integration | ✅ `integration/modules/search/test_indexing.py::TestClaim::test_reclaims_an_expired_worker_lease` |
| G044 | prunes_vectors_in_bounded_batches | Edge | more than 128 retired vectors | at most 128 deleted per worker unit | Integration | ✅ `integration/modules/search/test_generations.py::TestPruneOne::test_prunes_vectors_in_bounded_batches` |
| G045 | refreshes_different_passage_template_versions | Edge | active/building templates differ | both templates maintained until drain | Integration | ✅ `integration/modules/search/test_captions.py::TestCaptions::test_upgrades_active_text_to_caption_recipe` — edit while v1 active/v2 building refreshes both; only v2 includes the caption, then flips |

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| G046 | indexes_content_added_during_backfill | Edge | new Document after first batch | new passage vector present before activation | Integration | ✅ `integration/modules/search/test_indexing.py::TestIndexProcessor::test_indexes_content_added_during_backfill` |

## Compressed index transforms

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| Q001 | rejects_unapproved_truncation | Error | unknown MRL prefix size | proposal rejected before index creation | Unit | ✅ `packages/printstash-core/tests/inference/test_transforms.py::TestIndexTransform::test_rejects_unapproved_truncation` |
| Q002 | normalizes_an_approved_prefix | Happy | approved prefix of a full native vector | unit-length prefix; original bytes intact | Unit | ✅ `packages/printstash-core/tests/inference/test_transforms.py::TestIndexTransform::test_normalizes_an_approved_prefix` |
| Q003 | roundtrips_a_versioned_int8_recipe | Happy | fixed symmetric unit scale | identical codes after metadata reload | Unit | ✅ `packages/printstash-core/tests/inference/test_transforms.py::TestIndexTransform::test_roundtrips_a_versioned_int8_recipe` |
| Q004 | packs_binary_signs_in_declared_order | Happy | mixed signs including zero | exact packed bits and Hamming distance | Unit | ✅ `packages/printstash-core/tests/inference/test_transforms.py::TestIndexTransform::test_packs_binary_signs_in_declared_order` |
| Q005 | rejects_corrupt_native_vectors | Error | wrong length, NaN, zero prefix | stable error before derived publication | Unit | ✅ `packages/printstash-core/tests/inference/test_transforms.py::TestIndexTransform::test_rejects_corrupt_native_vectors` |
| Q006 | bounds_compressed_shortlists | Edge | iterable exceeds scan budget | bounded candidates with truncation disclosed | Unit | ✅ `packages/printstash-core/tests/inference/test_transforms.py::TestShortlistCodes::test_bounds_compressed_shortlists` |
| Q007 | rejects_incompatible_transform_metadata | Error | dimensions, quantization or version changed | stable error instead of cross-generation scoring | Unit | ✅ `packages/printstash-core/tests/inference/test_transforms.py::TestIndexTransform::test_rejects_incompatible_transform_metadata` |
| Q008 | stores_compact_derivatives_on_both_databases | Happy | quantized generation on SQLite/PostgreSQL | compact codes; full native bytes retained | Integration | ✅ `integration/modules/search/test_code_index.py::TestPrepare::test_stores_compact_derivatives` |
| Q009 | uses_native_quantized_shortlists | Happy | installed sqlite-vec or pgvector | authorized compressed shortlist then native-float scores | Integration | ✅ `integration/modules/search/test_vector_index.py::TestVectorIndex::test_rescores_compressed_native_candidates` |
| Q010 | rebuilds_compressed_derivatives_without_inference | Edge | derived table removed after restart | fallback serves; bounded rebuild reuses saved floats | Integration | ✅ `integration/modules/search/test_vector_index.py::TestVectorIndex::test_rebuilds_compressed_derivatives_without_inference` |
| Q011 | bounds_transform_metadata | Error | oversized or deeply nested recipe | stable transform error | Unit | ✅ `packages/printstash-core/tests/inference/test_transforms.py::TestIndexTransform::test_bounds_transform_metadata` |
| Q012 | rejects_overflowing_code_norms | Error | corrupt float codes overflow norm | stable code error | Unit | ✅ `packages/printstash-core/tests/inference/test_transforms.py::TestIndexTransform::test_rejects_overflowing_code_norms` |
| Q013 | restores_compressed_generations_without_extension | Edge | SQLite snapshot opened without extension | native floats serve equivalent results | Integration | ✅ `integration/modules/search/test_vector_index.py::TestVectorIndex::test_restores_compressed_generations_without_extension` |
| Q014 | copies_compressed_generations_between_databases | Happy | compressed SQLite source transferred to PostgreSQL | byte-exact native vectors and reconstructible codes | Integration | ✅ `integration/modules/administration/test_database_transfer.py::TestDatabaseTransfer::test_copies_compressed_generations_between_databases` |
| Q015 | preserves_legacy_endpoint_identity | Edge | endpoint created before model repository field | saved hash still resolves | Unit | ✅ `unit/modules/inference/test_endpoint.py::TestEndpointConfig::test_preserves_legacy_endpoint_identity` |
| Q016 | discloses_only_pinned_model_capabilities | Edge | exact reviewed identity or alias/moving revision | MRL choices only for exact identity | Integration | ✅ `integration/modules/inference/test_configuration.py::TestLoad::test_discloses_only_pinned_model_capabilities` |
| Q017 | measures_compressed_ranking_recall | Happy | reproducible vectors and held-out vector queries | measured int8/binary recall tolerance against full-float baseline | Integration | ✅ `integration/modules/search/test_code_index.py::TestRankingRecall::test_measures_compressed_ranking_recall` |
| Q018 | identifies_only_registered_compressed_tables | Error | registered codes and unrelated lookalike | schema audit excludes only owned derivative | Integration | ✅ `integration/modules/search/test_code_index.py::TestPrepare::test_identifies_only_registered_compressed_tables` |

## Hybrid query retrieval

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| H001 | fuses_ranked_subjects | Happy | two ranked legs, explicit weights | deterministic weighted RRF order; duplicates count once per leg | Unit | ✅ `packages/printstash-core/tests/search/test_fusion.py::TestFuse::test_fuses_ranked_subjects` |
| H002 | rejects_invalid_fusion_budgets | Error | invalid weights, rank constant or limits | stable error | Unit | ✅ `packages/printstash-core/tests/search/test_fusion.py::TestFuse::test_rejects_invalid_fusion_budgets` |
| H003 | caches_query_vectors_in_memory | Happy | repeated query in same Space and authorization context | one provider request; equivalent vectors | Unit | ✅ `unit/modules/inference/test_query.py::TestQueryRunner::test_caches_query_vectors_in_memory` |
| H004 | isolates_query_cache_contexts | Error | changed Space, user or permission context | independent inference; no cross-context cache hit | Unit | ✅ `unit/modules/inference/test_query.py::TestQueryRunner::test_isolates_query_cache_contexts` |
| H005 | bounds_interactive_inference | Edge | all query slots occupied | immediate busy result; no unbounded queue | Unit | ✅ `unit/modules/inference/test_query.py::TestQueryRunner::test_bounds_interactive_inference` |
| H006 | returns_by_query_deadline | Error | delayed provider | lexical response within budget; late result not cached | Unit | ✅ `unit/modules/inference/test_query.py::TestQueryRunner::test_returns_by_query_deadline` |
| H007 | expires_cached_query_vectors | Edge | TTL or entry cap reached | old entries recomputed | Unit | ✅ `unit/modules/inference/test_query.py::TestQueryRunner::test_expires_cached_query_vectors` |
| H008 | retrieves_semantic_matches | Happy | active text generation, nonliteral query | relevant Subject with semantic evidence | Integration | ✅ `integration/modules/search/test_retrieval.py::TestSearch::test_retrieves_semantic_matches` |
| H009 | rejects_weak_dense_neighbors | Edge | all vector scores below Space floor | no_strong_matches state | Integration | ✅ `integration/modules/search/test_retrieval.py::TestSearch::test_rejects_weak_dense_neighbors` |
| H010 | rechecks_permissions_after_inference | Error | access revoked while query runs | no hidden Subject or contributor evidence | Integration | ✅ `integration/modules/search/test_retrieval.py::TestSearch::test_rechecks_permissions_after_inference` |
| H011 | filters_subject_types_before_ranking | Happy | selected Document type | only Documents scored and returned | Integration | ✅ `integration/modules/search/test_retrieval.py::TestSearch::test_filters_subject_types_before_ranking` |
| H012 | disables_query_inference_for_lexical_mode | Edge | configured AI with lexical request | same lexical results; no provider request | Integration | ✅ `integration/modules/search/test_retrieval.py::TestSearch::test_disables_query_inference_for_lexical_mode` |
| H013 | expires_cursors_after_generation_switch | Edge | cursor from retired generation | stable invalid-cursor response | Integration | ✅ `integration/modules/search/test_retrieval.py::TestSearch::test_expires_cursors_after_generation_switch` |
| H014 | preserves_an_inflight_generation | Edge | activation during inference | admitted query uses pinned Space; lease released afterward | Integration | ✅ `integration/modules/search/test_retrieval.py::TestSearch::test_preserves_an_inflight_generation` |
| H015 | excludes_stale_or_trashed_vectors | Error | stale content hash or trashed Subject | no semantic result | Integration | ✅ `integration/modules/search/test_retrieval.py::TestSearch::test_excludes_stale_vectors` |
| H016 | degrades_after_active_vector_corruption | Error | corrupt source vector | lexical results with stable unavailable reason | Integration | ✅ `integration/modules/search/test_retrieval.py::TestSearch::test_degrades_after_active_vector_corruption` |
| H017 | reports_bounded_candidate_coverage | Edge | finite authorized overfetch exhausted | short page with explicit truncation, no hidden counts | Integration | ✅ `integration/modules/search/test_retrieval.py::TestSearch::test_reports_bounded_candidate_coverage` |
| H018 | serves_hybrid_queries_through_http | Happy | real worker, local contract server, generation ready | authenticated hybrid result with per-leg evidence | E2E | ✅ `e2e/test_search_generations.py::TestSearchGenerationLifecycle::test_serves_hybrid_queries_through_http` |
| H019 | keeps_instant_search_lexical | Edge | instant dropdown request | no query inference; lexical-only response | Integration | ✅ `integration/modules/search/test_retrieval.py::TestSearch::test_disables_query_inference_for_lexical_mode` |
| H020 | encodes_collection_links | Edge | collection path contains reserved characters | correct browse URL roundtrip | Integration | ✅ `integration/modules/search/test_retrieval.py::TestSearch::test_encodes_collection_links` |
| H021 | validates_advanced_retrieval_settings | Error | invalid weights, floors, timeout or per-Space key | rejected settings | Unit | ✅ `unit/schemas/test_inference.py::TestSearchSettings::test_validates_advanced_retrieval_settings` |
| H022 | reads_retrieval_defaults_from_environment | Happy | deployment overrides | initial search settings use validated defaults | Unit | ✅ `unit/schemas/test_inference.py::TestSearchSettings::test_reads_retrieval_defaults_from_environment` |
| H023 | reports_lexical_status_with_ai_disabled | Edge | signed-in user, AI disabled | lexical ready; no global corpus counts | Integration | ✅ `integration/api/v1/test_search.py::TestSearch::test_reports_lexical_status_with_ai_disabled` |
| H024 | requires_authentication_for_status | Error | unauthenticated status request | 401 | Integration | ✅ `integration/api/v1/test_search.py::TestSearch::test_requires_authentication_for_status` |
| H025 | selects_the_declared_space_floor | Happy | per-Space floor differs from default | generation uses its configured floor | Integration | ✅ `integration/modules/search/test_retrieval.py::TestSearch::test_selects_the_declared_space_floor` |
| H026 | excludes_trashed_subjects | Error | Model trashed while vectors existed | Model absent from fused results | Integration | ✅ `integration/modules/search/test_retrieval.py::TestSearch::test_excludes_trashed_subjects` |
| H027 | refuses_inference_without_visible_vectors | Error | authenticated user with no reachable Subjects | no provider request | Integration | ✅ `integration/modules/search/test_retrieval.py::TestSearch::test_refuses_inference_without_visible_vectors` |
| H028 | expires_cursors_after_permission_changes | Edge | permissions change between pages | cursor rejected before inference | Integration | ✅ `integration/modules/search/test_retrieval.py::TestSearch::test_expires_cursors_after_permission_changes` |
| H029 | retries_admission_after_concurrent_cutover | Edge | activation wins between registry read and lease write | bounded retry serves new active generation | Integration | ✅ `integration/modules/search/test_retrieval.py::TestSearch::test_retries_admission_after_concurrent_cutover` |
| H030 | serves_continuous_readers_during_a_transform_switch | Edge | readers during float-to-int8/binary replacement | zero HTTP errors or empty semantic pages; both generations observed | E2E | ✅ `e2e/test_search_generations.py::TestSearchGenerationLifecycle::test_serves_continuous_readers_during_a_transform_switch` |
| H031 | expires_cursors_after_ranking_changes | Edge | RRF settings changed between pages | stable invalid cursor | Integration | ✅ `integration/modules/search/test_retrieval.py::TestSearch::test_expires_cursors_after_ranking_changes` |
| H032 | excludes_hidden_contributors_from_dense_evidence | Error | PostgreSQL visible Subject with private contributor | only authorized evidence survives NumPy/native retrieval | Integration | ✅ `integration/postgres/test_semantic.py::TestSearch::test_excludes_hidden_contributors_from_dense_evidence` |
| H033 | never_explains_a_hidden_keyword_match | Error | query matches private contributor only | no lexical explanation from hidden text | Integration | ✅ `integration/postgres/test_semantic.py::TestSearch::test_never_explains_a_hidden_keyword_match` |
| H034 | reauthorizes_cached_query_vectors | Error | access revoked after a successful cached query | no hidden result on repeated query | Integration | ✅ `integration/modules/search/test_retrieval.py::TestSearch::test_reauthorizes_cached_query_vectors` |
| H035 | sanitizes_an_unexpected_provider_failure | Error | provider raises opaque exception | stable public error without upstream text | Unit | ✅ `unit/modules/inference/test_query.py::TestQueryRunner::test_sanitizes_an_unexpected_provider_failure` |
| H036 | rejects_invalid_query_vectors | Error | missing, wrong-sized or nonfinite vectors | no cached result | Unit | ✅ `unit/modules/inference/test_query.py::TestQueryRunner::test_rejects_invalid_query_vectors` |
| H037 | rejects_images_from_the_text_cache | Error | image sent to text-only runner | rejected before inference | Unit | ✅ `unit/modules/inference/test_query.py::TestQueryRunner::test_rejects_images_from_the_text_cache` |

## Local model acquisition and inference

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| LM001 | keeps_local_query_inputs_in_memory | Error | text or RGB query with temporary-file writes forbidden | valid vector without input spooling | Integration | ✅ `integration/modules/inference/test_local.py::TestLocalProvider::test_keeps_local_query_inputs_in_memory` |
| LM002 | bounds_worker_pipe_frames | Error | oversized or malformed IPC request | rejected before native loading | Integration | ✅ `integration/modules/inference/test_worker.py::TestNativeProtocol::test_bounds_worker_pipe_frames` |
| LM003 | preserves_legacy_visual_space_identity | Edge | existing v1 CLIP/DINO manifest | unchanged configuration hash and vectors | Integration | ✅ `integration/modules/inference/test_worker.py::TestManifest::test_preserves_legacy_visual_space_identity` |
| LM004 | embeds_text_with_declared_pooling | Happy | pinned text ONNX graph and tokenizer | declared pooling/prefix/dimension; native float vectors | Integration | ✅ `integration/modules/inference/local/test_text.py::TestLocalText::test_embeds_text_with_declared_pooling` |
| LM005 | rejects_undeclared_onnx_graph_contracts | Error | unsupported opset, signature or external data | unavailable provider before indexing | Integration | ✅ `integration/modules/inference/local/test_text.py::TestLocalText::test_rejects_undeclared_onnx_graph_contracts` |
| LM006 | discovers_preplaced_models_offline | Happy | valid pinned assets already present | available model without network | Integration | ✅ `integration/modules/inference/test_model_cache.py::TestModelCache::test_discovers_preplaced_models_offline` |
| LM007 | never_downloads_without_acquisition_opt_in | Error | acquisition disabled or non-admin | no egress or files | Integration | ✅ `integration/api/v1/test_inference_models.py::TestInferenceModels::test_never_downloads_without_acquisition_opt_in` |
| LM008 | installs_a_verified_model_atomically | Happy | explicitly requested pinned model | digest-verified complete directory and job status | Contract | ✅ `contract/modules/inference/test_model_acquisition.py::TestAcquisition::test_installs_a_verified_model_atomically` |
| LM009 | rejects_download_digest_mismatch | Error | corrupt asset stream | no installation; temporary files removed | Contract | ✅ `contract/modules/inference/test_model_acquisition.py::TestAcquisition::test_rejects_invalid_downloads[corrupt]` |
| LM010 | bounds_model_download_bytes | Error | declared or streamed bytes exceed manifest size | cancelled transfer without partial installation | Contract | ✅ `contract/modules/inference/test_model_acquisition.py::TestAcquisition::test_rejects_invalid_downloads[oversize]` |
| LM011 | validates_every_model_redirect | Error | redirect leaves registry/mirror policy | no unsafe egress | Contract | ✅ `contract/modules/inference/test_model_acquisition.py::TestAcquisition::test_rejects_invalid_downloads[redirect]` |
| LM012 | rejects_cache_path_escape | Error | symlink, traversal or external ONNX file | no outside read/write | Integration | ✅ `integration/modules/inference/test_model_cache.py::TestModelCache::test_rejects_cache_path_escape` |
| LM013 | preserves_installed_models_after_interrupted_download | Edge | cancellation/crash during install | existing version intact; temp recovered | Contract | ✅ `contract/modules/inference/test_model_acquisition.py::TestAcquisition::test_preserves_installed_models_after_interrupted_download` |
| LM014 | refuses_pruning_referenced_models | Edge | active/building/retained generation uses model | model remains available | Integration | ✅ `integration/modules/inference/test_model_cache.py::TestModelCache::test_refuses_pruning_referenced_models` |
| LM015 | prunes_unreferenced_models_by_lru | Edge | capacity cap reached | least-used unreferenced model removed | Integration | ✅ `integration/modules/inference/test_model_cache.py::TestModelCache::test_prunes_unreferenced_models_by_lru` |
| LM016 | protects_models_during_concurrent_load | Edge | prune races model load | reference lease protects files | Integration | ✅ `integration/modules/inference/test_model_cache.py::TestModelCache::test_protects_models_during_concurrent_load` |
| LM017 | keeps_remote_inference_available_without_onnx | Edge | lite installation | local unavailable, remote still usable | Contract | ✅ `contract/modules/inference/test_remote.py::TestRemoteEmbeddingProvider::test_keeps_remote_inference_available_without_onnx` |
| LM018 | activates_an_acquired_text_model | Happy | download opt-in, explicit generation proposal | full HTTP local semantic search succeeds | E2E | ✅ `e2e/test_search_local.py::TestLocalSearch::test_activates_an_acquired_text_model` |
| LM019 | reports_actual_token_truncation | Edge | passage exceeds tokenizer budget | stored vector records truncation | Integration | ✅ `integration/modules/search/test_indexing.py::TestIndexProcessor::test_reports_actual_token_truncation` |
| LM020 | measures_real_text_retrieval_quality | Happy | pinned BGE baseline and held-out corpus | hybrid recall@5 >=0.9; lexical BM25 >=0.6; ILIKE measured | Integration | ✅ `integration/modules/search/retrieval/test_text_quality.py::TestTextQuality::test_measures_real_text_retrieval_quality` |
| LM021 | admits_a_small_logical_index_budget | Edge | configured index budget below physical headroom | quota enforced without double-charging volume headroom | Integration | ✅ `integration/modules/search/test_generations.py::TestPrepare::test_admits_a_small_logical_index_budget` |
| LM022 | preserves_physical_headroom_with_logical_limits | Error | logical budget fits but disk is low | admission denied by physical capacity policy | Integration | ✅ `integration/modules/storage/test_capacity.py::TestCapacityManager::test_preserves_physical_headroom_with_logical_limits` |
| LM023 | reuses_a_loaded_worker | Happy | repeated calls for one model | same process serves both calls | Integration | ✅ `integration/modules/inference/test_worker_pool.py::TestWorkerPool::test_reuses_a_loaded_worker` |
| LM024 | evicts_the_least_recent_idle_worker | Edge | third model exceeds worker capacity | oldest idle process terminated | Integration | ✅ `integration/modules/inference/test_worker_pool.py::TestWorkerPool::test_evicts_the_least_recent_idle_worker` |
| LM025 | defers_background_work_while_busy | Edge | model worker already in use | busy response without another process | Integration | ✅ `integration/modules/inference/test_worker_pool.py::TestWorkerPool::test_defers_background_work_while_busy` |
| LM026 | prioritizes_waiting_queries | Edge | interactive query waits behind inference | background admission yields; query proceeds | Integration | ✅ `integration/modules/inference/test_worker_pool.py::TestWorkerPool::test_prioritizes_waiting_queries` |
| L027 | evicts_a_failed_worker | Error | inference fails or child exits | next request uses a fresh process | Integration | ✅ `integration/modules/inference/test_worker_pool.py::TestWorkerPool::test_evicts_a_failed_worker` |
| L028 | protects_a_busy_worker_from_pruning | Edge | disk prune during model inference | directory remains protected | Integration | ✅ `integration/modules/inference/test_worker_pool.py::TestWorkerPool::test_protects_a_busy_worker_from_pruning` |
| L029 | expires_idle_workers | Edge | idle time budget expires | resident child terminated | Integration | ✅ `integration/modules/inference/test_worker_pool.py::TestWorkerPool::test_expires_idle_workers` |
| L030 | enforces_the_total_worker_memory_budget | Error | two resident models exceed RSS allowance | idle model evicted or active request fails | Integration | ✅ `integration/modules/inference/test_worker_pool.py::TestWorkerPool::test_enforces_the_total_worker_memory_budget` |
| L031 | cancels_worker_admission | Error | query cancelled while waiting | stable cancellation and no stranded waiter | Integration | ✅ `integration/modules/inference/test_worker_pool.py::TestWorkerPool::test_cancels_worker_admission` |
| L032 | defers_busy_local_indexing_without_quarantine | Edge | local render budget busy | generation resumes without poison failures | Integration | ✅ `integration/modules/search/test_indexing.py::TestIndexProcessor::test_defers_busy_local_indexing_without_quarantine` |
| L033 | refuses_eviction_of_referenced_models_for_capacity | Error | next model exceeds cache budget | active version retained; admission fails | Integration | ✅ `integration/modules/inference/test_model_cache.py::TestModelCache::test_refuses_eviction_of_referenced_models_for_capacity` |
| L034 | keeps_a_referenced_model_warm | Edge | serving model reaches idle TTL | resident worker retained within overall cache cap | Integration | ✅ `integration/modules/inference/test_worker_pool.py::TestWorkerPool::test_keeps_a_referenced_model_warm` |
| L035 | preserves_recipe_identity_without_an_encoder | Edge | legacy remote text recipe | identical canonical JSON | Unit | ✅ `core/search/test_text_inputs.py::TestTextRecipe::test_preserves_recipe_identity_without_an_encoder` |
| L036 | binds_local_recipes_to_the_encoder | Happy | trusted encoder manifest digest | round-trip identity includes digest | Unit | ✅ `core/search/test_text_inputs.py::TestTextRecipe::test_binds_local_recipes_to_the_encoder` |
| L037 | rejects_malformed_encoder_digests | Error | noncanonical digest or invalid recipe types | stable rejection | Unit | ✅ `core/search/test_text_inputs.py::TestTextRecipe::test_rejects_malformed_encoder_digests` |
| L038 | lists_preplaced_models_without_downloads | Happy | preplaced model plus curated catalog | bounded admin inventory without filesystem paths | Integration | ✅ `integration/api/v1/test_inference_models.py::TestInferenceModels::test_lists_preplaced_models_without_downloads` |
| L039 | rejects_unauthenticated_model_management | Error | missing authentication for admin model actions | request rejected before file or network effects | Integration | ✅ `integration/api/v1/test_inference_models.py::TestInferenceModels::test_rejects_unauthenticated_model_management` |
| L040 | keeps_download_credentials_out_of_logs | Error | signed redirect with debug logging | credential absent from logs | Contract | ✅ `contract/modules/inference/test_model_acquisition.py::TestAcquisition::test_keeps_download_credentials_out_of_logs` |
| L041 | reuses_a_verified_install_without_egress | Edge | identical installed version requested again | existing complete model returned without network | Contract | ✅ `contract/modules/inference/test_model_acquisition.py::TestAcquisition::test_reuses_a_verified_install_without_egress` |
| L042 | recovers_an_abandoned_install | Edge | partial staging directory after crash | partial removed; complete atomic install succeeds | Contract | ✅ `contract/modules/inference/test_model_acquisition.py::TestAcquisition::test_recovers_an_abandoned_install` |
| L043 | cleans_up_a_cancelled_download | Error | cancel after first response | no partial install or staging leftovers | Contract | ✅ `contract/modules/inference/test_model_acquisition.py::TestAcquisition::test_cleans_up_a_cancelled_download` |
| L044 | applies_text_prefixes_only_once | Edge | prepared text with query/document recipe | no duplicate or cross-role prefix | Integration | ✅ `integration/modules/inference/local/test_text.py::TestLocalText::test_applies_text_prefixes_only_once` |
| L045 | rejects_unsupported_local_runtime_before_download | Error | lite install and explicit acquisition request | capability error before egress | Integration | ✅ `integration/api/v1/test_inference_models.py::TestInferenceModels::test_rejects_unsupported_local_runtime_before_download` |
| L046 | accepts_read_only_offline_models | Edge | weights mounted read-only | native inference succeeds without touching mount metadata | Integration | ✅ `integration/modules/inference/test_local.py::TestLocalProvider::test_accepts_read_only_offline_models` |
| L047 | rejects_hostile_model_download_urls | Error | credentials, HTTP or invalid redirect origin | no request before policy rejection | Unit | ✅ `unit/modules/inference/test_model_acquisition.py::TestDownloadPolicy::test_rejects_hostile_model_download_urls` |
| L048 | rejects_invalid_recipe_types | Error | boolean version, fractional/string budget, non-string digest | stable recipe error | Unit | ✅ `core/search/test_text_inputs.py::TestTextRecipe::test_rejects_invalid_recipe_types` |
| L049 | rejects_nonadministrator_model_management | Error | authenticated ordinary user | 403 before model operations | Integration | ✅ `integration/api/v1/test_inference_models.py::TestInferenceModels::test_rejects_nonadministrator_model_management` |
| L050 | accepts_an_explicit_https_mirror | Happy | configured HTTPS mirror and registry CDN | permitted host/port accepted | Unit | ✅ `unit/modules/inference/test_model_acquisition.py::TestDownloadPolicy::test_accepts_an_explicit_https_mirror` |
| L051 | rejects_invalid_mirror_configuration | Error | HTTP, credentials or query in mirror base URL | rejected before download | Unit | ✅ `unit/modules/inference/test_model_acquisition.py::TestDownloadPolicy::test_rejects_invalid_mirror_configuration` |
| L052 | serves_multiple_private_requests | Happy | two framed queries in one native worker | distinct correct native outputs with bounded framing | Integration | ✅ `integration/modules/inference/test_worker.py::TestNativeProtocol::test_serves_multiple_private_requests` |

W7 measured corpus: hybrid recall@5 0.96875, BM25 0.875, ILIKE 0.625 (32 original engineering queries, real pinned BGE vectors). Both out-of-domain probes returned results at the generic 0.35 floor. This is not the separate human-labelled user evaluation. This was the W7 checkpoint; the later model, visual and performance sections record subsequent measurements. Physical ARM and independent human quality acceptance remain open.

## W8 operational controls and search UI

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| U001 | preserves_endpoint_credentials_when_editing_model | Happy | existing endpoint; omitted secrets | new tested version retains encrypted secrets | Integration | ✅ `integration/api/v1/test_inference.py::TestCreateEndpoint::test_preserves_endpoint_credentials_when_editing_model` |
| U002 | rejects_credential_inheritance_across_origins | Error | edited endpoint host, scheme or port | no probe and no credential egress | Integration | ✅ `integration/api/v1/test_inference.py::TestCreateEndpoint::test_rejects_credential_inheritance_across_origins` |
| U003 | clears_explicitly_replaced_credentials | Edge | explicit empty key and headers | new version has no credentials; original unchanged | Integration | ✅ `integration/api/v1/test_inference.py::TestCreateEndpoint::test_clears_explicitly_replaced_credentials` |
| U004 | rejects_missing_credential_source | Error | unknown source ID | stable failure without probe | Integration | ✅ `integration/api/v1/test_inference.py::TestCreateEndpoint::test_rejects_missing_credential_source` |
| U005 | reports_only_usable_active_capabilities | Happy | active verified generation; local runtime or endpoint available | AI status follows usable capability | Integration | ✅ `integration/modules/search/test_status.py::TestStatus::test_reports_only_usable_active_capabilities` |
| U006 | hides_backlog_for_inaccessible_subjects | Error | missing vectors in private Collection | ordinary status does not reveal backlog | Integration | ✅ `integration/modules/search/test_status.py::TestStatus::test_hides_backlog_for_inaccessible_subjects` |
| U007 | reports_authorized_backlog | Happy | visible passage missing a current vector | backlog status becomes true | Integration | ✅ `integration/modules/search/test_status.py::TestStatus::test_reports_authorized_backlog` |
| U008 | disables_local_queries_when_local_opt_in_is_off | Error | active local model; flag disabled | no worker starts | Integration | ✅ `integration/modules/inference/test_configuration.py::TestEmbeddingProvider::test_disables_local_queries_when_local_opt_in_is_off` |
| U009 | estimates_generation_without_starting_work | Happy | current library and model proposal | bytes/counts returned; no rows, jobs or egress | Integration | ✅ `integration/modules/search/test_generations.py::TestEstimate::test_estimates_generation_without_starting_work` |
| U010 | reports_measured_generation_eta | Happy | partly processed running generation | last activity, created time and measured estimate | Integration | ✅ `integration/modules/search/test_generations.py::TestEstimate::test_reports_measured_generation_eta` |
| U011 | loads_grouped_environment_defaults | Happy | AI/embedding/chat environment | typed defaults with SystemConfig overlay | Integration | ✅ `integration/modules/inference/test_environment.py::TestEnvironment::test_loads_grouped_environment_defaults` |
| U012 | imports_environment_endpoint_only_on_admin_request | Happy | configured env endpoint | no startup networking; explicit canary before persistence | Integration | ✅ `integration/modules/inference/test_environment.py::TestEnvironment::test_imports_environment_endpoint_only_on_admin_request` |
| U013 | debounces_lexical_suggestions | Happy | typing in search | only lexical request before submit | Frontend | ✅ `frontend/src/components/__tests__/library-search.test.tsx — debounces lexical suggestions without inference` |
| U014 | submits_hybrid_search | Happy | Enter in search | canonical search route triggers hybrid | Frontend | ✅ `frontend/src/components/__tests__/library-search.test.tsx — submits the query to the results route` |
| U015 | keeps_search_keyboard_navigation_accessible | Happy | slash, arrows, Escape | focus and selection follow keyboard | Frontend | ✅ `frontend/src/components/__tests__/library-search.test.tsx — keeps keyboard focus through suggestions` |
| U016 | ignores_stale_suggestions | Edge | late response for old query | old results never replace current input | Frontend | ✅ `frontend/src/components/__tests__/library-search.test.tsx — ignores late suggestions for a replaced query` |
| U017 | renders_authorized_search_evidence | Happy | heterogeneous Subjects with plain text ranges | type, safe evidence, reason and correct destination | Frontend | ✅ `frontend/src/pages/__tests__/search.test.tsx — renders authorized evidence for every Subject type` |
| U018 | renders_degraded_search_state | Error | semantic failure | lexical results with readable warning | Frontend | ✅ `frontend/src/pages/__tests__/search.test.tsx — renders degraded search without losing available results` |
| U019 | renders_no_strong_matches | Edge | below semantic floor | honest empty state | Frontend | ✅ `frontend/src/pages/__tests__/search.test.tsx — renders no strong matches honestly` |
| U020 | loads_more_search_results | Happy | opaque next cursor | appends page without invented total | Frontend | ✅ `frontend/src/pages/__tests__/search.test.tsx — loads the next opaque cursor without repeating the first page` |
| U021 | separates_pending_model_from_active_generation | Happy | new selection before cutover | active history retains old model | Frontend | ✅ `frontend/src/components/__tests__/ai-search-settings.test.tsx — separates pending model selection from the serving index` |
| U022 | controls_model_download_lifecycle | Happy | admin explicit download | progress, cancel and completion refresh | Frontend | ✅ `frontend/src/components/__tests__/ai-search-settings.test.tsx — explicit download, cancellable progress and completed-model refresh` |
| U023 | controls_generation_lifecycle | Happy | building, ready, failed generations | only valid cancel/activate/retry actions | Frontend | ✅ `frontend/src/components/__tests__/ai-search-settings.test.tsx — sends a version-fenced cancel/activate/retry action` |
| U024 | preserves_secrets_in_settings_edits | Happy | existing endpoint form; unchanged key | secret omitted from request; indicator shown | Frontend | ✅ `frontend/src/components/__tests__/ai-search-settings.test.tsx — keeps saved credentials out of an unrelated endpoint edit` |
| U025 | shows_model_provenance_and_resource_estimate | Happy | catalog model selection | language, license, measured bytes and estimate | Frontend | ✅ `frontend/src/components/__tests__/ai-search-settings.test.tsx — shows pinned provenance with a current-library estimate` |
| U026 | supports_spanish_search_controls | Happy | Spanish locale | search and maintenance text translated | Frontend | ✅ `frontend/src/components/__tests__/library-search.test.tsx; ai-search-settings.test.tsx — Spanish controls` |
| U027 | searches_the_real_backend_from_the_top_bar | Happy | real library; browser submit | authorized Subject result opens | Playwright | ✅ `frontend/tests/e2e-real/ai-search/search.spec.ts — actual BGE and original CI fixture` |
| U028 | supports_search_on_mobile | Happy | narrow viewport | input and results usable without overflow | Playwright | ✅ `frontend/tests/e2e-real/ai-search/search.spec.ts — 390px mobile search and settings` |
| U029 | waits_for_shared_compute_within_the_query_deadline | Happy | another query or render briefly holds the compute slot | native result after release, within deadline | Integration | ✅ `integration/modules/inference/test_local.py::TestLocalProvider::test_waits_for_shared_compute_within_the_query_deadline` |
| U030 | times_out_while_waiting_for_shared_compute | Error | compute slot remains occupied | bounded inference_timeout, no extra worker | Integration | ✅ `integration/modules/inference/test_local.py::TestLocalProvider::test_times_out_while_waiting_for_shared_compute` |
| U031 | patches_canonical_admin_contracts | Happy | partial settings and generation lifecycle through /search | other opt-ins preserved, detail/action consistent | Integration | ✅ `integration/api/v1/test_inference.py::TestUpdateSettings::test_patches_settings_without_resetting_other_opt_ins; TestProposeGeneration::test_exposes_the_canonical_generation_lifecycle` |
| U032 | protects_canonical_administration_routes | Error | ordinary user on every new alias | 403 before side effects or disclosure | Integration | ✅ `integration/api/v1/test_inference.py::TestUpdateSettings::test_protects_canonical_administration_routes` |
| U033 | uses_configured_lexical_backend_and_browse_routes | Happy | forced LIKE and Collection result | ranked LIKE selected, correct Collection destination | Integration | ✅ `integration/api/v1/test_search.py::TestSearch — configured ranked LIKE and Collection browse destination` |
| U034 | groups_duplicate_evidence | Edge | two legs share excerpt | one excerpt with both reasons | Frontend | ✅ `frontend/src/pages/__tests__/search.test.tsx — shows a shared excerpt once with both match reasons` |
| U035 | restarts_expired_cursor | Error | generation changed between pages | restart discards old pages and cursor | Frontend | ✅ `frontend/src/pages/__tests__/search.test.tsx — restarts at the first page after a generation expires the cursor` |
| U036 | remembers_relevance_sort | Happy | select relevance in Model browse | stored preference and request sort agree | Frontend | ✅ `frontend/src/components/__tests__/model-grid.test.tsx — restores the selected relevance ordering for Model browse` |
| U037 | yields_background_admission_to_waiting_queries | Edge | interactive request waiting; capacity released | background yields, interactive receives native result | Integration | ✅ `integration/modules/inference/test_local.py::TestLocalProvider::test_yields_background_admission_to_waiting_queries` |
| U038 | honors_configured_embedding_batch_size | Happy | batch size two, five passages | every inference batch bounded at two, all indexed | Integration | ✅ `integration/modules/search/test_indexing.py::TestIndexProcessor::test_honors_the_configured_embedding_batch_size` |
| U039 | requires_caption_capability_and_consent | Error | no server or render consent | disabled control; removing consent clears opt-in | Frontend | ✅ `frontend/src/components/__tests__/ai-search-settings.test.tsx — requires the caption capability prerequisites` |
| U040 | preserves_new_input_when_a_debounced_url_update_lands | Edge | clear/type during an earlier browse URL transition | latest draft survives and only its suggestions render | Frontend | ✅ `frontend/src/components/__tests__/library-search.test.tsx — ignores late suggestions for a replaced query` |
| U041 | saves_advanced_ranking_choices | Happy | admin changes lexical backend and weight | typed request persists values with other ranking fields intact | Frontend | ✅ `frontend/src/components/__tests__/ai-search-settings.test.tsx — saves advanced ranking choices explicitly` |
| U042 | distinguishes_expired_and_invalid_cursors | Error | signed cursor generation or TTL changes; forged cursor | expired returns 409; malformed/context mismatch remains invalid | Unit / Integration | ✅ `unit/modules/search/test_cursors.py; integration/modules/search/test_retrieval.py::TestSearch::test_expires_cursors_after_generation_switch` |

W8 checkpoint: 133 backend tests passed in the combined run; its one failing
batch-count expectation omitted the verification canary and passed after
correction. Retrieval/API checks passed 32 cases, with the stale Collection URL
expectation corrected and its focused case passing. Six cursor unit tests pass.
The UI pass ran 259 tests with one worker; 24 focused control tests, 29 result/cache
tests and the real BGE/tiny-ONNX browser flows also passed. An overloaded combined
UI run produced 13 timeout failures plus two diagnosed failures; all affected
files subsequently passed. Lint, formatting, TypeScript and Pyright passed.
The OpenAPI snapshot was reviewed and regenerated. The design detector reported
no deterministic findings; desktop/mobile screenshots were inspected and the
excerpt/clear-button corrections confirmed. These are stage checks, not the
final full-suite, coverage, security or hardware acceptance gates.

## W9 visual retrieval and image boundaries

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| VI001 | decodes_oriented_metadata_free_query_images | Happy | JPEG with orientation and EXIF metadata | correctly oriented bounded RGB bytes only | Core unit | ✅ `integration/modules/search/test_visual_index.py::TestVisualIndex::test_refuses_incomplete_visual_worker_batches` |
| VI002 | rejects_invalid_or_excessive_image_inputs | Error | wrong MIME, malformed bytes, animation, pixel/body limits | stable failure before large decode or inference | Core unit / API | ✅ `integration/modules/search/test_visual_index.py::TestVisualIndex::test_discards_stale_visual_worker_failures` |
| VI003 | runs_image_queries_without_a_retained_cache_entry | Edge | repeated image query in compatible Space | bounded executor runs each time; no image or vector cache retention | Unit | ✅ `integration/modules/search/test_visual_index.py::TestVisualIndex::test_sanitizes_visual_failure_details` |
| VI004 | binds_visual_profile_to_exact_paired_towers | Error | same dimension from another encoder | manifest/Space mismatch rejected before inference | Integration | ✅ `test_worker.py` and visual recipe tests |
| VI005 | shares_one_mesh_load_for_visual_views | Happy | six canonical views and thumbnail | one load, fixed renderer recipe and compute budget | Integration | ✅ geometry analysis and native render tests |
| VI006 | aggregates_normalized_view_vectors | Happy | valid views of differing norms | correct normalized mean; deterministic max ranking | Core unit / Integration | ✅ mean-pool tests; real max-ranking replay |
| VI007 | builds_visual_generations_independently | Happy | text active, visual build and flip | text unchanged; visual vectors tied to source hash | Integration | ✅ visual/text independent cutover integration test |
| VI008 | reuses_views_for_aggregation_changes | Edge | only mean/max aggregation changed | existing per-view native floats copied without rendering/inference | Integration | ✅ aggregation-only reuse and fallback-copy tests |
| VI009 | fences_visual_sources_before_and_after_work | Error | trash, permission change or Artifact update during work | no stale publication or unauthorized results | Integration | ✅ late hash/trash, VIEW, cursor and executor-context fences |
| VI010 | quarantines_and_retries_failed_visual_units | Error | a view fails repeatedly | partial coverage reported; bounded retry; no false-ready generation | Integration | ✅ bounded poison retries/quarantine; ready-count verification |
| VI011 | accepts_image_upload_without_disk_spooling | Happy | raw or multipart image through shipped proxy | bounded memory body, no original/thumbnail/query vector persisted | E2E | ✅ real nginx, read-only body directory, raw/chunked multipart |
| VI012 | uses_only_ready_visual_fallbacks | Error | preferred profile unavailable | ready thumbnail leg or truthful text-only degradation | Integration | ✅ actual prepared thumbnail survives multiview failure |
| VI013 | retrieves_models_using_current_vectors | Happy | VIEW-authorized Model-as-query | existing source vectors, self excluded, missing work bounded | Integration / E2E | ✅ current-vector/self-exclusion/missing-work API + browser |
| VI014 | searches_images_from_accessible_ui | Happy | ready visual profile, desktop/mobile image selection | capability-gated controls, image results, clear/retry and disclosure | Frontend / Playwright | ✅ frontend tests + real-browser image flow; responsive screenshot correction |
| VI015 | measures_real_visual_recipe_quality | Happy | frozen 32-object engineering corpus, real CLIP B/32 | measured recall/cost and reproducible vector replay, separately labelled limitations | Integration / Benchmark | ✅ real B/32 replay and study; independent human/ARM acceptance remains ❌; the later printed-photo section records the real photo result |

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| VI016 | rejects_unadmitted_image_body_before_parsing | Error | anonymous, disabled or saturated image request | stable error without parser/decode invocation | Integration | ✅ API admission tests |
| VI017 | shares_render_and_resident_encoder_rss_budget | Error | renderer plus idle encoder exceeds ceiling | idle encoder evicted; oversized renderer rejected/reaped | Integration | ✅ worker-pool/native-render tests |
| VI018 | preserves_passage_failures_across_visual_migration | Edge | seeded pre-upgrade passage retry and Artifact failure | upgrade/downgrade/re-upgrade retains old text data | Migration | ✅ `test_visual_failures_migration.py` |
| VI019 | reuses_only_current_verified_mesh_thumbnail | Error | changed source, recipe, strategy or encoded digest | valid thumbnail reused; stale/corrupt/non-mesh image rejected | Integration | ✅ five cached-media identity/digest cases |
| VI020 | renders_step_without_child_database_access | Happy | real STEP solid | bounded triangle-only tessellation and seven RGB frames | Integration | ✅ real OCP STEP box |

W9's backend replay/source checks passed 45 cases; five newly added cached-media
cases exposed fixture setup errors and subsequently passed with the corrected
preplaced-file fixture. Three API admission cases also passed. Prior native memory
and worker-pool checks passed 40 cases, with a degenerate STEP fixture replaced
by a real solid and its focused check passing. The nginx contract passes with
request-body storage read-only. Parallel image tests exposed a lost ContextVar
at the request executor boundary; the executor now copies request context.
The browser flow passed with original tiny ONNX models; its mobile image picker
was corrected after screenshot inspection. The first browser run rejected a
mislabelled WebP fixture, and one subsequent Vite run stalled while importing
modules before the app mounted. A later unchanged run passed. These are stage
checks, not final full-suite, coverage, human/photo, hardware or security gates.

### W10 — optional point-cloud profile (planning pass)

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| PT001 | accepts a bounded point tensor | Happy | 10,000 XYZ/RGB points | Exact bytes round-trip through input contract | Unit | ✅ core `inference/test_points.py::TestPointInput::test_preserves_the_bounded_tensor` |
| PT002 | rejects invalid point tensors | Error | Wrong size, mixed modalities, nonfinite or out-of-range values | Stable error before native inference | Unit | ✅ core `inference/test_points.py::TestPointInput::test_rejects_wrong_tensor_contract` and `test_rejects_unbounded_tensor_values` |
| PT003 | groups points with the export's exact deterministic recipe | Happy | Fixed cloud with radius ties | FPS starts at zero; source-order radius groups padded with first match | Unit | ✅ core `inference/test_points.py::TestPointInput::test_groups_by_source_order_with_radius_padding` |
| PT004 | rejects incompatible point alignment | Error | Same dimension but different paired towers | Manifest/Space mismatch before inference | Integration | ✅ `integration/modules/inference/worker/test_point.py::TestPointManifest::test_rejects_incompatible_point_towers` |
| PT005 | runs the point tower in the shared native worker | Happy | Original small ONNX contract export | Unit-normalized vector and canary validation | Integration | ✅ `integration/modules/inference/worker/test_point.py::TestPointWorker::test_runs_all_paired_modalities_locally` |
| PT006 | samples complete geometry in one contained pass | Happy | STL/3MF/STEP source | Deterministic normalized Y-up points; source untouched | Integration | ✅ `integration/modules/search/visual_index/test_point.py::TestPointIndex::test_samples_points_in_the_isolated_geometry_worker` |
| PT007 | quarantines incomplete point geometry | Error | Oversized or malformed Artifact | No current point unit; bounded retry | Integration | ✅ `integration/modules/search/visual_index/test_point.py::TestPointIndex::test_quarantines_invalid_point_geometry` |
| PT008 | publishes one current point unit per Model | Happy | Opted-in preplaced encoder and two live meshes | Ready Generation and exact typed source identity | Integration | ✅ `integration/modules/search/visual_index/test_point.py::TestPointIndex::test_publishes_one_current_point_unit` |
| PT009 | fences a stale point result | Error | File hash or live state changes during inference | No stale vector publication | Integration | ✅ `integration/modules/search/visual_index/test_point.py::TestPointIndex::test_fences_late_point_publication` |
| PT010 | queries point vectors with their paired text tower | Happy | Text prompt, authorized Models | Ranked point results; hidden Models absent | Integration | ✅ `integration/modules/search/visual_index/test_point.py::TestPointIndex::test_hides_private_point_matches` |
| PT011 | preserves ready visual fallback after point failure | Error | Point encoder unavailable, thumbnail ready | Available thumbnail results with explicit degraded point leg | Integration | ✅ `integration/modules/search/visual_index/test_point.py::TestPointIndex::test_preserves_thumbnail_results_when_point_encoding_fails` |
| PT012 | exposes the optional preplaced point profile | Happy | Installed point manifest | Admin can prepare and inspect the independent Generation | Frontend unit | ✅ `frontend/src/components/__tests__/ai-search-settings.test.tsx::prepares the optional preplaced point profile` |
| PT013 | searches geometry through a real point encoder | Happy | Pinned OpenShape, frozen geometry corpus, real paired CLIP queries | Recall and cost recorded separately from human acceptance | Integration | ✅ `integration/modules/search/retrieval/test_visual_quality.py::TestPointRanking::test_replays_real_point_quality_gain` |
| PT014 | completes a point-profile browser flow | Happy | Local point model, uploaded meshes | Build, ready status and visible search results | Playwright | ✅ `frontend/tests/e2e-real/ai-search/search.spec.ts::builds the optional local point profile for geometry search` (actual preplaced OpenShape) |
| PT015 | validates the point canary on physical ARM | Happy | Native ARM hardware and pinned export | Canary within tolerance; latency/RSS recorded | Integration | ❌ missing — physical ARM host requested |
| PT016 | publishes current geometry units on PostgreSQL | Happy | Real PostgreSQL, thumbnail fallback then point generation | Both profiles activate; integer null passage identity; point retrieval succeeds | Integration | ✅ `integration/postgres/test_search_generations.py::TestPointGenerations::test_publishes_current_point_units_on_postgres` |

W10 focused evidence: 67 backend visual/point regression tests passed together;
44 core point/input contract tests passed; 49 native manifest/worker tests passed;
31 frontend settings/results tests passed. The real OpenShape browser flow passed,
as did the PostgreSQL thumbnail/point publication test. The 40 affected repository
hygiene checks passed after grouping/naming corrections. Frontend lint, typecheck
and the deterministic design scan passed. These are historical W10 checks; the
final verification section records branch-wide gates. A broader Pyright scan
outside the configured include list reported SQLModel typing/narrowing errors;
that separate scan is not reported as a passing gate.

### W11 — separate generated captions (implementation evidence)

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| CAP001 | preserves human descriptions during caption generation | Happy | Opted-in image chat endpoint; mesh Model with description | Caption in separate row; description unchanged | Integration | ✅ `integration/modules/search/test_captions.py::TestCaptions::test_keeps_generated_text_separate_from_human_description` |
| CAP002 | requires image-render consent before generation | Error | Chat configured but caption or image consent off | No render or outbound completion | Integration | ✅ `integration/modules/search/test_captions.py::TestCaptions::test_rechecks_consent_after_rendering` |
| CAP003 | contains malformed caption output | Error | Extra fields, oversized, empty or non-string caption | No partial caption; bounded retry status | Integration | ✅ `integration/modules/search/test_captions.py::TestCaptions::test_bounds_invalid_output_retries` |
| CAP004 | keeps dismissal durable across source or endpoint changes | Edge | Dismissed caption; later sweep | Tombstone survives; no regeneration | Integration | ✅ `integration/modules/search/test_captions.py::TestCaptions::test_keeps_dismissal_when_the_endpoint_changes` |
| CAP005 | protects an edit against late worker completion | Edge | User saves while VLM runs | Edited text retained; late response discarded | Integration | ✅ `integration/modules/search/test_captions.py::TestCaptions::test_fences_a_late_completion[edit]` |
| CAP006 | protects dismissal against late worker completion | Edge | User dismisses while VLM runs | Empty caption retained; late response discarded | Integration | ✅ `integration/modules/search/test_captions.py::TestCaptions::test_fences_a_late_completion[dismiss]` |
| CAP007 | rejects caption changes without Subject EDIT | Error | VIEW-only or hidden Subject | 403/404; no text or state mutation | Integration | ✅ `integration/api/v1/test_captions.py::TestCaptionAPI::test_enforces_subject_edit_permissions` |
| CAP008 | regenerates caption Passages synchronously | Happy | Caption edited | New lexical match immediately; old native vector invalidated | Integration | ✅ `integration/modules/search/test_captions.py::TestCaptions::test_keeps_one_lexical_recipe_during_semantic_coexistence` |
| CAP009 | removes dismissed text from every current search leg | Edge | Previously indexed caption | No lexical or semantic match from dismissed caption | Integration | ✅ `integration/modules/search/test_captions.py::TestCaptions::test_keeps_one_lexical_recipe_during_semantic_coexistence` |
| CAP010 | preserves recipe-v1 identity alongside caption recipe-v2 | Edge | Active v1 index while v2 builds | Old hashes and readers valid; v2 includes caption | Integration | ✅ `integration/modules/search/test_captions.py::TestCaptions::test_upgrades_active_text_to_caption_recipe` |
| CAP011 | counts one lexical recipe per source segment | Edge | Both passage versions exist | No duplicate BM25 document or term counts | Integration | ✅ `integration/modules/search/test_captions.py::TestCaptions::test_keeps_one_lexical_recipe_during_semantic_coexistence` |
| CAP012 | fences changed geometry before caption publication | Edge | File hash or live state changes during completion | No stale caption publication | Integration | ✅ `integration/modules/search/test_captions.py::TestCaptions::test_rejects_stale_source_completion` |
| CAP013 | resumes expired caption leases after restart | Edge | Worker stops during inference | One bounded retry; duplicate publication prevented | Integration | ✅ `integration/modules/search/test_captions.py::TestCaptions::test_reclaims_an_expired_lease_after_restart` |
| CAP014 | bounds poison-caption retries | Error | Endpoint repeatedly fails | Three attempts then failed status; Model remains usable | Integration | ✅ `integration/modules/search/test_captions.py::TestCaptions::test_bounds_remote_timeout_retries` |
| CAP015 | runs captions independently from query parsing | Happy | Caption opt-in true; NL parsing false | Caption generated without parsing queries | Integration | ✅ `integration/modules/search/test_captions.py::TestCaptions::test_keeps_generated_text_separate_from_human_description` |
| CAP016 | labels edited or generated captions in the Model UI | Happy | Existing caption, en/es | Separate labelled text; authorized controls | Frontend unit | ✅ `frontend/src/components/__tests__/subject-caption.test.tsx — saves an edit with the displayed version; explains unavailable generation in Spanish` |
| CAP017 | renders caption content as untrusted text | Error | HTML/script-like completion | Text visible; no executable markup | Frontend unit | ✅ `frontend/src/components/__tests__/subject-caption.test.tsx — shows text safely to a viewer without edit controls` |
| CAP018 | edits and dismisses through the browser | Happy | Real Model and caption API | Edited text visible; dismissal removes search contribution | Playwright | ✅ `frontend/tests/e2e-real/ai-search/search.spec.ts — controls a separately searchable caption` |
| CAP019 | upgrades existing libraries without changing human content | Happy | Existing Models/Documents and configuration | Additive schema; rows retained through upgrade | Integration | ✅ `integration/db/migrations/test_captions_migration.py::TestCaptionMigration::test_preserves_existing_library_content` |
| CAP020 | retains captions for all four Subject identities | Happy | Model, Collection, Multipart Model, Document | Separate typed caption rows; owner deletion cannot attach text to a reused ID | Integration | ✅ `integration/db/models/test_captions.py::TestSubjectCaption` |
| CAP021 | sends a rendered preview through the real HTTP chat adapter | Happy | Real STL renderer, local contract VLM | Bounded 384px JPEG only; searchable separate caption; dismissal removes it | E2E | ✅ `e2e/test_search_captions.py::TestCaptionWorkflow::test_generates_a_searchable_caption_without_changing_human_text` |
| CAP022 | coalesces duplicate generation requests | Edge | Same Subject/input/recipe requested twice | One row and unchanged version/lease | Integration | ✅ `integration/modules/search/test_captions.py::TestCaptions::test_keeps_one_task_for_repeated_generation_requests` |
| CAP023 | removes generated claims in the source transaction | Edge | Geometry hash changes | Caption text and lexical match disappear before regeneration | Integration | ✅ `integration/modules/search/test_captions.py::TestCaptions::test_invalidates_generated_claims_when_geometry_changes` |
| CAP024 | preserves PostgreSQL BM25 statistics across caption dismissal | Edge | Real PostgreSQL, v1+v2 passages | One document; caption match disappears; human match retained | Integration | ✅ `integration/postgres/test_search_passages.py::TestSearchPassages::test_indexes_one_caption_recipe_with_bm25` |
| CAP025 | terminates a final expired lease after restart | Error | Third attempt interrupted | Failed status, no fourth inference call | Integration | ✅ `integration/modules/search/test_captions.py::TestCaptions::test_finishes_an_exhausted_lease_after_restart` |

W11 evidence: 70 backend regression tests passed together, with four additional
freshness/idempotency/timeout tests passing separately; the final caption-owner
suite passed all 25 tests, including exhausted-lease recovery. Core passage/recipe suite:
44 passed. Caption UI: 5 passed; real-backend Playwright: 1 passed (desktop and
mobile screenshots inspected). PostgreSQL caption check: 1 passed. Repository
hygiene/migration checks: 3,484 passed. These are stage checks; full branch gates
remain for final closure. The VLM end-to-end test uses a contract server and makes
no claim about caption quality from an actual language model.

### W12 — independent consumers and compatible adoption

| ID | Behaviour | Case | Setup | Observable expectation | Tier | Evidence |
| --- | --- | --- | --- | --- | --- | --- |
| IC001 | runs Search without the Similar Models package | Happy | Isolated installed app, package removed, preplaced ONNX | Public Search works; native provider/store consumer works; no similarity imports | E2E | ✅ `e2e/test_search_independence.py::TestSearchIndependence::test_runs_without_related_feature_packages` |
| IC002 | keeps external algorithm versions outside Space identity | Edge | Consumer changes ranking version | Same Space, generation, IDs and native bytes reused | E2E | ✅ `e2e/test_search_independence.py::TestSearchIndependence::test_runs_without_related_feature_packages` |
| IC003 | publishes portable nullable owner columns | Error | PostgreSQL consumer source with SQL NULL passage ID | Successful native upsert, no text-to-integer failure | Integration | ✅ `integration/postgres/test_vector_index.py::TestPostgresVectorIndex::test_publishes_nullable_owner_columns_from_a_consumer` |
| IC004 | rejects mismatched immutable metadata during adoption | Error | Corrupt mirrored Space profile/provider/model/recipe/prefix | Stable corruption error; no adoption | Integration | ✅ `integration/modules/search/test_vector_store.py::TestVectorStore::test_refuses_corrupt_immutable_metadata; integration/modules/similarity/test_vector_sources.py::TestGenerationLifecycle::test_refuses_adoption_of_corrupt_space` |
| IC005 | rejects unauthorized independent consumer publication | Error | Source filtered by an outsider's VIEW scope | No vector written | Integration | ✅ `integration/modules/search/test_vector_store.py::TestVectorStore::test_scopes_an_independent_consumer_to_view_permissions` |
| IC006 | rejects incompatible units before persistence | Error | Wrong dimension, invalid key/kind/hash | No vector written | Integration | ✅ `integration/modules/search/test_vector_store.py::TestVectorStore::test_rejects_invalid_consumer_units` |
| IC007 | declares configured and unavailable inference capability | Edge | Missing configuration, then preplaced validated local model | Explicit unavailable error; validated native provider when configured | Integration | ✅ `integration/modules/inference/test_local.py::TestLocalProvider::test_reports_configured_capability` |
| IC008 | keeps existing Similar Models bytes and identities compatible | Happy | Existing v1 rows migrate and consumer resumes | Exact IDs, keys, hashes and BLOBs; no re-embedding for compatible rows | Integration | ✅ `integration/db/migrations/test_search_vectors_migration.py::TestSearchVectorsMigration::test_preserves_legacy_vectors_on_upgrade; integration/modules/similarity/test_vector_sources.py` |
| IC009 | preserves manual library work without inference installed | Edge | Packages physically removed | App boots; Families work; model-query shortcut reports unavailable | E2E | ✅ `e2e/test_family_independence.py::TestFamilyIndependence::test_manual_family_flow_without_related_feature_packages` |

W12 evidence: independent-consumer/provider/adoption regression suite 49 passed;
PostgreSQL nullable-owner regression passed after reproducing the failure;
optional-install and OpenAPI checks 5 passed, followed by the final optional
assembly/Family run (5 passed). Shared-store adoption validates every mirrored
immutable Space field before reusing an active generation.

### W15 — print-history predicates and editable natural-language filters

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
| --- | --- | --- | --- | --- | --- | --- |
| NL001 | requires one matching print job | Error | Different jobs satisfy each restriction | Split-history Model excluded | Integration | ✅ `backend/tests/integration/modules/library/model_views/test_structured_filters.py::TestPrintHistoryFilters::test_requires_one_job_for_the_combined_history_predicates` |
| NL002 | uses actual half-open history bounds | Edge | Exact start/end/duration; null and estimated times | Only actual duration inside interval included | Integration | ✅ `backend/tests/integration/modules/library/model_views/test_structured_filters.py::TestPrintHistoryFilters::test_uses_actual_duration_with_half_open_date_boundaries` |
| NL003 | applies duration to every browse reader | Happy | List, page, outliner, facets with 199/200 second jobs | All readers exclude the exclusive maximum | Integration | ✅ `backend/tests/integration/api/v1/test_search.py::TestBrowseHistory::test_applies_actual_duration_to_each_browse_reader` |
| NL004 | resolves relative calendar periods | Edge | All seven periods; DST spring/fall, year boundary | Exact UTC boundaries | Unit | ✅ `backend/tests/unit/modules/search/test_calendar.py::TestBounds::test_resolves_calendar_boundaries` |
| NL005 | requires both consent switches | Error | User/instance/both disabled | Original query; no chat egress | Integration | ✅ `backend/tests/integration/modules/search/test_parsing.py::TestParse::test_requires_both_optins_before_egress` |
| NL006 | rejects untrusted filter values | Error | Unknown choices, extra keys, invalid dates/ranges | Original query and empty restrictions | Integration | ✅ `backend/tests/integration/modules/search/test_parsing.py::TestParse::test_rejects_untrusted_filters` |
| NL007 | falls back on provider timeout | Error | Provider timeout | Original text and bounded failure code | Integration | ✅ `backend/tests/integration/modules/search/test_parsing.py::TestParse::test_falls_back_on_provider_timeout` |
| NL008 | exposes successful-print inference | Happy | Parsed completed outcome | Removable success chip and normalized request | Frontend unit | ✅ `frontend/src/components/__tests__/search-filter-controls.test.tsx::turns a submitted sentence into canonical editable filters` |
| NL009 | filters semantic candidates before scoring | Error | Nonmatching history includes dense candidates | No egress for empty eligibility; matching Model retained | Integration | ✅ `backend/tests/integration/modules/search/test_retrieval.py::TestStructuredRetrieval::test_filters_semantic_candidates_before_scoring` |
| NL010 | binds cursor to filter context | Error | Change restriction or sort between pages | Old cursor rejected | Integration | ✅ `backend/tests/integration/modules/search/test_retrieval.py::TestStructuredRetrieval::test_expires_a_cursor_when_structured_context_changes` |
| NL011 | persists filters without repeated parsing | Happy | Real browser/backend/local HTTP chat; save, edit, restore | One chat call; normalized view and desktop/mobile layout | Playwright | ✅ `frontend/tests/e2e-real/ai-search/nl-filters.spec.ts::persists editable filters without repeated parsing` |
| NL012 | parses independently of captions | Happy | NL enabled; captions disabled | Actual duration and server calendar bounds; no image input | Integration | ✅ `backend/tests/integration/modules/search/test_parsing.py::TestParse::test_normalizes_real_duration_with_absolute_calendar_bounds` |
| NL013 | omits queries from proxy failures | Error | Actual nginx with unavailable upstream | 502 query and prompt absent from logs | E2E | ✅ `backend/tests/e2e/test_search_query_logs.py::TestSearchQueryLogs::test_omits_queries_when_upstream_is_unavailable` |
| NL014 | preserves users through preference migration | Happy | Seeded prior schema; upgrade/downgrade; erase user | Original identity preserved; preference cascade | Integration | ✅ `backend/tests/integration/db/migrations/test_search_preferences_migration.py::TestSearchPreferencesMigration::test_preserves_existing_user_data` |
| NL015 | localizes inferred filters | Edge | Spanish locale | Spanish success and history controls | Frontend unit | ✅ `frontend/src/components/__tests__/search-filter-controls.test.tsx::uses Spanish for inferred print-history filters` |
| NL016 | preserves explicit outcomes | Edge | Failed/cancelled/any outcome | No implicit completed filter added | Integration | ✅ `backend/tests/integration/modules/search/test_parsing.py::TestParse::test_preserves_explicit_outcomes` |
| NL017 | rejects invalid response envelopes | Error | Unknown sort, extra key, oversized residual | Original text retained | Integration | ✅ `backend/tests/integration/modules/search/test_parsing.py::TestParse::test_rejects_an_invalid_response_envelope` |
| NL018 | excludes hidden context | Error | Nonadministrator without collection grants | No hidden names or printer IDs in chat context | Integration | ✅ `backend/tests/integration/modules/search/test_parsing.py::TestParse::test_excludes_hidden_collection_context` |
| NL019 | discards reply after consent revocation | Error | Opt out while provider is running | No parsed filters published | Integration | ✅ `backend/tests/integration/modules/search/test_parsing.py::TestParse::test_discards_reply_after_user_revokes_consent` |
| NL020 | keeps personal preferences isolated | Happy | Two users; one timezone override/reset | Other user and instance unchanged; fallback restored | Integration | ✅ `backend/tests/integration/modules/search/test_parsing.py::TestPreferences::test_keeps_personal_preferences_isolated` |
| NL021 | defaults personal consent off | Edge | No preference row | No insert; disabled capability and UTC fallback | Integration | ✅ `backend/tests/integration/modules/search/test_parsing.py::TestPreferences::test_defaults_to_disabled_without_inserting_a_row` |
| NL022 | returns a filter-only sorted page | Happy | No residual text; multiple Models | Requested order with usable next page | Integration | ✅ `backend/tests/integration/modules/search/test_retrieval.py::TestStructuredRetrieval::test_serves_a_filter_only_query_in_requested_order` |
| NL023 | applies history to visual results | Happy | Local multiview; absent then qualifying job | Only qualifying geometry returned | Integration | ✅ `backend/tests/integration/modules/search/test_visual_index.py::TestFilteredVisualQuery::test_applies_actual_history_to_visual_results` |
| NL024 | keeps unexpected error traces private | Error | DEBUG; exception contains raw q | Generic 500; class-only diagnostic without original query | Integration | ✅ `backend/tests/integration/api/v1/test_search.py::TestSearchErrorPrivacy::test_omits_raw_query_from_unexpected_error_traces` |
| NL025 | redacts HTTP URL objects | Error | httpx URL object in formatter arguments | Query removed without corrupting status formatting | Unit | ✅ `backend/tests/unit/core/test_logging.py::TestQueryUrlObjects::test_redacts_an_http_client_url_object` |
| NL026 | preserves the printer-filter permission boundary | Error | Nonadministrator selects printer ID | 403 | Integration | ✅ `backend/tests/integration/api/v1/test_search.py::TestStructuredSearch::test_preserves_admin_only_printer_filter_policy` |
| NL027 | validates ranges on every browse API | Error | Min equals exclusive max | 422 without server exception | Integration | ✅ `backend/tests/integration/api/v1/test_search.py::TestStructuredSearch::test_rejects_inconsistent_history_ranges_in_browse` |
| NL028 | uses joint predicates on PostgreSQL | Error | Split jobs plus one qualifying Model | Same-job result and facet count | Integration | ✅ `backend/tests/integration/postgres/test_search_passages.py::TestStructuredHistory::test_applies_joint_print_predicates_on_postgres` |
| NL029 | searches actual history through the HTTP parser | Happy | Real local contract server; 10799/10800 second jobs | Closed response, correct search result, normalized Saved View | E2E | ✅ `backend/tests/e2e/test_search_parsing.py::TestNaturalLanguageSearch::test_searches_actual_print_history_from_a_parsed_sentence` |
| NL030 | roundtrips a normalized Saved View | Happy | Residual, absolute dates, actual duration, sort | Exact persisted fields without ranking or LLM envelope | Integration | ✅ `backend/tests/integration/modules/library/test_saved_views.py::TestSearchHistoryView::test_roundtrips_normalized_history_with_sort` |
| NL031 | removes only the selected restriction | Happy | Successful-print chip removed | Other chips retained; no repeated POST | Frontend unit | ✅ `frontend/src/components/__tests__/search-filter-controls.test.tsx::removes only the chosen filter without parsing again` |
| NL032 | edits a duration chip | Happy | Change 10800 to 7200 | Canonical URL updated; no repeated POST | Frontend unit | ✅ `frontend/src/components/__tests__/search-filter-controls.test.tsx::edits an inferred duration without parsing again` |
| NL033 | retains the original text after HTTP failure | Error | Parser endpoint unavailable | Readable fallback reason and original search | Frontend unit | ✅ `frontend/src/components/__tests__/search-filter-controls.test.tsx::falls back to the original search on parser failure` |
| NL034 | keeps a rejected preference draft | Error | Invalid IANA timezone | Visible error; input retained | Frontend unit | ✅ `frontend/src/components/__tests__/search-preferences.test.tsx::keeps the draft after a rejected timezone` |
| NL035 | hides unavailable personal controls | Edge | No instance parser capability | No personal enable control | Frontend unit | ✅ `frontend/src/components/__tests__/search-preferences.test.tsx::hides the option when the instance has no available parser` |
| NL036 | bounds concurrent parsing | Error | Admission lane full | No queued work or egress; original query | Integration | ✅ `backend/tests/integration/modules/search/test_parsing.py::TestParseAdmission::test_rejects_excess_work_without_egress` |
| NL037 | bounds context before egress | Error | Context exceeds endpoint cap | Original query; no provider call | Integration | ✅ `backend/tests/integration/modules/search/test_parsing.py::TestParseAdmission::test_bounds_the_context_before_egress` |
| NL038 | renders offline preference DDL | Happy | Offline Alembic range | Reviewable CREATE TABLE output | Integration | ✅ `backend/tests/integration/db/migrations/test_search_preferences_migration.py::TestSearchPreferencesMigration::test_renders_offline_ddl` |
| NL039 | requires signed-in preference/parser access | Error | Anonymous GET/PATCH/POST | 401 | Integration | ✅ `backend/tests/integration/api/v1/test_search.py::TestSearchParseAuthentication::test_requires_a_signed_in_user` |
| NL040 | transports history through all browser readers | Happy | List/page/outliner/facets; zero lower duration | Exact query parameters | Frontend unit | ✅ `frontend/src/lib/api/__tests__/models/browse.test.ts::passes real duration bounds through every browse reader` |

W15 evidence: 130 backend checks passed together; additional browse API,
PostgreSQL and local visual-filter checks passed. Browser flow passed against
the actual backend and a local HTTP contract server; desktop/mobile screenshots
were inspected. The checks establish wiring and consent, not the quality of a
specific language model. Query/error logging regressions were reproduced before
fixing the URL-object and unexpected-debug-exception paths.

## W13 — optional sparse expansion

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| SP001 | uses a pinned sparse export | Happy | registry manifest | immutable asset digests, vocabulary, pooling and term cap | Unit | ✅ `unit/modules/inference/test_model_registry.py::TestModelRegistry::test_pins_the_sparse_document_export` |
| SP002 | rejects incompatible sparse manifests | Error | invalid cap, graph, canary or vocabulary | validation error | Unit | ✅ `unit/modules/inference/test_model_registry.py::TestModelRegistry::test_rejects_invalid_sparse_contracts` |
| SP003 | preserves continuous expansion weights | Happy | real local sparse graph | bounded weighted terms independent of original text | Integration | ✅ `integration/modules/inference/test_sparse.py::TestSparse::test_preserves_continuous_expansion_weights` |
| SP004 | rejects malformed sparse worker outputs | Error | nonfinite, duplicated, excessive or mismatched terms | bounded safe error | Integration | ✅ `integration/modules/inference/test_sparse.py::TestSparse::test_rejects_malformed_sparse_pipe_results` |
| SP005 | contains sparse inference failures | Error | stalled or oversized worker | deadline and shared capacity enforced | Integration | ✅ `integration/modules/inference/test_sparse.py::TestSparse::test_contains_a_stalled_sparse_worker` |
| SP006 | discloses the pinned sparse catalog | Happy | installed sparse export | immutable sparse metadata; no MRL dimensions | Integration | ✅ `integration/api/v1/test_inference_models.py::TestInferenceModels::test_discloses_the_pinned_sparse_catalog` |
| SP007 | protects the enabled sparse model from eviction | Edge | active sparse setting | cache removal rejected | Integration | ✅ `integration/modules/search/test_expansion_worker.py::TestExpansionProcessor::test_keeps_an_enabled_model_in_the_cache` |
| SP008 | keeps expansion disabled by default | Edge | ordinary installation | original lexical results without inference | Integration | ✅ `integration/api/v1/test_inference_models.py::TestInferenceModels::test_keeps_sparse_expansion_off_by_default` |
| SP009 | materializes expansion after explicit opt in | Happy | eligible canonical passages | weighted rows with input hash and recipe | Integration | ✅ `integration/modules/search/test_expansion_worker.py::TestExpansionProcessor::test_materializes_opted_in_passages` |
| SP010 | indexes only authorized live passages | Error | hidden or trashed contributors | no inference or postings | Integration | ✅ `integration/modules/search/test_expansion_worker.py::TestExpansionProcessor::test_skips_nonlive_contributors` |
| SP011 | ignores stale expansion after source edits | Edge | changed passage hash | old terms cannot rank | Integration | ✅ `integration/modules/search/test_expansion.py::TestExpansion::test_ignores_ineligible_expansion_rows` |
| SP012 | rejects late expansion publication | Error | settings, permission or input changes during inference | no stale rows committed | Integration | ✅ `integration/modules/search/test_expansion_worker.py::TestExpansionProcessor::test_discards_late_results` |
| SP013 | recovers an expired sparse lease | Edge | worker crash | bounded retry then terminal failure | Integration | ✅ `integration/modules/search/test_expansion_worker.py::TestExpansionProcessor::test_retries_an_expired_lease` |
| SP014 | retains original lexical results during sparse backfill | Edge | partial expanded corpus | original matches remain available | Integration | ✅ `integration/modules/search/test_expansion_worker.py::TestExpansionProcessor::test_retains_original_search_during_backfill` |
| SP015 | removes expansion contribution atomically on disable | Happy | completed expansion then off | same request sees only original ranks | Integration | ✅ `integration/modules/search/test_expansion.py::TestExpansion::test_removes_contribution_immediately_on_disable` |
| SP016 | bounds the expansion contribution | Edge | large sparse weights | bounded score without repeated tokens | Integration | ✅ `integration/modules/search/test_expansion.py::TestExpansion::test_ranks_separate_expansion_terms` |
| SP017 | filters sparse candidates before ranking | Error | unauthorized or structured-filtered passages | no leaked candidates or evidence | Integration | ✅ `integration/modules/search/test_expansion.py::TestExpansion::test_filters_sparse_candidates_before_ranking` |
| SP018 | preserves original displayed passage text | Happy | expansion-only query hit | human text unchanged | Integration | ✅ `integration/modules/search/test_expansion.py::TestExpansion::test_preserves_original_passage_evidence` |
| SP019 | deletes expansion with its passage | Edge | passage purged | FK cascade removes work and postings | Integration | ✅ `integration/modules/search/test_expansion.py::TestExpansion::test_cascades_sparse_rows_when_a_passage_is_purged` |
| SP020 | upgrades populated databases for sparse expansion | Happy | prior revision plus data | new tables preserve original rows; downgrade restores | Integration | ✅ `integration/db/migrations/test_search_expansion_migration.py::TestSearchExpansionMigration::test_preserves_original_passages_through_upgrade` |
| SP021 | ranks sparse matches on PostgreSQL | Happy | weighted sparse postings | same bounded rank contract as SQLite | Integration | ✅ `integration/postgres/test_search_passages.py::TestStructuredHistory::test_ranks_separate_sparse_postings_on_postgres` |
| SP022 | controls expansion from administrator settings | Happy | local installed model | explicit toggle and missing-model state | Frontend unit | ✅ `frontend/src/components/__tests__/ai-search-settings.test.tsx::saves independent local expansion consent` |
| SP023 | retrieves an expansion-only synonym through the API | Happy | original contained ONNX contract model | search finds original Subject without query inference | E2E | ✅ `e2e/test_search_expansion.py::TestSparseSearch::test_retrieves_a_separately_expanded_synonym` |
| SP024 | measures sparse index cost on the fixed corpus | Happy | frozen corpus and real pinned model | before/after bytes, term counts and quality reported | Integration | ✅ `integration/modules/inference/preplaced_sparse.py::TestPreplacedSparse::test_measures_the_pinned_sparse_profile` |
| SP025 | refuses sparse models for dense generations | Error | installed sparse model | 400 embedding_text_unavailable | Integration | ✅ `integration/api/v1/test_inference_models.py::TestInferenceModels::test_refuses_a_sparse_model_as_a_dense_generation` |
| SP026 | contains sparse native memory | Error | RSS budget exceeded | child terminated with safe OOM error | Integration | ✅ `integration/modules/inference/test_sparse.py::TestSparse::test_contains_sparse_native_memory` |
| SP027 | quarantines an exhausted sparse lease | Error | third expired claim | failed state, cleared token, no further work | Integration | ✅ `integration/modules/search/test_expansion_worker.py::TestExpansionProcessor::test_quarantines_an_exhausted_lease` |
| SP028 | preserves sparse retry budget during contention | Edge | shared compute busy | deferred retry with unchanged attempts | Integration | ✅ `integration/modules/search/test_expansion_worker.py::TestExpansionProcessor::test_preserves_retry_budget_under_compute_backpressure` |
| SP029 | requires an installed sparse model for consent | Edge | uninstalled catalog model | toggle disabled; explicit download uses selected key | Frontend unit | ✅ `frontend/src/components/__tests__/ai-search-settings.test.tsx::requires an installed sparse model before enabling expansion` |
| SP030 | reports actual sparse token truncation | Edge | more than model token limit | truncation true; omitted tail contributes no term | Integration | ✅ `integration/modules/inference/test_sparse.py::TestSparse::test_reports_sparse_token_truncation` |
| SP031 | accepts an empty learned expansion | Edge | no positive terms | valid empty list | Integration | ✅ `integration/modules/inference/test_sparse.py::TestSparse::test_accepts_empty_sparse_outputs` |
| SP032 | validates sparse canaries without library content | Happy | explicit model validation | verified manifest returned | Integration | ✅ `integration/modules/inference/test_sparse.py::TestSparse::test_validates_sparse_canaries_without_a_document` |
| SP033 | rejects sparse text outside the input budget | Error | empty or oversized input | safe input-budget failure | Integration | ✅ `integration/modules/inference/test_sparse.py::TestSparse::test_rejects_sparse_input_outside_budget` |
| SP034 | rejects incompatible sparse native exports | Error | wrong canary, vocabulary, tensor or opset | specific safe verification failure | Integration | ✅ `integration/modules/inference/test_sparse.py::TestSparse::test_rejects_incompatible_sparse_exports` |
| SP035 | records only safe sparse failure codes | Error | provider exception containing passage text | generic durable code; no text leakage | Integration | ✅ `integration/modules/search/test_expansion_worker.py::TestExpansionProcessor::test_records_only_a_safe_failure_code` |
| SP036 | defers sparse work during restore | Edge | maintenance admission closed | no sparse state written | Integration | ✅ `integration/runtime/test_expansion.py::TestExpansionRuntime::test_defers_sparse_work_during_restore` |
| SP037 | waits for sparse work during shutdown | Edge | cancellation during active unit | shutdown awaits bounded lease completion | Integration | ✅ `integration/runtime/test_expansion.py::TestExpansionRuntime::test_shutdown_waits_for_the_sparse_lease` |
| SP038 | acquires a sparse model through verified HTTPS | Happy | explicit acquisition against TLS contract peer | digest verified cache publication after canary | Contract | ✅ `contract/modules/inference/test_model_acquisition.py::TestAcquisition::test_acquires_a_sparse_export_through_the_shared_cache` |
| SP039 | defers sparse work when index capacity is exhausted | Edge | combined durable index exceeds quota | no terms written; retry budget retained | Integration | ✅ `integration/modules/search/test_expansion_worker.py::TestExpansionProcessor::test_defers_expansion_when_the_shared_index_budget_is_full` |
| SP040 | controls real sparse expansion in the browser | Happy | pinned pretrained export | UI opt-in retrieves absent synonym; off preserves originals | Playwright | ✅ `frontend/tests/e2e-real/ai-search/sparse.preplaced.ts::controls local sparse expansion through the browser` |
| SP041 | replays frozen real sparse quality weights | Happy | hash-bound corpus and queries | original and expanded recall@5 both 28/32 | Integration | ✅ `integration/modules/search/retrieval/test_sparse_quality.py::TestSparseQuality::test_measures_frozen_real_sparse_expansion` |
| SP042 | refuses sparse consent without local prerequisites | Error | enabled expansion without selected local model | 400; setting not accepted | Integration | ✅ `integration/api/v1/test_inference_models.py::TestInferenceModels::test_requires_local_sparse_prerequisites` |
| SP043 | does no sparse work while an opt in is disabled | Edge | eligible passage with one capability off | no expansion claim or publication | Integration | ✅ `integration/modules/search/test_expansion_worker.py::TestExpansionProcessor::test_does_no_work_with_an_opt_in_disabled` |

## Restart warm-up and cold-query admission

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| WU001 | returns promptly while a local model is cold | Edge | Cold provider, short query budget | Warming result; model loading is not repeatedly killed | Integration | ✅ `integration/modules/inference/query/test_warmup.py::TestLocalQueryWarmup::test_returns_promptly_while_a_model_is_cold` |
| WU002 | serves queries after background canary warm-up | Happy | Active preplaced model after process restart | Native query fits its ordinary deadline | Integration | ✅ `integration/modules/search/test_model_warmup.py::TestModelWarmup::test_serves_queries_after_background_warmup` |
| WU003 | warms only active locally permitted models | Error | Disabled local inference, inactive or remote generations | No local model load or network request | Integration | ✅ `integration/modules/search/test_model_warmup.py::TestModelWarmup::test_warms_only_active_locally_permitted_models` |
| WU004 | bounds pending warm-up requests | Edge | Repeated requests for many models | At most four distinct model identities retained; no query text | Unit | ✅ `unit/modules/inference/test_warmup.py::TestWarmupRequests::test_bounds_pending_models_without_duplicate_work` |
| WU005 | preserves warm workers while queries wait | Edge | Background load is in progress | Query returns warming without terminating the loader | Integration | ✅ `integration/modules/search/test_model_warmup.py::TestModelWarmup::test_preserves_the_loader_during_cold_queries` |
| WU006 | invalidates readiness when model assets change | Error | Replaced model graph or tokenizer | Old worker cannot count as ready | Integration | ✅ `integration/modules/inference/query/test_warmup.py::TestLocalQueryWarmup::test_invalidates_readiness_after_an_asset_changes` |
| WU007 | stops warm-up when local consent is revoked | Error | Configuration off during canary | Contained load cancelled | Integration | ✅ `integration/modules/search/test_model_warmup.py::TestModelWarmup::test_cancels_loading_after_local_consent_is_revoked` |
| WU008 | preserves vector-cache hits after worker eviction | Edge | Cached query vector; model worker gone | Cached result without another model load | Integration | ✅ `integration/modules/inference/query/test_warmup.py::TestLocalQueryWarmup::test_preserves_cached_vectors_after_worker_eviction` |
| WU009 | keeps local canary warm-up out of the lexical path | Happy | Cold active text generation | Lexical response available while native model loads | E2E | ✅ `e2e/test_search_local.py::TestLocalSearch::test_keeps_search_available_during_restart_warmup` |
| WU010 | cancels warm-up before shutdown completes | Edge | Shutdown during contained load | Cancellation drains bounded work | Integration | ✅ `integration/runtime/test_model_warmup.py::TestModelWarmupRuntime::test_cancels_loading_before_shutdown_completes` |
| WU011 | defers model loading during restore | Edge | restore maintenance active | no loading admitted | Integration | ✅ `integration/runtime/test_model_warmup.py::TestModelWarmupRuntime::test_defers_model_loading_during_restore` |

## Core acceptance coverage audit

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| AC001 | preserves Artifact unit identity on valid boundaries | Happy | valid Artifact ID, component and recipe | stable key and parsed component | Unit | ✅ `core/inference/test_units.py::TestUnitIdentity::test_preserves_valid_artifact_component_identity` |
| AC002 | binds point recipes to the paired encoder | Happy | valid point Space | exact recipe round-trip | Unit | ✅ `core/search/test_point_inputs.py::TestPointRecipe::test_binds_the_exact_paired_encoder` |
| AC003 | rejects incompatible point recipe declarations | Error | bad hash, version or profile | safe recipe error | Unit | ✅ `core/search/test_point_inputs.py::TestPointRecipe::test_rejects_incompatible_recipe_declarations` |
| AC004 | rejects mismatched point Spaces | Error | different modality, profile or alignment | safe recipe error | Unit | ✅ `core/search/test_point_inputs.py::TestPointRecipe::test_rejects_mismatched_spaces` |
| AC005 | rejects malformed point recipe JSON | Error | malformed or unknown fields | safe recipe error | Unit | ✅ `core/search/test_point_inputs.py::TestPointRecipe::test_rejects_malformed_recipe_json` |
| AC006 | includes the document prefix within the input budget | Edge | prefix plus document at and above cap | bounded text and accurate truncation | Unit | ✅ `core/search/test_text_inputs.py::TestDocumentInput::test_counts_prefix_characters_inside_the_budget` |
| AC007 | rejects prefixes consuming the entire budget | Error | prefix at or above cap | safe prefix-budget error | Unit | ✅ `core/search/test_text_inputs.py::TestDocumentInput::test_rejects_prefixes_consuming_the_document_budget` |
| AC008 | rejects malformed text recipe JSON | Error | malformed or unknown fields | safe recipe error | Unit | ✅ `core/search/test_text_inputs.py::TestTextRecipe::test_rejects_malformed_recipe_json` |
| AC009 | reports the absent image runtime | Error | Pillow unavailable | safe capability error | Unit | ✅ `core/inference/test_images.py::TestDecodeImage::test_reports_an_absent_image_runtime` |
| AC010 | rejects unsupported decoded image formats | Error | GIF body declared as PNG | safe unsupported-type error | Unit | ✅ `core/inference/test_images.py::TestDecodeImage::test_rejects_an_unsupported_decoded_format` |
| AC011 | rejects degenerate point normalization | Error | near-zero geometry radius | safe geometry error | Unit | ✅ `core/inference/test_points.py::TestPointInput::test_rejects_near_zero_geometry_radius` |
| AC012 | rejects nonpoint grouping inputs | Error | text input offered to point grouping | safe point-input error | Unit | ✅ `core/inference/test_points.py::TestPointInput::test_rejects_nonpoint_grouping_inputs` |
| AC013 | reports float index code width | Happy | float32 transform | four bytes per coordinate | Unit | ✅ `core/inference/test_transforms.py::TestCodeBoundaries::test_reports_float_code_width` |
| AC014 | rejects invalid compressed distance blocks | Error | bad code/query lengths or block size | safe code error | Unit | ✅ `core/inference/test_transforms.py::TestCodeBoundaries::test_rejects_invalid_distance_blocks` |
| AC015 | rejects invalid shortlist budgets | Error | invalid limit or scan bound | safe query-budget error | Unit | ✅ `core/inference/test_transforms.py::TestShortlistCodes::test_rejects_invalid_shortlist_budgets` |
| AC016 | returns stable bounded compressed shortlists | Edge | short, empty or non-improving candidate streams | ordered IDs and accurate scan/truncation | Unit | ✅ `core/inference/test_transforms.py::TestShortlistCodes::test_finishes_exhausted_candidate_streams` |
| AC017 | rejects incompatible visual recipe declarations | Error | bad recipe metadata | safe visual-recipe error | Unit | ✅ `core/search/test_visual_inputs.py::TestVisualRecipeAdmission::test_rejects_incompatible_recipe_declarations` |
| AC018 | rejects malformed visual recipe JSON | Error | malformed or unknown fields | safe visual-recipe error | Unit | ✅ `core/search/test_visual_inputs.py::TestVisualRecipeAdmission::test_rejects_malformed_recipe_json` |

## Master-switch acceptance audit

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| MS001 | suppresses caption egress with the master off | Error | caption opt-in retained, AI master off | no render/provider request or caption work | Integration | ✅ `integration/modules/search/test_captions.py::TestCaptions::test_suppresses_caption_egress_with_the_master_off` |
| MS002 | suppresses parsing with the master off | Error | personal and NL opt-ins retained, AI master off | original query returned; no provider request | Integration | ✅ `integration/modules/search/test_parsing.py::TestParse::test_suppresses_parsing_with_the_master_off` |
| MS003 | discards parsed output after master revocation | Error | master disabled during remote completion | original query returned; no filters applied | Integration | ✅ `integration/modules/search/test_parsing.py::TestParse::test_discards_parsed_output_after_master_revocation` |
| MS004 | fences caption egress after master revocation | Error | master disabled during rendering | no image sent to provider | Integration | ✅ `integration/modules/search/test_captions.py::TestCaptions::test_rechecks_consent_after_rendering` |
| MS005 | fences caption egress after actor loss | Error | actor disabled during rendering | no image sent to provider | Integration | ✅ `integration/modules/search/test_captions.py::TestCaptions::test_fences_caption_egress_after_actor_loss` |

## Localization acceptance audit

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| UI001 | localizes authored search copy | Edge | sparse size label and date example | no untranslated presentation copy; both catalogs complete | Frontend unit | ✅ `frontend/tests/repo/i18n-coverage.test.ts::translationCoverage::has no unwrapped authored JSX copy` |

## Caption identity acceptance audit

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| CI001 | hides cached captions after an identity change | Error | another tab changes the authenticated user | previous user's private text disappears before the new read completes | Frontend unit | ✅ `frontend/src/components/__tests__/subject-caption.test.tsx::Subject caption::hides cached captions after an identity change` |
| CI002 | discards a caption draft after an identity change | Error | another user replaces the editor's session | previous user's unsaved draft disappears | Frontend unit | ✅ `frontend/src/components/__tests__/subject-caption.test.tsx::Subject caption::discards a caption draft after an identity change` |
| CI003 | avoids caption reads without an authenticated user | Error | no authenticated user | no caption request or private text rendered | Frontend unit | ✅ `frontend/src/components/__tests__/subject-caption.test.tsx::Subject caption::avoids caption reads without an authenticated user` |
| CI004 | discards private image queries after an identity change | Error | image search belongs to the previous user | preview released; no image submitted as the new user | Frontend unit | ✅ `frontend/src/pages/__tests__/search.test.tsx::Search page::discards private image queries after an identity change` |

## Image input acceptance audit

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| IM001 | searches with one dropped image | Happy | ready visual index, one static image dropped | selected image rendered and supplied to the query | Frontend unit | ✅ `frontend/src/components/__tests__/search-image-input.test.tsx::Search image input::searches with one dropped image` |
| IM002 | refuses dropped images without visual capability | Error | no ready visual index | no selection; controls unavailable | Frontend unit | ✅ `frontend/src/components/__tests__/search-image-input.test.tsx::Search image input::refuses dropped images without visual capability` |
| IM003 | rejects invalid dropped images | Error | empty, oversized or unsupported image | accessible error; no query input accepted | Frontend unit | ✅ `frontend/src/components/__tests__/search-image-input.test.tsx::Search image input::rejects invalid dropped images` |
| IM004 | rejects multiple dropped images | Error | two images dropped at once | one-image error; no partial query | Frontend unit | ✅ `frontend/src/components/__tests__/search-image-input.test.tsx::Search image input::rejects multiple dropped images` |
| IM005 | accepts a camera image through the same private input | Happy | camera capture on a supported device | environment-camera hint, validated image selected | Frontend unit | ✅ `frontend/src/components/__tests__/search-image-input.test.tsx::Search image input::accepts a camera image through the same private input` |
| IM006 | preserves a selection when the picker is cancelled | Edge | selected image, no replacement file | preview and query remain unchanged | Frontend unit | ✅ `frontend/src/components/__tests__/search-image-input.test.tsx::Search image input::preserves a selection when the picker is cancelled` |
| IM007 | exposes image controls in Spanish | Edge | Spanish locale | localized camera and drop instructions | Frontend unit | ✅ `frontend/src/components/__tests__/search-image-input.test.tsx::Search image input::exposes image controls in Spanish` |
| IM008 | retrieves the source Model from a dropped image | Happy | real local CLIP index, browser file drop | source Model found with visual evidence | E2E | ✅ `frontend/tests/e2e-real/ai-search/search.spec.ts::AI Search::retrieves related geometry through local visual indexes` |

### Final architecture gate

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| AR001 | keeps application dependencies acyclic | Edge | all AI Search providers and consumers installed | no deferred import cycles, private cross-owner imports or framework dependency inversions | Repo | ✅ `backend/tests/repo/test_architecture.py::TestArchitecture::test_application_respects_its_recorded_boundaries` |

Final repair evidence: 236 backend owner/API/architecture checks passed; 26 image/page
checks passed after hiding duplicate native picker controls. The real local CLIP
browser flow passed drag-and-drop, camera-picker submission, clearing, related
Models, and desktop/mobile overflow assertions. Both viewport screenshots were
inspected. Filter controls passed 9 tests in an isolated run; earlier contention
caused timeout failures, so the complete frontend lane remains to be rerun.

### Related feature availability

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| AF001 | omits Family annotations when unavailable | Edge | Families package absent | no Family routes or annotations; ordinary Model remains usable | Integration | ✅ `integration/bootstrap/test_optional_features.py::TestOptionalFeatures::test_omits_family_annotations_when_the_package_is_absent` |
| AF002 | rejects unavailable Family filters | Error | Families provider absent; explicit Family constraint | capability error; query is not broadened | Integration | ✅ `integration/bootstrap/test_optional_features.py::TestOptionalFeatures::test_rejects_family_filters_when_the_provider_is_absent` |

### SQLite activation under concurrent writes

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| CW001 | drains competing SQLite writes before activation verification | Edge | ready replacement; concurrent writer in WAL mode | writer waits through verification; one new active generation commits without a stale-snapshot error | Integration | ✅ `integration/modules/search/generations/test_cutover_concurrency.py::TestCutoverConcurrency::test_drains_competing_writes_before_verifying_activation` |

Final independence/activation evidence: 168 optional-composition and Family API
checks passed; 22 activation/migration/independence checks passed after the SQLite
writer-lock repair. Visual/PostgreSQL activation checks passed 29 tests. The final
independence and parsing run passed 33 tests, including an assertion that no Family
table is queried during upload, four-Subject Search, and generation replacement.
Trash/restoration acceptance checks passed 14 tests.

Frontend coverage: all 2,347 app tests passed (82.43% statements, 77.23% branches);
domain measured 95.54%/93.20%, shared UI 98.78%/97.60%. The three improved app
branch floors were raised, and every frontend floor passed. The initial backend
run exposed storage-reserve and resource-server capacity issues; the later full
resource lane passed 269 tests after those corrections. The final verification
section below records the current backend coverage audit.

### Reproducible scale harness

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| SC001 | compares exact native retrieval against the shipped float scanner | Happy | seeded normalized vectors; unrestricted and restricted SQL scopes | native recall and timing measured against identical authorized floats | Repo | ✅ `repo/test_vector_scale.py::TestVectorScale::test_compares_identical_authorized_neighbors` (256-vector harness check; 500k timing recorded separately) |
| SC002 | replays the scale snapshot without loading a vector extension | Edge | durable-only snapshot; fresh standard SQLite connection | same row count, digest and neighbors through the float fallback | Repo | ✅ `repo/test_vector_scale.py::TestVectorScale::test_recovers_floats_without_a_vector_extension` (256-vector harness check) |

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| SC003 | keeps scale replicas consistent with ordinary content refresh | Edge | frozen Model text/vector pairs replicated with unique identities | vector retained after production refresh; exact lexical statistics and FTS results | Repo | ✅ `repo/test_search_scale.py::TestReplicateModels::test_replicas_retain_searchability_after_maintenance` |

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| SC004 | seeds backfill without precomputed active vectors | Happy | replicated ordinary Models and lexical passages | all replicas searchable; zero vector rows | Repo | ✅ `repo/test_search_scale.py::TestReplicateModels::test_builds_an_unembedded_backfill_corpus` |
| SC005 | measures workers spawned from executor threads | Edge | real Python child launched by a background thread | sampler records parent and child RSS/CPU while alive | Repo | ✅ `repo/test_process_metrics.py::TestSampleProcesses::test_samples_workers_owned_by_an_executor_thread` |

### Native protocol acceptance

The contained-provider tests prove process isolation. These cases assert the
worker's serialized contract across every supported manifest family, so native
CPU execution is also visible to the branch-coverage audit.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
| --- | --- | --- | --- | --- | --- | --- |
| NP001 | executes every supported manifest family | Happy | Original CC0 CLS, mean, point and sparse graphs; serialized requests | Exact normalized vectors or continuous sparse weights; truncation and identity retained | Integration | ✅ `integration/modules/inference/test_worker.py::TestNativeProtocol::test_executes_every_supported_manifest_family` |
| NP002 | rejects cross-profile sparse envelopes | Error | Sparse identity with dense inputs, dense Space metadata or foreign hash | Stable mismatch before inference output | Integration | ✅ `integration/modules/inference/test_worker.py::TestNativeProtocol::test_rejects_cross_profile_sparse_envelopes` |
| NP003 | rejects hidden external ONNX tensor data | Error | External initializers or tensor attributes, including nested graphs and sparse tensors | Stable rejection before the runtime can open the referenced path | Unit | ✅ `unit/modules/inference/test_onnx_cpu.py::TestOnnxCpuProvider::test_rejects_hidden_external_tensor_data` |

### Family browsing compatibility

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
| --- | --- | --- | --- | --- | --- | --- |
| FB001 | pages collapsed cards without relevance scores | Edge | Relevance requested without a ranking leg; SQLite/PostgreSQL | Date-based cursor yields every card once | Integration | ✅ `integration/postgres/test_families.py::TestFamilyBrowse::test_pages_family_union_on_supported_databases` — relevance cases pass on both databases |
| FB002 | pages unranked Families by recent change | Edge | Relevance requested, with/without name query; SQLite/PostgreSQL | Newest Family first; next page returns older Family without cursor failure | Integration | ✅ `integration/postgres/test_families.py::TestFamilyBrowse::test_pages_unranked_families_by_recent_change` |
| NP004 | frames visual worker outputs for every profile | Happy | Original tetrahedron, thumbnail/multiview/point recipes | Complete bounded RGB/point frames decode to expected shapes | Integration | ✅ `integration/modules/media/test_visual_worker.py::TestMain::test_frames_every_visual_profile` |
| NP005 | refuses oversized visual worker messages | Error | Recipe beyond request cap or reply above configured cap | Nonzero exit with no partial output | Integration | ✅ `integration/modules/media/test_visual_worker.py::TestMain::test_refuses_oversized_messages` |
| NP006 | sanitizes visual worker failures | Error | Invalid recipe or unsupported source kind | Framed stable error excludes source path and exception detail | Integration | ✅ `integration/modules/media/test_visual_worker.py::TestMain::test_sanitizes_failures` |

### Semantic leg boundaries

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
| --- | --- | --- | --- | --- | --- | --- |
| SL001 | reuses text vectors for Model queries | Happy | Active text generation, existing Model vector | Related Subject returned; source excluded; no query inference request | Integration | ✅ `integration/modules/search/test_retrieval.py::TestSearch::test_reuses_text_vectors_for_model_queries` |
| SL002 | denies stale semantic admission | Error | Missing/inactive user, changed auth version, or revoked master consent | Unavailable leg with no inference request or reader lease | Integration | ✅ `integration/modules/search/test_retrieval.py::TestSearch::test_denies_stale_semantic_admission` |
| SL003 | rejects incompatible semantic inputs | Error | Image against text leg or text beyond recipe budget | Stable leg failure before inference | Integration | ✅ `integration/modules/search/test_retrieval.py::TestSearch::test_rejects_incompatible_semantic_inputs` |
| SL004 | rejects invalid sparse worker budgets | Error | Zero, boolean or above-cap thread count | Stable thread-budget rejection before a child is started | Integration | ✅ `integration/modules/inference/test_sparse.py::TestSparse::test_rejects_invalid_worker_budgets` |

### Invalid configuration inputs

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
| --- | --- | --- | --- | --- | --- | --- |
| IV001 | rejects invalid timezone lengths | Error | Empty or over-128-character IANA identifier | Stable timezone rejection | Unit | ✅ `unit/core/test_timezones.py::TestTimezoneName::test_rejects_invalid_lengths` |
| IV002 | rejects inconsistent caption edits | Error | Missing edit text, whitespace, or text attached to another action | HTTP 422; no caption row created | Integration | ✅ `integration/api/v1/test_captions.py::TestCaptionAPI::test_rejects_inconsistent_edits` |
| IV003 | rejects an unusable selected chat endpoint | Error | Missing endpoint or embedding-only endpoint | HTTP 400; previous settings preserved | Integration | ✅ `integration/api/v1/test_inference.py::TestUpdateSettings::test_rejects_an_unusable_chat_endpoint` |
| IV004 | rejects an unavailable sparse model | Error | Unknown selected model with sparse consent | HTTP 400; sparse expansion remains disabled | Integration | ✅ `integration/api/v1/test_inference.py::TestUpdateSettings::test_rejects_an_unavailable_sparse_model` |
| IV005 | degrades corrupt active capability metadata | Error | Active Space has invalid JSON or wrong object shape | Lexical-only status and a stable degraded reason | Integration | ✅ `integration/modules/search/test_status.py::TestStatus::test_degrades_corrupt_active_metadata` |
| IV006 | reports a missing local inference runtime | Error | Installed local model but missing ONNX runtime module | Local semantic capability unavailable without trying inference | Integration | ✅ `integration/modules/search/test_status.py::TestStatus::test_reports_a_missing_local_runtime` |
| IV007 | skips unusable active models during warmup | Error | Corrupt active Space or stopped warmer | No warmup work admitted | Integration | ✅ `integration/modules/search/test_model_warmup.py::TestModelWarmup::test_skips_unusable_active_models` |
| IV008 | keeps caption source identities typed | Error | Non-Model Subject shares an ID with a Model containing eligible geometry | No Model Artifact returned for another Subject type | Integration | ✅ `integration/modules/search/test_caption_source.py::TestSource::test_keeps_source_identities_typed` |

### Background task recovery

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
| --- | --- | --- | --- | --- | --- | --- |
| RT001 | resumes captions after a failed work unit | Error | First bounded unit raises, next unit can run | Scheduler stays alive; next unit runs; logs omit exception payload | Integration | ✅ `integration/runtime/test_captions.py::TestRunCaptions::test_resumes_after_a_failed_unit` |
| RT002 | records failed model acquisitions durably | Error | Real HTTPS host serves corrupt model bytes | Failed job carries stable digest error; no installed or partial model remains | Contract | ✅ `contract/runtime/test_model_acquisition.py::TestModelAcquisition::test_records_a_failed_transfer` |
| RT003 | cancels an admitted model transfer | Edge | Real HTTPS response held while cancellation or shutdown begins | Job fails as cancelled; shutdown waits; partial model removed | Contract | ✅ `contract/runtime/test_model_acquisition.py::TestModelAcquisition::test_cancels_an_admitted_transfer` |

### HTTP admission and resource capacity

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
| --- | --- | --- | --- | --- | --- | --- |
| HA001 | retrieves visual matches from a valid image upload | Happy | Active local visual generation; PNG through the authenticated API | Model evidence returned with no-store caching | Integration | ✅ `integration/modules/search/test_visual_index.py::TestVisualIndex::test_retrieves_visual_matches_from_a_valid_image_upload` |
| HA002 | estimates a proposed local generation | Happy | Verified preplaced text model | Bounded estimate returned without creating a generation | Integration | ✅ `integration/api/v1/test_inference.py::TestProposeGeneration::test_estimates_a_proposed_local_generation` |
| HA003 | rejects unknown local generation models | Error | Unknown model identity for proposal or estimate | Stable HTTP 400 without creating a generation | Integration | ✅ `integration/api/v1/test_inference.py::TestProposeGeneration::test_rejects_unknown_local_generation_models` |
| HA004 | rejects an unconfigured environment import | Error | No embedding environment preset | HTTP 400 with stable error | Integration | ✅ `integration/api/v1/test_inference.py::TestCreateEndpoint::test_rejects_an_unconfigured_environment_import` |
| HA005 | removes an unreferenced local model | Happy | Verified preplaced model, no generation references | HTTP 204 and model files removed | Integration | ✅ `integration/api/v1/test_inference_models.py::TestInferenceModels::test_removes_an_unreferenced_local_model` |
| HA006 | rejects validation of a missing local model | Error | Unknown model identity | Stable HTTP 400 | Integration | ✅ `integration/api/v1/test_inference_models.py::TestInferenceModels::test_rejects_validation_of_a_missing_local_model` |
| RS001 | completes isolated S3 workflows on a small test disk | Edge | Many distinct buckets on the pinned SeaweedFS server | Resource suite completes without exhausting auto-sized volume slots | Contract/E2E | ✅ `contract/modules/storage/test_storage_backend.py` plus complete resource lane: 269 passed in 33m16s; `backend-resources-capacity-fixed.log` |

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| CB001 | rejects foreign compressed table claims | Error | Generation points at another table | Replacement and shortlist both rejected; original bytes retained | Integration | ✅ `integration/modules/search/test_code_index.py::TestShortlist::test_rejects_foreign_compressed_table_claims` |
| CB002 | refreshes every member of a large Collection | Edge | More than one projection page of Models | Every passage reflects changed Collection metadata | Integration | ✅ `integration/modules/search/test_projection.py::TestContentProjection::test_refreshes_every_member_of_a_large_collection` |
| CB003 | projects a newly attached provenance source | Happy | Source added after initial Model projection | Provenance tag becomes searchable | Integration | ✅ `integration/modules/search/test_projection.py::TestContentProjection::test_projects_a_newly_attached_provenance_source` |
| CB004 | rejects invalid cache filesystem layouts | Error | File in place of directory, unavailable path, malformed or oversized manifest | Stable error without changing files | Integration | ✅ `integration/modules/inference/test_model_cache.py::TestModelCache::test_rejects_a_file_in_place_of_the_cache`; `integration/modules/inference/test_model_cache.py::TestModelCache::test_rejects_invalid_cached_manifests` |
| CB005 | bounds cache inventory and filesystem entries | Error | More than 64 models or 1024 files | Stable entry-limit error | Integration | ✅ `integration/modules/inference/test_model_cache.py::TestModelCache::test_bounds_the_model_inventory`; `integration/modules/inference/test_model_cache.py::TestModelCache::test_bounds_files_during_capacity_calculation` |
| CB006 | refuses cache symlinks during capacity calculation | Error | Unknown file points outside cache | Stable rejection; outside data preserved | Integration | ✅ `integration/modules/inference/test_model_cache.py::TestModelCache::test_refuses_cache_symlinks_during_capacity_calculation` |
| CB007 | bounds waiting for a pinned model cache | Error | Exclusive lock held past deadline | Compute-busy error; cache remains usable after release | Integration | ✅ `integration/modules/inference/test_model_cache.py::TestModelCache::test_bounds_waiting_for_a_pinned_model_cache` |

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| NB001 | rejects malformed native worker replies | Error | Real child sends invalid JSON, foreign identity/counts, oversized/trailing frame or exits | Stable error; no invalid vectors escape | Integration | ✅ `integration/modules/inference/test_local.py::TestLocalProvider::test_rejects_malformed_native_worker_replies` |
| NB002 | refuses invalid local thread budgets | Error | Zero or above-cap worker threads | Stable error before worker startup | Integration | ✅ `integration/modules/inference/test_local.py::TestLocalProvider::test_refuses_invalid_local_thread_budgets` |
| NB003 | drops semantic output after consent revocation | Error | Master consent revoked while embedding request is in flight | Unavailable leg, no passages, released lease | Integration | ✅ `integration/modules/search/test_retrieval.py::TestSearch::test_drops_semantic_output_after_consent_revocation` |
| NB004 | records unexpected caption renderer failures safely | Error | Renderer raises with private details | Retryable stable failure; no caption text or provider call | Integration | ✅ `integration/modules/search/test_captions.py::TestCaptions::test_records_unexpected_caption_renderer_failures_safely` |
| NB005 | finishes exhausted durable caption jobs | Error | Expired third attempt has a running BackgroundJob | Caption and job both fail with bounded error | Integration | ✅ `integration/modules/search/test_captions.py::TestCaptions::test_finishes_exhausted_durable_caption_jobs` |
| DT001 | refuses unsupported database transfer boundaries | Error | Wrong dialect or invalid batch size | Stable refusal before any writes | Integration | ✅ `integration/modules/administration/test_database_transfer.py::TestDatabaseTransfer::test_refuses_invalid_transfer_batches`; `integration/modules/administration/test_database_transfer.py::TestDatabaseTransfer::test_refuses_unsupported_transfer_dialects` |
| DT002 | refuses unknown or outdated transfer sources | Error | Source has extra table or old migration stamp | Target remains empty after refusal | Integration | ✅ `integration/modules/administration/test_database_transfer.py::TestDatabaseTransfer::test_refuses_unknown_or_outdated_transfer_sources` |

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| GR001 | upgrades an active local text generation for captions | Happy | Native v1 generation, edited caption, administrator consent | Exactly one v2 replacement queued; v1 keeps serving | Integration | ✅ `integration/modules/search/test_generations.py::TestEnsureCaptionRecipe::test_upgrades_an_active_local_text_generation_for_captions` |
| GR002 | defers a local caption upgrade without its prerequisites | Error | Disabled local inference, missing model, inactive actor or no active generation | No replacement queued; prior generation unchanged | Integration | ✅ `integration/modules/search/test_generations.py::TestEnsureCaptionRecipe::test_defers_a_local_caption_upgrade_without_its_prerequisites` |
| GR003 | rejects incompatible generation modalities | Error | Text model selected for visual or point generation | Stable alignment failure before any generation is created | Integration | ✅ `integration/modules/search/test_generations.py::TestPrepare::test_rejects_incompatible_generation_modalities` |
| VI001 | refuses incomplete visual worker batches | Error | Multiview source with fewer than six views | No partial vectors published | Integration | ✅ `integration/modules/search/test_visual_index.py::TestVisualIndex::test_refuses_incomplete_visual_worker_batches` |
| VI002 | discards stale visual worker failures | Error | Source changes before failure recording | No stale quarantine entry | Integration | ✅ `integration/modules/search/test_visual_index.py::TestVisualIndex::test_discards_stale_visual_worker_failures` |
| VI003 | sanitizes visual failure details | Error | Worker error contains a path | Generic persisted error with no path | Integration | ✅ `integration/modules/search/test_visual_index.py::TestVisualIndex::test_sanitizes_visual_failure_details` |

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| RF001 | rejects an actor revoked during image upload | Error | Actor deactivated after admission but before decode/retrieval | Forbidden failure before inference | Integration | ✅ `integration/api/v1/test_search.py::TestSearch::test_rejects_an_actor_revoked_during_image_upload` |
| RF002 | reauthorizes retries after vector withdrawal | Edge | Ranked vectors withdrawn before hydration, with/without actor loss | No withdrawn passages; at most one fresh retry | Integration | ✅ `integration/modules/search/test_retrieval.py::TestSearch::test_reauthorizes_retries_after_vector_withdrawal` |
| RF003 | ignores visual legs for non-Model queries | Edge | Active visual generation and Document-only type filter | No visual candidates or inference | Integration | ✅ `integration/modules/search/test_visual_index.py::TestVisualIndex::test_ignores_visual_legs_for_nonmodel_queries` |
| RF004 | refuses remote endpoints for visual input | Error | Text endpoint presented to visual query leg | Stable unavailable-image failure before egress | Integration | ✅ `integration/modules/search/test_visual_index.py::TestVisualIndex::test_refuses_remote_endpoints_for_visual_input` |
| RF005 | rejects unavailable source Models | Error | Model query references an unknown Model | Stable not-found response | Integration | ✅ `integration/modules/search/test_retrieval.py::TestSearch::test_rejects_unavailable_source_models` |
| RF006 | bounds direct search inputs | Error | Invalid result limit or non-image upload input | Stable input refusal before retrieval | Integration | ✅ `integration/modules/search/test_retrieval.py::TestSearch::test_bounds_direct_result_limits`; `integration/modules/search/test_retrieval.py::TestSearch::test_rejects_nonimage_upload_inputs` |

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| NI001 | refuses foreign native table claims | Error | Corrupt generation table name | Query falls back; removal preserves the real derivative | Integration | ✅ `integration/modules/search/test_vector_index.py::TestVectorIndex::test_refuses_foreign_native_table_claims` |
| NI002 | tolerates native table loss during content deletion | Edge | Derived table dropped before cleanup | Cleanup returns without undoing durable content work | Integration | ✅ `integration/modules/search/test_vector_index.py::TestVectorIndex::test_tolerates_native_table_loss_during_content_deletion` |
| NI003 | degrades a lost portable index during writes | Error | Compressed derivative disappears | Generation marks unavailable; native floats retained | Integration | ✅ `integration/modules/search/test_vector_index.py::TestVectorIndex::test_degrades_a_lost_portable_index_during_writes` |
| NI004 | refuses invalid native query/rebuild budgets | Error | Zero or above-cap batch | Stable error before scanning or mutation | Integration | ✅ `integration/modules/search/test_vector_index.py::TestVectorIndex::test_refuses_invalid_native_query_budgets`; `integration/modules/search/test_vector_index.py::TestVectorIndex::test_refuses_invalid_native_rebuild_budgets` |
| NI005 | degrades when SQLite extension loading fails | Error | Extension unavailable during native preparation | Stable fallback state; durable floats preserved | Integration | ✅ `integration/modules/search/test_vector_index.py::TestVectorIndex::test_degrades_when_sqlite_extension_loading_fails` |
| DT003 | refuses unknown tables in a transfer destination | Error | Empty unowned target table | Refusal preserves table and source | Integration | ✅ `integration/modules/administration/test_database_transfer.py::TestDatabaseTransfer::test_refuses_unknown_tables_in_a_transfer_destination` |

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| CR001 | fails queued captions whose source was trashed | Error | Artifact trashed after caption request | Terminal source-changed failure; zero provider requests | Integration | ✅ `integration/modules/search/test_captions.py::TestCaptions::test_fails_queued_captions_whose_source_was_trashed` |
| DT004 | preserves portable date and decimal proof values | Edge | Date and precise decimal values | Stable lossless canonical JSON values | Unit | ✅ `unit/modules/administration/test_database_transfer.py::TestCanonical::test_preserves_portable_scalar_proof_values` |
| NI006 | recovers from rejected portable index DDL | Error | SQLite denies derivative CREATE TABLE | Fallback state; durable vectors preserved | Integration | ✅ `integration/modules/search/test_vector_index.py::TestVectorIndex::test_recovers_from_rejected_portable_index_ddl` |

### Backfill scheduling budget

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
| --- | --- | --- | --- | --- | --- | --- |
| BS001 | drains ready backfill in bounded bursts | Happy | Index work remains ready after ordinary repair | Fifteen additional bounded units complete before the periodic pause | Integration | ✅ `integration/runtime/test_search.py::TestSearchRuntime::test_drains_ready_backfill_in_bounded_bursts` |
| BS002 | stops a burst when the index is idle | Edge | No claimed index work | Burst ends after one idle probe | Integration | ✅ `integration/runtime/test_search.py::TestSearchRuntime::test_stops_a_burst_when_the_index_is_idle` |
| BS003 | defers burst work during restore | Error | Restore maintenance held | No index unit admitted | Integration | ✅ `integration/runtime/test_search.py::TestSearchRuntime::test_defers_burst_work_during_restore` |
| BS004 | waits for an in-flight burst unit on cancellation | Edge | Shutdown during a held index unit | Task waits for worker completion before exit | Integration | ✅ `integration/runtime/test_search.py::TestSearchRuntime::test_waits_for_an_inflight_burst_unit_on_cancellation` |
| BS005 | reports idle for a settled active generation | Edge | Active ready generation has no missing vectors | No inference; false work result ends the scheduler burst | Integration | ✅ `integration/modules/search/test_indexing.py::TestIndexProcessor::test_reports_idle_for_a_settled_active_generation` |
| BS006 | keeps burst inference disabled without AI consent | Edge | Master AI setting off | No index work or model inference admitted | Integration | ✅ `integration/runtime/test_search.py::TestSearchRuntime::test_keeps_burst_inference_disabled_without_ai_consent` |

### Printed-photo acceptance

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
| --- | --- | --- | --- | --- | --- | --- |
| PH001 | retrieves the source Model from a real printed-part photograph | Happy | Attributed original Benchy photo, matching STL, 32 frozen distractors, pinned CLIP | Source Model in top 10 with multiview; opaque library metadata; original full-scene image | Integration/Measurement | ✅ `integration/modules/search/retrieval/test_visual_quality.py::TestPrintedPhotoRanking::test_replays_printed_photo_rank`; 9 visual/point/photo replay tests passed |
| PH002 | preserves the measured thumbnail photo limitation | Edge | Same full-scene photo and distractors, thumbnail profile | Source Model ranks 13th; failure remains explicit | Integration/Measurement | ✅ `integration/modules/search/retrieval/test_visual_quality.py::TestPrintedPhotoRanking::test_replays_printed_photo_rank`; 9 visual/point/photo replay tests passed |

### Bounded ranking at scale

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| VK001 | preserves exact subject ranking across competitive replacements | Edge | Repeated typed Subjects, late best units, tied vectors, different block sizes and limits | Same unique Subjects and units as an independent full-sort oracle | Unit | ✅ core `tests/inference/test_vectors.py::TestCompetitiveRanking::test_preserves_exact_subject_ranking`; full core coverage: 1,903 passed |
| VK002 | rejects corruption beyond an established ranking cutoff | Error | Full top-k already contains perfect matches; later malformed vectors | Validation error rather than silently skipping invalid stored data | Unit | ✅ core `tests/inference/test_vectors.py::TestCompetitiveRanking::test_rejects_corruption_beyond_a_full_cutoff` |
| VK003 | bounds Model-card work to requested identities | Edge | Same one-Model result before and after adding 1,000 unrelated Models | Identical authorized result; SQLite work remains within twice the small-library baseline | Integration | ✅ `integration/modules/library/model_views/test_listing.py::TestReadItemsByIds::test_bounds_card_work_to_requested_identities` |
| VK004 | bounds passage visibility work to candidate identities | Edge | One requested passage among 1,000 unrelated Models; live/hidden contributors | Same fresh authorization with bounded SQLite work | Integration | ✅ `integration/modules/search/test_access.py::TestVisiblePassageIds::test_bounds_visibility_work_to_candidate_identities` |
| VK005 | bounds vector reauthorization to candidate identities | Edge | One result vector; 1,000 unrelated current passages | Same authorized vector; SQL work remains within twice the small-library baseline | Integration | ✅ `integration/modules/search/test_query_context.py::TestAllowedVectors::test_bounds_reauthorization_to_candidate_identities` |
| VK006 | bounds rare lexical lookup work to matching passages | Edge | One matching passage before/after 1,000 unrelated current passages | Same authorized hit; SQL work stays within twice the small-library baseline | Integration | ✅ `integration/modules/search/test_lexical_query.py::TestBoundedCandidates::test_bounds_rare_lookup_work_to_matching_passages` |
| VK007 | preserves bounded passage-scope membership | Edge | Limited, joined, distinct or empty authorized ID query | Correlated membership returns exactly the original authorized set | Integration | ✅ `integration/modules/search/test_access.py::TestPassageInScope::test_preserves_bounded_scope_membership` |
| BS007 | keeps periodic repair transactions small during browsing | Edge | Large already-indexed library; runtime repair tick | Checkpoints advance by at most one Subject per stream so queries can acquire a writer lease promptly | Integration | ✅ `integration/runtime/test_search.py::TestPeriodicRepairBudget::test_keeps_periodic_repair_transactions_small` |
| VK008 | rechecks candidate contributor visibility on PostgreSQL | Edge | Live candidate with private, granted, then trashed contributor | Authorized vector IDs follow each current permission/liveness change | Integration | ✅ `integration/postgres/test_search_passages.py::TestCandidateVisibility::test_rechecks_contributor_visibility_on_postgres`; PostgreSQL owner suite: 11 passed |
| VK009 | preserves arbitrary vector-scope membership | Edge | Limited, offset, joined, distinct or empty authorized ID query; portable/native backend | Only original scope members can be ranked; native and portable agree | Integration | ✅ `integration/modules/search/test_vector_index.py::TestVectorIndex::test_preserves_arbitrary_vector_scope_membership`; 274 owner/API/E2E checks and 11 PostgreSQL native checks passed |

### Verification at `cb080c6a` after query-path corrections

Backend coverage uses the complete main and resource lane data plus focused
reruns against every changed source file; old line/branch data for those files
is discarded before merging. Aggregate coverage is **94.10%**; all ten coverage
floor checks pass, including every unpinned module. Shared vector storage is
92.8% and retrieval is 90.99%. No floor was lowered.

The main lane passed 12,291 tests and found one test-naming failure, subsequently
fixed and verified by the complete hygiene rerun (**3,546 passed**). The complete
resource lane passed **269 tests**. The final query-path runs passed **274**
owner/API/E2E tests, **11** PostgreSQL passage tests, **11** PostgreSQL native-vector
tests, **26** visual/shared-store tests and **7** embedding recovery tests.
The earlier 80-test focused run's timing failure was corrected to measure the
configured provider-admission budget; the unchanged 300 ms whole-request target
is measured independently by the scale harness. Backend Ruff and configured
Pyright pass. Core coverage passed **1,903 tests plus five floor checks**, at
99.19%; its configured Pyright also passes. The previously completed frontend,
locale and real-backend browser evidence above remains applicable because the
final query-path corrections change no frontend code.

Security review is recorded separately against the final branch snapshot.
Performance measurements and remaining hardware/quality acceptance are tracked
in [AI Search performance](ai-search-performance.md) and the explicit open rows
in this matrix; passing correctness tests does not close those acceptance gates.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| SC006 | rejects an inactive backfill load measurement | Error | Generation stays labelled building but stalls, changes identity, regresses or leaves backfill | Acceptance requires actual indexed-vector progress through the measured sample and continuous backfill state | Repo | ✅ `repo/test_search_ingest_load.py::TestBackfillOverlap`; seven outcome checks and four affected hygiene checks passed |
| BS008 | bounds publication authorization to the current source | Edge | One leased vector publication before/after 1,000 unrelated live passages | Same durable vector; SQLite writer work remains within twice the small-library baseline | Integration | ✅ `integration/modules/search/test_indexing.py::TestPublicationWork::test_bounds_publication_authorization_to_the_current_source` |
| VK010 | avoids rescanning a full native shortlist | Edge | Authorized native shortlist reaches its declared candidate cap | Truncation remains reported with one authorization pass plus fresh candidate checks | Integration | ✅ `integration/modules/search/test_vector_index.py::TestVectorIndex::test_avoids_rescanning_a_full_native_shortlist` |

Follow-up publication checks passed **59** owner/PostgreSQL tests and **73**
visual/caption/cutover/API/E2E tests. The native shortlist correction passed
**90** shared-store/native/PostgreSQL tests. Their new regressions reproduce
both removed full-scope scans; affected hygiene, Ruff and Pyright checks pass.
The foreground-priority coverage audit and timed load measurements are recorded
below.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| PS001 | tracks nested foreground writes | Edge | Two admitted request mutations | Priority clears only after both finish | Unit | ✅ `unit/runtime/test_maintenance.py::TestForegroundMutations::test_tracks_nested_foreground_writes` |
| PS002 | excludes background work from foreground priority | Happy | Ordinary background mutation admission | Foreground count stays clear | Unit | ✅ `unit/runtime/test_maintenance.py::TestForegroundMutations::test_excludes_background_work` |
| PS003 | releases priority after failed admission | Error | First-write observer raises | Both maintenance counts return to idle | Unit | ✅ `unit/runtime/test_maintenance.py::TestForegroundMutations::test_releases_priority_after_failed_admission` |
| PS004 | refuses foreground writes during restore | Error | Restore maintenance active | Request is not counted or admitted | Unit | ✅ `unit/runtime/test_maintenance.py::TestForegroundMutations::test_refuses_foreground_writes_during_restore` |
| PS005 | covers the complete mutating request with priority | Happy | ASGI request sends response then performs cleanup | Priority remains through cleanup and releases on completion | Integration | ✅ `integration/api/test_vault_generation.py::TestVaultGenerationMiddleware::test_prioritizes_the_complete_mutating_request` |
| PS006 | clears request priority after failure | Error | ASGI application raises or is cancelled | No foreground admission leaks | Integration | ✅ `integration/api/test_vault_generation.py::TestVaultGenerationMiddleware::test_clears_priority_after_request_failure` |
| PS007 | leaves read requests outside mutation priority | Happy | GET/HEAD/OPTIONS request | No foreground write priority | Integration | ✅ `integration/api/test_vault_generation.py::TestVaultGenerationMiddleware::test_leaves_read_requests_outside_mutation_priority` |
| PS008 | defers search repair during foreground writes | Edge | Admitted foreground mutation | No repair checkpoint or indexing work starts | Integration | ✅ `integration/runtime/test_search.py::TestSearchRuntime::test_defers_search_work_during_foreground_writes` |
| PS009 | cancels a foreground wait promptly | Error | Foreground request remains active; inference cancelled or restore begins | Worker wait terminates without writing a vector | Integration | ✅ `integration/modules/search/test_indexing.py::TestForegroundPriority::test_cancels_a_foreground_wait_promptly` |
| PS010 | resumes publication when foreground writes finish | Edge | Foreground request completes during a wait | Existing owned work can continue | Integration | ✅ `integration/modules/search/test_indexing.py::TestForegroundPriority::test_resumes_after_foreground_writes_finish` |
| PS011 | rejects mismatched foreground release | Error | Background admission is released as foreground | Counters remain balanced after rejection | Unit | ✅ `unit/runtime/test_maintenance.py::TestForegroundMutations::test_rejects_mismatched_foreground_release` |
| PS012 | defers direct indexing during foreground writes | Edge | Durable generation is pending; foreground mutation active | No provider call or vector publication | Integration | ✅ `integration/modules/search/test_indexing.py::TestForegroundPriority::test_defers_direct_indexing_during_foreground_writes` |

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| SC007 | populates the complete native scale fixture after early readiness | Edge | Native generation becomes ready before bulk replica insertion | Native IDs equal all ten durable fixture IDs before measurements | Repo | ✅ `repo/test_search_scale.py::TestReplicateModels::test_rebuilds_the_complete_native_benchmark_fixture`; red reproduced one native row vs ten durable rows; all ten fixture/overlap checks pass |

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| PS013 | retries a transient provider outage without quarantining the input | Error | First embedding request loses connectivity; retry deadline expires | Durable retry record clears after recovery; one vector is published without quarantine | Integration | ✅ `integration/modules/search/test_indexing.py::TestIndexProcessor::test_retries_a_transient_provider_outage` |

### Verification after foreground-priority correction

The production snapshot is `a92720eb59c2a5626c798b9f3f598a155c07276f`,
based on `c11db1021afb85d42d7cbc81c2fed490ecd2e216`; remote `main` was
rechecked on 2026-09-12 and still pointed to that base. AI remains disabled by
default and independent of ordinary library, upload and printing workflows.

The first focused foreground run passed **88 tests**. The expanded follow-up
passed **131 of 132**; the failure was a native-restore E2E fixture opening
SQLite connections without the production extension/configuration hook. It
reproduced alone, was corrected in the fixture, and the complete generation
E2E/WebSocket rerun passed **10 tests**. A separate transient-provider outage
recovery check also passes. The test checks a durable retry followed by one
successful publication with no quarantine. The two recovery-login checks pass.
The benchmark-fixture/overlap suite passed **11 checks** and affected hygiene
passed **28 checks**. After the final retry/session-boundary tests, the affected
hygiene rerun passed **15 checks**; Ruff and formatting were rechecked and pass.
Configured Pyright passes for the unchanged final production snapshot.

Coverage is **94.10%**, with **all ten floor checks passing**. Current indexing
is **91.12%**, generations **91.29%**, runtime search **92.86%**, maintenance
**95.45%** and request admission **94.37%**. No floor was lowered. Search files
use fresh focused coverage at their current line positions. The two newly
changed admission files reuse earlier full-suite evidence only for
**byte-identical functions**; changed function bodies are excluded from reuse.
A source-hash/line-map audit records the unchanged functions and translates
only their executed arcs. New/changed admission behavior is covered by the
foreground, restore, recovery-login and WebSocket reruns. This is a combined
audit, not a claim that the entire main/resource suite was rerun after the
last four-file production correction.

The final security diff scan is
`666ab570-0e87-4038-b941-81935ace2df4`, sealed against `a92720eb`: **219 source
files accounted for, zero reportable findings, no deferred security items**.
It reuses the sealed `49421702` review for 215 identical files and reviews all
four production deltas. Review was performed by the parent only, as required
by this repository. Passing this review does not close performance, physical
ARM or independent human-quality acceptance.

The resumed-backfill comparison passes the unchanged 25% ingestion limit
(all 40 uploads completed; p95 +2.5%). Raw successful and failed runs, the
native-fixture correction and scale limits are retained in
[the performance report](ai-search-performance.md).

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| SC008 | verifies benchmark cardinality from a fresh session | Edge | Seeding session expired its ORM objects | Both durable and native counts are checked from a stable generation ID | Repo | ✅ `repo/test_search_scale.py::TestReplicateModels::test_checks_native_cardinality_after_seeding_session_expires` |

Final assessment: **828 behavior rows — 822 covered, one justified N/A, five
open acceptance checks** (`A051`, `A134`, `A135`, `A141`, `PT015`). The valid
populated native run has p95 **2.136 s** and fails the unchanged 300 ms target.
Implementation, correctness coverage, and security review do not convert those
explicit performance/hardware/quality gaps into passes.
