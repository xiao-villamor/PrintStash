"""Search never broadens an authenticated user's library permissions."""

import pytest
from sqlalchemy import delete

from app.db.models import CollectionPermission, CollectionRole
from app.db.projections import bind_content_projection, content_changed
from app.modules.search.lexical_index import rebuild_partition
from app.modules.search.projection import LibraryProjection
from tests.factories import bearer
from tests.search_projection import drain_search


@pytest.fixture(autouse=True)
def projection():
    previous = bind_content_projection(LibraryProjection())
    yield
    bind_content_projection(previous)


class TestSearch:
    @pytest.mark.asyncio
    async def test_rejects_an_actor_revoked_during_image_upload(
        self, db_session, make_user
    ):
        from io import BytesIO

        from fastapi import Request, Response
        from PIL import Image

        from app.api.v1.search import search_image
        from app.core.errors import OperationError
        from app.modules.search import configuration
        from app.schemas.inference import SearchSettings

        actor = make_user(superuser=True)
        configuration.update(db_session, SearchSettings(enabled=True))
        db_session.commit()
        drain_search(db_session)
        body = BytesIO()
        Image.new("RGB", (1, 1), "gray").save(body, "PNG")

        async def receive():
            actor.is_active = False
            db_session.add(actor)
            db_session.commit()
            return {"type": "http.request", "body": body.getvalue(), "more_body": False}

        request = Request(
            {"type": "http", "headers": [(b"content-type", b"image/png")]}, receive
        )

        with pytest.raises(OperationError, match="search_user_required"):
            await search_image(request, Response(), limit=30, cursor=None, user=actor)

    @pytest.mark.parametrize(
        "kind,expected", [("bytes", 413), ("pixels", 413), ("zip", 415)]
    )
    def test_rejects_unsupported_image_uploads_before_retrieval(
        self, client, db_session, make_user, monkeypatch, kind, expected
    ):
        import struct
        import zlib

        from app.api.v1 import search as route
        from app.modules.search import configuration
        from app.schemas.inference import SearchSettings

        actor = make_user(superuser=True)
        configuration.update(db_session, SearchSettings(enabled=True))
        db_session.commit()
        drain_search(db_session)
        headers = bearer(actor) | {"Content-Type": "image/png"}
        body = b"invalid"
        if kind == "bytes":
            headers["Content-Length"] = str(50 * 1024**2)
        elif kind == "zip":
            headers["Content-Type"] = "application/zip"
            body = b"PK\x03\x04private-archive"
        else:

            def chunk(name, data):
                return (
                    struct.pack(">I", len(data))
                    + name
                    + data
                    + struct.pack(">I", zlib.crc32(name + data))
                )

            body = (
                b"\x89PNG\r\n\x1a\n"
                + chunk(b"IHDR", struct.pack(">IIBBBBB", 65535, 65535, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", b"")
            )

        def no_retrieval(*args, **kwargs):
            raise AssertionError("rejected image reached retrieval or inference")

        monkeypatch.setattr(route, "search", no_retrieval)
        response = client.post("/api/v1/search/image", content=body, headers=headers)
        assert response.status_code == expected, response.text
        assert response.json()["detail"] == (
            "embedding_image_type_unsupported"
            if kind == "zip"
            else "embedding_image_too_large"
        )

    @pytest.mark.parametrize("mode", ["anonymous", "disabled", "busy"])
    def test_rejects_image_requests_before_reading_the_body(
        self, client, db_session, make_user, monkeypatch, mode
    ):
        import threading

        from app.api.v1 import search as route
        from app.modules.search import configuration
        from app.schemas.inference import SearchSettings

        actor = make_user(superuser=True)
        configuration.update(db_session, SearchSettings(enabled=mode != "disabled"))
        db_session.commit()
        drain_search(db_session)
        slots = threading.BoundedSemaphore(2)
        if mode == "busy":
            slots.acquire()
            slots.acquire()
        monkeypatch.setattr(route, "_image_slots", slots)

        async def no_body(*args):
            raise AssertionError("unauthorized or unadmitted body was parsed")

        monkeypatch.setattr(route, "read_image", no_body)
        response = client.post(
            "/api/v1/search/image",
            content=b"untrusted image",
            headers={} if mode == "anonymous" else bearer(actor),
        )
        assert (
            response.status_code
            == {"anonymous": 401, "disabled": 409, "busy": 429}[mode]
        ), response.text

    def test_uses_the_configured_ranked_like_backend(
        self, client, db_session, auth_headers, make_model
    ):
        model = make_model("Bracket")
        content_changed(db_session, "model", [model.id])
        rebuild_partition(db_session)
        db_session.commit()
        drain_search(db_session)
        assert (
            client.patch(
                "/api/v1/search/settings",
                headers=auth_headers,
                json={"lexical_backend": "ranked_like"},
            ).status_code
            == 200
        )
        response = client.get(
            "/api/v1/search", headers=auth_headers, params={"q": "bracket"}
        )
        assert response.status_code == 200, response.text
        assert response.json()["lexical_backend"] == "ranked_like"
        assert response.json()["items"][0]["subject_id"] == model.id

    def test_links_collection_matches_to_the_existing_browse_route(
        self, client, db_session, auth_headers, make_collection
    ):
        collection = make_collection("Bracket tools")
        content_changed(db_session, "collection", [collection.id])
        db_session.commit()
        drain_search(db_session)
        response = client.get(
            "/api/v1/search", headers=auth_headers, params={"q": "bracket"}
        )
        assert response.status_code == 200, response.text
        assert response.json()["items"][0]["href"] == "/?c=bracket-tools"

    def test_reports_lexical_status_with_ai_disabled(self, client, auth_headers):
        response = client.get("/api/v1/search/status", headers=auth_headers)

        assert response.status_code == 200, response.text
        assert response.json() == {
            "enabled": False,
            "semantic_ready": False,
            "legs": ["lexical"],
            "generations": [],
            "degraded": [],
            "backlog": False,
            "remote_hosts": [],
        }

    def test_requires_authentication_for_status(self, client):
        assert client.get("/api/v1/search/status").status_code == 401

    def test_rejects_unauthenticated_search(self, client):
        response = client.get("/api/v1/search", params={"q": "private"})
        assert response.status_code == 401

    def test_rejects_share_context_search(self, client, db_session, make_model):
        from app.modules.identity.share import create_share

        model = make_model()
        _link, token = create_share(
            db_session,
            model_id=model.id,
            expires_in_days=1,
            allow_download=False,
            created_by=None,
        )
        response = client.get(
            "/api/v1/search",
            headers={"Authorization": f"Bearer {token}"},
            params={"q": "bracket"},
        )
        assert response.status_code == 403

    def test_hides_unauthorized_member_segments(
        self,
        client,
        db_session,
        make_user,
        make_model,
        make_collection,
        make_multipart_model,
        grant_role,
    ):
        admin = make_user(superuser=True)
        viewer = make_user()
        public = make_collection("Shared")
        private = make_collection("Private")
        member = make_model("Secretprototype", collection=private)
        aggregate = make_multipart_model("Assembly", collection=public)
        grant_role(viewer, public, CollectionRole.VIEW)
        response = client.put(
            f"/api/v1/multipart-models/{aggregate.id}",
            headers=bearer(admin),
            json={"parts": [{"name": "Leg", "choices": [{"model_id": member.id}]}]},
        )
        assert response.status_code == 200, response.text
        content_changed(db_session, "model", [member.id])
        rebuild_partition(db_session)
        db_session.commit()
        drain_search(db_session)

        response = client.get(
            "/api/v1/search", headers=bearer(viewer), params={"q": "Secretprototype"}
        )

        assert response.status_code == 200, response.text
        assert response.json()["items"] == []
        response = client.get(
            "/api/v1/search", headers=bearer(viewer), params={"q": "Assembly"}
        )
        assert [row["subject_id"] for row in response.json()["items"]] == [aggregate.id]

    def test_applies_permission_revocation_immediately(
        self, client, db_session, make_user, make_model, make_collection, grant_role
    ):
        viewer = make_user()
        collection = make_collection("Shared")
        model = make_model("Bracket", collection=collection)
        grant_role(viewer, collection, CollectionRole.VIEW)
        content_changed(db_session, "model", [model.id])
        rebuild_partition(db_session)
        db_session.commit()
        drain_search(db_session)
        assert client.get(
            "/api/v1/search", headers=bearer(viewer), params={"q": "bracket"}
        ).json()["items"]
        db_session.exec(
            delete(CollectionPermission).where(
                CollectionPermission.user_id == viewer.id
            )
        )
        db_session.commit()
        drain_search(db_session)

        response = client.get(
            "/api/v1/search", headers=bearer(viewer), params={"q": "bracket"}
        )

        assert response.status_code == 200, response.text
        assert response.json()["items"] == []

    def test_returns_bounded_plain_text_evidence(
        self, client, db_session, make_user, make_document
    ):
        actor = make_user(superuser=True)
        doc = make_document(
            "Guide", body='<script>alert("assembly")</script>' + "x " * 300
        )
        content_changed(db_session, "document", [doc.id])
        rebuild_partition(db_session)
        db_session.commit()
        drain_search(db_session)

        response = client.get(
            "/api/v1/search", headers=bearer(actor), params={"q": "assembly"}
        )

        assert response.status_code == 200, response.text
        evidence = response.json()["items"][0]["evidence"][0]
        assert len(evidence["text"]) <= 240
        start, end = evidence["ranges"][0]
        assert evidence["text"][start:end] == "assembly"
        assert "<mark>" not in evidence["text"]

    def test_rejects_a_cursor_from_another_query(
        self, client, db_session, make_user, make_model
    ):
        actor = make_user(superuser=True)
        models = [make_model(f"Bracket {i}") for i in range(3)]
        content_changed(db_session, "model", [row.id for row in models])
        db_session.commit()
        drain_search(db_session)
        first = client.get(
            "/api/v1/search", headers=bearer(actor), params={"q": "bracket", "limit": 1}
        ).json()
        assert first["next_cursor"]

        response = client.get(
            "/api/v1/search",
            headers=bearer(actor),
            params={"q": "hinge", "cursor": first["next_cursor"]},
        )

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "search_cursor_invalid"

    def test_paginates_without_repeating_a_subject(
        self, client, db_session, make_user, make_model
    ):
        actor = make_user(superuser=True)
        models = [make_model(f"Bracket {i}") for i in range(3)]
        content_changed(db_session, "model", [row.id for row in models])
        db_session.commit()
        drain_search(db_session)
        first = client.get(
            "/api/v1/search", headers=bearer(actor), params={"q": "bracket", "limit": 2}
        ).json()
        second = client.get(
            "/api/v1/search",
            headers=bearer(actor),
            params={"q": "bracket", "limit": 2, "cursor": first["next_cursor"]},
        ).json()

        assert [row["subject_id"] for row in first["items"] + second["items"]] == [
            row.id for row in models
        ]
        assert second["next_cursor"] is None


class TestStructuredSearch:
    def test_filters_results_by_actual_print_history(
        self, client, db_session, auth_headers, make_model, make_file, make_print_job
    ):
        import json
        from datetime import datetime, timezone

        from app.db.models import PrintJobState

        model = make_model("Qualifying bracket")
        make_model("Other bracket")
        file = make_file(model)
        content_changed(db_session, "model", [model.id])
        rebuild_partition(db_session)
        db_session.commit()
        drain_search(db_session)
        make_print_job(
            file,
            state=PrintJobState.COMPLETED,
            finished_at=datetime(2026, 8, 12, tzinfo=timezone.utc),
            actual_duration_s=100,
        )
        response = client.get(
            "/api/v1/search",
            headers=auth_headers,
            params={
                "q": "bracket",
                "filters": json.dumps(
                    {
                        "printed_after": "2026-08-01T00:00:00Z",
                        "printed_before": "2026-09-01T00:00:00Z",
                        "print_duration_max_s": 10800,
                        "print_outcome": ["completed"],
                    }
                ),
            },
        )
        assert response.status_code == 200
        assert [item["subject_id"] for item in response.json()["items"]] == [model.id]

    @pytest.mark.parametrize(
        "payload",
        [
            '{"extra":true}',
            '{"print_duration_min_s":100,"print_duration_max_s":99}',
            '{"printed_after":"2026-09-02","printed_before":"2026-09-01"}',
            "not json",
        ],
    )
    def test_rejects_invalid_filter_payloads(self, client, auth_headers, payload):
        response = client.get(
            "/api/v1/search", headers=auth_headers, params={"filters": payload}
        )
        assert (
            response.status_code == 422
            and response.json()["detail"] == "model_filters_invalid"
        )

    def test_preserves_admin_only_printer_filter_policy(self, client, make_user):
        response = client.get(
            "/api/v1/search",
            headers=bearer(make_user()),
            params={"filters": '{"printer_id":1}'},
        )
        assert response.status_code == 403

    def test_keeps_failed_queries_out_of_request_logs(
        self, client, auth_headers, caplog
    ):
        import logging

        with caplog.at_level(logging.INFO):
            response = client.get(
                "/api/v1/search",
                headers=auth_headers,
                params={"q": "private-search-marker", "filters": "invalid"},
            )
        assert response.status_code == 422
        assert "private-search-marker" not in caplog.text
        assert "status=422" in caplog.text

    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/models",
            "/api/v1/models/page",
            "/api/v1/models/facets",
            "/api/v1/models/outliner",
        ],
    )
    def test_rejects_inconsistent_history_ranges_in_browse(
        self, client, auth_headers, path
    ):
        response = client.get(
            path,
            headers=auth_headers,
            params={"print_duration_min_s": 100, "print_duration_max_s": 100},
        )
        assert response.status_code == 422


class TestSearchPreferences:
    def test_persists_only_the_signed_in_users_preferences(self, client, make_user):
        first, second = make_user(), make_user()
        response = client.patch(
            "/api/v1/search/preferences",
            headers=bearer(first),
            json={"nl_filters_enabled": True, "timezone": "America/New_York"},
        )
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["nl_filters_enabled"]
        other = client.get("/api/v1/search/preferences", headers=bearer(second)).json()
        assert not other["nl_filters_enabled"] and other["timezone"] is None

    def test_rejects_unknown_timezone(self, client, auth_headers):
        response = client.patch(
            "/api/v1/search/preferences",
            headers=auth_headers,
            json={"timezone": "Mars/Olympus"},
        )
        assert response.status_code == 422

    def test_returns_original_query_when_parser_is_unavailable(
        self, client, auth_headers
    ):
        response = client.post(
            "/api/v1/search/parse", headers=auth_headers, json={"query": "original"}
        )
        assert response.status_code == 200 and not response.json()["parsed"]
        assert response.json()["residual_query"] == "original"
        assert response.headers["cache-control"] == "no-store"


class TestSearchErrorPrivacy:
    def test_omits_raw_query_from_unexpected_error_traces(
        self, app, client, auth_headers, caplog, monkeypatch
    ):
        import logging

        from fastapi import Request

        from app.core.config import _overlay

        monkeypatch.setitem(_overlay, "log_level", "DEBUG")

        async def failing(request: Request):
            raise RuntimeError("failed to parse " + request.query_params["q"])

        path = "/api/v1/search/__test__/failure"
        app.add_api_route(path, failing, methods=["GET"], include_in_schema=False)
        route = app.router.routes[-1]
        try:
            with caplog.at_level(logging.DEBUG):
                response = client.get(
                    path, params={"q": "raw-private-query-marker"}, headers=auth_headers
                )
            assert response.status_code == 500
            assert "raw-private-query-marker" not in caplog.text
            assert "RuntimeError" in caplog.text
        finally:
            app.router.routes.remove(route)


class TestSearchParseAuthentication:
    @pytest.mark.parametrize(
        ("method", "path"),
        [("GET", "/preferences"), ("PATCH", "/preferences"), ("POST", "/parse")],
    )
    def test_requires_a_signed_in_user(self, client, method, path):
        response = client.request(
            method,
            "/api/v1/search" + path,
            json={"query": "private"} if path == "/parse" else {},
        )
        assert response.status_code == 401


class TestBrowseHistory:
    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/models",
            "/api/v1/models/page",
            "/api/v1/models/outliner",
            "/api/v1/models/facets",
        ],
    )
    def test_applies_actual_duration_to_each_browse_reader(
        self, client, auth_headers, make_model, make_file, make_print_job, path
    ):
        from app.db.models import PrintJobState

        short, exact = make_model("Short"), make_model("At maximum")
        for model, duration in ((short, 199), (exact, 200)):
            make_print_job(
                make_file(model),
                state=PrintJobState.COMPLETED,
                actual_duration_s=duration,
            )
        response = client.get(
            path, headers=auth_headers, params={"print_duration_max_s": 200}
        )
        assert response.status_code == 200
        result = response.json()
        if path.endswith("/facets"):
            assert result["print_outcome"] == [{"value": "completed", "count": 1}]
        else:
            items = result["items"] if path.endswith("/page") else result
            assert [item["id"] for item in items] == [short.id]
