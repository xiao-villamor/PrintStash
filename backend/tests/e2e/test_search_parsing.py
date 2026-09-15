"""Actual HTTP chat parsing feeds canonical search and reusable Saved Views."""

import json
from datetime import timedelta

import pytest
from printstash_core.search.passages import SearchSubject, SubjectType

from app.core.time import utcnow
from app.db.models import PrintJobState
from app.modules.inference.transport import close_client
from app.modules.search.calendar import bounds
from app.modules.search.passages import sync_subject
from tests.factories import build_file, build_model, build_print_job
from tests.fakes.inference import InferenceFake
from tests.fakes.search_parsing import parsed_output
from tests.fakes.server import start_server

pytestmark = pytest.mark.asyncio


class TestNaturalLanguageSearch:
    async def test_searches_actual_print_history_from_a_parsed_sentence(
        self, api, e2e_db, superuser_headers
    ):
        after, _ = bounds("last_month", utcnow(), "Europe/Madrid")
        short = build_model(e2e_db, "Short bracket")
        long = build_model(e2e_db, "Long bracket")
        for model, duration in ((short, 10799), (long, 10800)):
            file = build_file(e2e_db, model)
            build_print_job(
                e2e_db,
                file,
                state=PrintJobState.COMPLETED,
                finished_at=after + timedelta(days=2),
                actual_duration_s=duration,
            )
            sync_subject(e2e_db, SearchSubject(SubjectType.MODEL, model.id))
        e2e_db.commit()
        fake = InferenceFake(chat_result={"probe": "ok"})
        server = start_server(fake.app())
        try:
            response = await api.post(
                "/api/v1/config/ai-search/endpoints",
                headers=superuser_headers,
                json={
                    "base_url": server.base_url + "/v1",
                    "model": fake.model,
                    "kind": "chat",
                },
            )
            assert response.status_code == 201
            endpoint_id = response.json()["id"]
            response = await api.put(
                "/api/v1/config/ai-search",
                headers=superuser_headers,
                json={
                    "enabled": True,
                    "nl_filters_enabled": True,
                    "chat_endpoint_id": endpoint_id,
                    "timezone": "Europe/Madrid",
                },
            )
            assert response.status_code == 200
            response = await api.patch(
                "/api/v1/search/preferences",
                headers=superuser_headers,
                json={"nl_filters_enabled": True},
            )
            assert response.status_code == 200
            fake.chat_result = parsed_output(
                printed=True,
                print_outcome=["completed"],
                printed_period="last_month",
                print_duration_max_s=10800,
            )
            original = "brackets printed last month under 3 hours"
            response = await api.post(
                "/api/v1/search/parse",
                headers=superuser_headers,
                json={"query": original},
            )
            assert response.status_code == 200
            parsed = response.json()
            assert parsed["parsed"], parsed["reason"]
            assert parsed["filters"]["print_duration_max_s"] == 10800
            assert parsed["timezone"] == "Europe/Madrid"
            assert "printed_period" not in parsed["filters"]
            response = await api.get(
                "/api/v1/search",
                headers=superuser_headers,
                params={
                    "q": parsed["residual_query"],
                    "filters": json.dumps(parsed["filters"]),
                    "sort": parsed["sort"],
                },
            )
            assert response.status_code == 200
            assert [item["subject_id"] for item in response.json()["items"]] == [
                short.id
            ]
            response = await api.post(
                "/api/v1/saved-views",
                headers=superuser_headers,
                json={
                    "name": "Recent successful brackets",
                    "filters": {
                        **parsed["filters"],
                        "q": parsed["residual_query"],
                        "sort": parsed["sort"],
                    },
                },
            )
            assert response.status_code == 201
            saved = response.json()["filters"]
            assert saved["q"] == "bracket" and saved["print_duration_max_s"] == 10800
            assert original not in response.text
            wire = fake.calls[-1]["body"]
            assert wire["stream"] is False
            assert wire["response_format"]["json_schema"]["strict"] is True
            assert "image_url" not in json.dumps(wire)
            before = len(fake.calls)
            response = await api.patch(
                "/api/v1/search/preferences",
                headers=superuser_headers,
                json={"nl_filters_enabled": False},
            )
            assert response.status_code == 200
            response = await api.post(
                "/api/v1/search/parse",
                headers=superuser_headers,
                json={"query": original},
            )
            assert not response.json()["parsed"] and len(fake.calls) == before
        finally:
            close_client()
            server.stop()
