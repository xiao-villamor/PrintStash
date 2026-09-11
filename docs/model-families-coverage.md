# Model Families coverage

Implementation of the revised public #155 plan. Each row names an observable
requirement; pending rows remain explicit until a real test asserts the result.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| F001 | creates_family_without_changing_members | Happy | Dos Models con Revisions, tags e historial | Family creada; snapshots de ambos Models iguales | Integration | ✅ `backend/tests/integration/api/v1/families/test_create.py::TestCreateFamily::test_creates_family_without_changing_members` |
| F002 | rejects_duplicate_member_ids | Error | Mismo Model repetido en create | 422 y ninguna Family creada | Integration | ✅ `backend/tests/integration/api/v1/families/test_create.py::TestCreateFamily::test_rejects_duplicate_member_ids` |
| F003 | rejects_empty_creation | Edge | Create sin miembros | 422 sin filas nuevas | Integration | ✅ `backend/tests/integration/api/v1/families/test_create.py::TestCreateFamily::test_rejects_empty_creation` |
| F004 | requires_explicit_canonical | Error | Create sin canónica válida | 422 sin relaciones | Integration | ✅ `backend/tests/integration/api/v1/families/test_create.py::TestCreateFamily::test_requires_explicit_canonical` |
| F005 | accepts_one_member_family | Edge | Un miembro con canónica explícita | Family válida y vista con indicación | Integration | ✅ `backend/tests/integration/api/v1/families/test_create.py::TestCreateFamily::test_accepts_one_member_family` |
| F006 | rejects_second_live_family | Error | Model reservado por otra Family | 409 family_membership_conflict | Integration | ✅ `backend/tests/integration/api/v1/families/test_create.py::TestCreateFamily::test_rejects_second_live_family` |
| F007 | serializes_concurrent_membership | Edge | Dos transacciones agregan mismo Model | Una pertenencia activa en SQLite/PostgreSQL | Integration | ✅ `backend/tests/integration/postgres/test_families.py::TestConcurrentFamilyEdits::test_serializes_concurrent_membership` |
| F008 | replays_identical_member_add | Edge | Mismo POST dos veces | Una relación; mismo identificador | Integration | ✅ `backend/tests/integration/api/v1/families/test_members.py::TestAddMember::test_replays_identical_member_add` |
| F009 | rejects_invalid_relative_scale | Error | Escala cero, negativa o no finita | 422 sin modificar relación | Integration | ✅ `backend/tests/integration/api/v1/families/test_create.py::TestCreateFamily::test_rejects_invalid_relative_scale` |
| F010 | changes_canonical_atomically | Happy | Nueva canónica y democión elegida | Un puntero y un rol canonical coherentes | Integration | ✅ `backend/tests/integration/api/v1/families/test_members.py::TestChangeCanonical::test_changes_canonical_atomically` |
| F011 | rejects_foreign_canonical | Error | Miembro de otra Family | 422 sin cambio de puntero | Integration | ✅ `backend/tests/integration/api/v1/families/test_members.py::TestChangeCanonical::test_rejects_foreign_canonical` |
| F012 | serializes_canonical_edits | Edge | Dos cambios concurrentes | Una canónica; conflicto de versión explícito | Integration | ✅ `backend/tests/integration/postgres/test_families.py::TestConcurrentFamilyEdits::test_serializes_canonical_edits` |
| F013 | rebases_relative_scale | Happy | Escalas conocidas; nueva referencia | Factores relativos recalculados correctamente | Unit | ✅ `backend/tests/unit/modules/library/families/test_relative.py::TestRebaseScale::test_rebases_relative_scale` |
| F014 | clears_unreliable_relative_scale | Edge | Nueva canónica sin escala comparable | Factores no conocidos quedan null | Integration | ❌ missing |
| F015 | detaches_canonical_to_vacancy | Edge | Retirar canónica | Family vacante; Models intactos | Integration | ✅ `backend/tests/integration/api/v1/families/test_members.py::TestDetachMember::test_detaches_canonical_to_vacancy` |
| F016 | moves_member_atomically | Happy | Origen y destino editables | Una pertenencia final en destino | Integration | ✅ `backend/tests/integration/api/v1/families/test_move.py::TestMoveMember::test_moves_member_atomically` |
| F017 | rolls_back_failed_member_move | Error | Fallo de destino tras preparar cambio | Origen conserva pertenencia | Integration | ✅ `backend/tests/integration/api/v1/families/test_move.py::TestMoveMember::test_rolls_back_failed_member_move` |
| F018 | preserves_destination_canonical_on_move | Edge | Mover canónica de otra Family | Destino conserva su selección humana | Integration | ✅ `backend/tests/integration/api/v1/families/test_move.py::TestMoveMember::test_preserves_destination_canonical_on_move` |
| F019 | trashes_family_without_trashing_models | Happy | Family con miembros vivos | Solo agrupación y pertenencias desactivadas | Integration | ✅ `backend/tests/integration/api/v1/families/test_lifecycle.py::TestTrashFamily::test_trashes_family_without_trashing_models` |
| F020 | restores_family_atomically | Happy | Family trashed sin conflicto | Todas las relaciones elegibles reactivadas | Integration | ✅ `backend/tests/integration/api/v1/families/test_lifecycle.py::TestRestoreFamily::test_restores_family_atomically` |
| F021 | refuses_conflicting_family_restore | Error | Un antiguo miembro en otra Family | 409; ningún miembro restaurado parcialmente | Integration | ✅ `backend/tests/integration/api/v1/families/test_lifecycle.py::TestRestoreFamily::test_refuses_conflicting_family_restore` |
| F022 | reserves_membership_during_model_trash | Edge | Trash de un miembro | Pertenencia conservada; Model no visible | Integration | ✅ `backend/tests/integration/api/v1/families/test_lifecycle.py::TestModelFamilyLifecycle::test_reserves_membership_during_model_trash` |
| F023 | restores_member_to_family | Happy | Restore del Model | Misma pertenencia visible | Integration | ❌ missing |
| F024 | restores_reserved_canonical | Edge | Canónica trashed sin reemplazo | Restore recupera selección anterior | Integration | ✅ `backend/tests/integration/api/v1/families/test_lifecycle.py::TestModelFamilyLifecycle::test_restores_reserved_canonical` |
| F025 | preserves_replacement_canonical | Edge | Se eligió reemplazo durante trash | Restore no cambia canónica vigente | Integration | ✅ `backend/tests/integration/api/v1/families/test_lifecycle.py::TestModelFamilyLifecycle::test_preserves_replacement_canonical` |
| F026 | purges_member_references_explicitly | Edge | Purge Model miembro | Sin referencias colgantes; Family no borrada | Integration | ✅ `backend/tests/integration/api/v1/families/test_lifecycle.py::TestModelFamilyLifecycle::test_purges_member_references_explicitly` |
| F027 | restores_after_member_purge | Edge | Family trashed; miembro purgado | Resto restaurado con reporte de referencia perdida | Integration | ✅ `backend/tests/integration/api/v1/families/test_lifecycle.py::TestRestoreFamily::test_restores_after_member_purge` |
| F028 | replays_family_trash | Edge | DELETE repetido | Sin doble efecto ni cambios en Models | Integration | ✅ `backend/tests/integration/api/v1/families/test_lifecycle.py::TestTrashFamily::test_replays_family_trash` |
| F029 | denies_mutation_with_mixed_edit_roles | Error | Un miembro solo VIEW | 403; transacción sin cambios | Integration | ✅ `backend/tests/integration/api/v1/families/test_permissions.py::TestFamilyPermissions::test_denies_mutation_with_mixed_edit_roles` |
| F030 | rechecks_permissions_before_move | Error | Permiso revocado tras abrir diálogo | Movimiento rechazado sin efecto | Integration | ✅ `backend/tests/integration/api/v1/families/test_move.py::TestMoveMember::test_rechecks_permissions_before_move` |
| F031 | hides_invisible_siblings | Error | Family compartida parcialmente | Sin nombres ni IDs de miembros ocultos | Integration | ✅ `backend/tests/integration/api/v1/families/test_permissions.py::TestFamilyPermissions::test_hides_invisible_siblings` |
| F032 | counts_only_visible_members | Edge | Hermanos visibles y ocultos | Conteo autorizado en ModelRead y tarjetas | Integration | ❌ missing |
| F033 | hides_invisible_canonical | Error | Canónica fuera del alcance | canonical_model_id null; sin imagen oculta | Integration | ✅ `backend/tests/integration/api/v1/families/test_permissions.py::TestFamilyPermissions::test_hides_invisible_canonical` |
| F034 | denies_unauthenticated_family_write | Error | Sin sesión o token de solo lectura | 401/403 sin escrituras | Integration | ✅ `backend/tests/integration/api/v1/families/test_permissions.py::TestFamilyPermissions::test_denies_unauthenticated_family_write` |
| F035 | groups_read_only_source_models | Happy | Models de Library Source read-only | Relaciones guardadas; origen sin escrituras | Integration | ❌ missing |
| F036 | audit_logs_family_mutation | Happy | Parametrizar create/edit/add/detach/role/canonical/move/trash/restore | Audit row con actor y cambios relacionales | Integration | ❌ missing |
| F037 | rolls_back_mutation_when_audit_fails | Error | Falla commit de auditoría | Estado anterior conservado | Integration | ❌ missing |
| F038 | projects_family_through_model_views | Happy | Detalle y lista de Models agrupados | Mismo contrato family sin N+1 | Integration | ❌ missing |
| F039 | paginates_collapsed_families | Edge | Más de 500 agrupaciones y varios tamaños de página | Cada tarjeta una vez; sin páginas perdidas | Integration | ❌ missing |
| F040 | searches_family_name_in_collapsed_mode | Happy | Nombre solo en Family | Tarjeta Family recuperada | Integration | ❌ missing |
| F041 | searches_visible_member_in_collapsed_mode | Happy | Coincidencia en un hermano visible | Family incluida con conteo de coincidencias | Integration | ❌ missing |
| F042 | excludes_hidden_member_search_evidence | Error | Texto solo en miembro oculto | Family no aparece por ese texto | Integration | ❌ missing |
| F043 | filters_models_by_family_membership | Happy | family_id/family_role/in_family parametrizados | Conjunto correcto de Models vivos | Integration | ❌ missing |
| F044 | rejects_cursor_from_other_browse_mode | Error | Cursor de Everything en collapsed | Error estable de cursor | Integration | ❌ missing |
| F045 | persists_family_browse_in_saved_view | Happy | Guardar y reabrir vista filtrada | Modo y filtros restaurados | Playwright | ❌ missing |
| F046 | renders_member_metadata | Happy | Miembros con métricas, Revisions y resultados | Todos los campos y nulls presentados correctamente | Frontend unit | ❌ missing |
| F047 | filters_family_member_grid | Happy | Filtros role/format/known-good/revisions/source | Solo miembros coincidentes | Integration | ❌ missing |
| F048 | sorts_family_members_deterministically | Edge | Escalas null y empates | Orden estable para escala/fecha/éxito | Integration | ❌ missing |
| F049 | creates_family_from_multiselect | Happy | Dos uploads con Revisions | Family creada desde grid | Playwright | ❌ missing |
| F050 | creates_family_from_model_detail | Happy | Picker paginado en detalle | Miembros seleccionados preservados al enviar | Playwright | ❌ missing |
| F051 | offers_explicit_move_for_conflict | Error | Model ya agrupado | Diálogo exige movimiento explícito | Playwright | ❌ missing |
| F052 | compares_members_at_shared_scale | Happy | Dos tamaños del mismo diseño | Diferencia física visible y metadatos conservados | Playwright | ❌ missing |
| F053 | handles_unsupported_compare_preview | Edge | Un formato sin preview | Metadatos accesibles; otra preview funciona | Frontend unit | ❌ missing |
| F054 | adds_family_siblings_as_choices | Happy | Hermano ya presente entre Choices | Solo Choices nuevas, sin duplicados | Integration | ❌ missing |
| F055 | rejects_invisible_sibling_choice | Error | Permiso revocado tras selección | Ninguna Choice no autorizada | Integration | ❌ missing |
| F056 | applies_bulk_tags_explicitly | Happy | Confirmar tag para todos | Solo tags solicitados por servicio existente | Integration | ❌ missing |
| F057 | moves_all_members_with_destination_permission | Happy | Todos editables y destino permitido | Collections modificadas en operación explícita | Integration | ❌ missing |
| F058 | rejects_partial_bulk_move | Error | Un miembro no editable | Ningún Model movido | Integration | ❌ missing |
| F059 | stars_visible_members_for_current_user | Happy | Star all anunciado | Stars personales para conjunto visible | Integration | ❌ missing |
| F060 | disables_send_without_canonical | Edge | Vacante canónica | No inicia envío; motivo visible | Frontend unit | ❌ missing |
| F061 | sends_canonical_through_print_flow | Happy | Canónica utilizable e impresora autorizada | Flujo existente recibe solo Model elegido | Playwright | ❌ missing |
| F062 | omits_family_bulk_trash | Edge | Vista Family con selección | No existe acción destructiva de conjunto | Frontend unit | ❌ missing |
| F063 | preserves_known_good_per_member | Edge | Un miembro known-good | Otros miembros no cambian su estado | Integration | ❌ missing |
| F064 | roundtrips_family_portable_v2 | Happy | Family con cover, roles y notas; local/S3 | Relaciones resueltas por hash con datos equivalentes | Integration | ❌ missing |
| F065 | imports_legacy_archive_without_families | Edge | Archivo v1 existente | Import correcto sin Family | Integration | ❌ missing |
| F066 | reimports_family_idempotently | Edge | Importar mismo export dos veces | Una Family y una relación por miembro | Integration | ❌ missing |
| F067 | refuses_import_membership_conflict | Error | Model ya en otra Family | Reporte de conflicto; Family no aplicada parcialmente | Integration | ❌ missing |
| F068 | preserves_existing_canonical_on_import | Edge | Reimport tras selección manual | Canónica local conservada | Integration | ❌ missing |
| F069 | rejects_unsafe_family_cover | Error | Cover excesivo o referencia fuera del archive | Error y cleanup sin archivos ajenos modificados | Integration | ❌ missing |
| F070 | restores_family_database_backup | Happy | Backup con vacante y Family trashed | Relaciones y ciclo de vida preservados | E2E | ❌ missing |
| F071 | roundtrips_family_schema | Happy | DB poblada; SQLite/PostgreSQL | Upgrade/downgrade/upgrade válido | Integration | ✅ `backend/tests/integration/postgres/test_families.py::TestFamilySchema::test_roundtrips_family_schema` |
| F072 | groups_variants_without_losing_revisions | Happy | Dos variantes reales: create, canonical, compare | Models y Revisions siguen accesibles e independientes | Playwright | ❌ missing |
| F073 | localizes_family_states | Edge | Idiomas en/es; vacío/conflicto/vacante | Sin claves crudas y controles accesibles | Frontend unit | ❌ missing |
| F074 | delivers_families_without_related_features | Happy | Main con esta PR; similarity e inference/search ausentes | Crear, comparar, browse, restore y portable funcionan sin otra issue | E2E | ❌ missing |
| F075 | rejects_detached_membership_without_reason | Error | Archived relationship loses its detach reason | Database rejects the incomplete history; prior reason remains | Integration | ✅ `backend/tests/integration/db/models/test_library.py::TestModelFamilyMember::test_rejects_detached_membership_without_reason` |
