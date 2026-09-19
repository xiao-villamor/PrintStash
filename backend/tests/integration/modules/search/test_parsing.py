"""NL opt-ins, authorized vocabulary, strict output and safe original-query fallback."""

import json

import pytest
from printstash_core.inference import EmbeddingError
from sqlmodel import select

from app.db.models import UserSearchPreferences
from app.modules.search import configuration, parsing, preferences
from app.schemas.inference import SearchSettings
from app.schemas.search_parsing import SearchPreferencesPatch
from tests.fakes.search_parsing import ParseProvider, parsed_output


@pytest.fixture
def parser_setup(
    db_session, make_user, make_inference_endpoint, make_user_search_preferences
):
    user = make_user(superuser=True)
    endpoint = make_inference_endpoint(kind="chat")
    configuration.update(
        db_session,
        SearchSettings(
            enabled=True,
            nl_filters_enabled=True,
            chat_endpoint_id=endpoint.id,
            timezone="Europe/Madrid",
        ),
    )
    make_user_search_preferences(user, nl_filters_enabled=True)
    return user, endpoint


class TestParse:
    def test_discards_parsed_output_after_master_revocation(
        self, db_session, parser_setup
    ):
        user, _ = parser_setup

        def revoke():
            configuration.update(
                db_session,
                configuration.settings(db_session).model_copy(
                    update={"enabled": False}
                ),
            )
            db_session.commit()

        provider = ParseProvider(before_reply=revoke)
        result = parsing.parse(
            db_session, user, "private original", provider_factory=lambda *_: provider
        )
        assert len(provider.requests) == 1
        assert not result.parsed
        assert result.residual_query == "private original"
        assert result.reason == "search_parse_unavailable"

    def test_suppresses_parsing_with_the_master_off(self, db_session, parser_setup):
        user, _ = parser_setup
        configuration.update(
            db_session,
            configuration.settings(db_session).model_copy(update={"enabled": False}),
        )
        db_session.commit()
        provider = ParseProvider()
        result = parsing.parse(
            db_session, user, "private original", provider_factory=lambda *_: provider
        )
        assert not result.parsed
        assert result.residual_query == "private original"
        assert provider.requests == []

    def test_normalizes_real_duration_with_absolute_calendar_bounds(
        self, db_session, parser_setup
    ):
        user, _ = parser_setup
        provider = ParseProvider(
            parsed_output(
                printed=True,
                print_outcome=["completed"],
                printed_period="last_month",
                print_duration_max_s=10800,
            )
        )
        result = parsing.parse(
            db_session,
            user,
            "brackets printed last month under 3 hours",
            provider_factory=lambda *_: provider,
        )
        assert result.parsed and result.filters.print_duration_max_s == 10800
        assert result.filters.print_outcome == ["completed"]
        assert result.filters.printed_after < result.filters.printed_before
        assert result.timezone == "Europe/Madrid"
        assert result.residual_query == "bracket"
        assert provider.requests[0].image_jpegs == ()
        assert not configuration.settings(db_session).captions_enabled

    @pytest.mark.parametrize("switch", ["user", "instance", "both"])
    def test_requires_both_optins_before_egress(self, db_session, parser_setup, switch):
        user, endpoint = parser_setup
        if switch in ("user", "both"):
            preferences.update(
                db_session, user, SearchPreferencesPatch(nl_filters_enabled=False)
            )
        if switch in ("instance", "both"):
            configuration.update(
                db_session, SearchSettings(enabled=True, chat_endpoint_id=endpoint.id)
            )
        assert configuration.settings(db_session).enabled
        provider = ParseProvider()
        result = parsing.parse(
            db_session, user, "original query", provider_factory=lambda *_: provider
        )
        assert not result.parsed and result.residual_query == "original query"
        assert provider.requests == []

    def test_keeps_parsing_off_with_a_configured_chat_endpoint(
        self, db_session, parser_setup, make_user
    ):
        _, endpoint = parser_setup
        user = make_user()
        configuration.update(
            db_session, SearchSettings(enabled=True, chat_endpoint_id=endpoint.id)
        )
        provider = ParseProvider()

        result = parsing.parse(
            db_session, user, "original query", provider_factory=lambda *_: provider
        )

        assert not result.parsed
        assert result.residual_query == "original query"
        assert provider.requests == []

    @pytest.mark.parametrize(
        "invalid",
        [
            {"collection_id": 99999},
            {"printer_id": 99999},
            {"material_type": ["unknown"]},
            {"tag": ["unknown"]},
            {"sql": "select * from users"},
            {"print_duration_min_s": 100, "print_duration_max_s": 100},
            {
                "printed_after": "2026-09-02T00:00:00Z",
                "printed_before": "2026-09-01T00:00:00Z",
            },
            {"printed_after": "2026-09-01"},
            {"print_duration_max_s": -1},
            {"printed_period": "last_month", "printed_after": "2026-09-01T00:00:00Z"},
            {"printed": False, "print_outcome": ["completed"]},
        ],
    )
    def test_rejects_untrusted_filters(self, db_session, parser_setup, invalid):
        user, _ = parser_setup
        provider = ParseProvider(parsed_output(**invalid))
        result = parsing.parse(
            db_session, user, "original query", provider_factory=lambda *_: provider
        )
        assert not result.parsed and result.reason == "search_parse_failed"
        assert result.residual_query == "original query"
        assert result.filters.model_dump(exclude_defaults=True) == {}

    @pytest.mark.parametrize(
        "value",
        [{"sort": "drop-table"}, {"extra": True}, {"residual_query": "x" * 513}],
    )
    def test_rejects_an_invalid_response_envelope(
        self, db_session, parser_setup, value
    ):
        user, _ = parser_setup
        provider = ParseProvider(parsed_output() | value)
        result = parsing.parse(
            db_session, user, "original", provider_factory=lambda *_: provider
        )
        assert not result.parsed and result.residual_query == "original"

    @pytest.mark.parametrize("outcome", [[], ["failed"], ["cancelled"]])
    def test_preserves_explicit_outcomes(self, db_session, parser_setup, outcome):
        user, _ = parser_setup
        provider = ParseProvider(parsed_output(printed=True, print_outcome=outcome))
        result = parsing.parse(
            db_session, user, "bracket", provider_factory=lambda *_: provider
        )
        assert result.parsed and result.filters.print_outcome == outcome

    def test_falls_back_on_provider_timeout(self, db_session, parser_setup):
        user, _ = parser_setup
        provider = ParseProvider(error=EmbeddingError("inference_timeout"))
        result = parsing.parse(
            db_session, user, "original", provider_factory=lambda *_: provider
        )
        assert (
            result.reason == "search_parse_failed"
            and result.residual_query == "original"
        )

    def test_excludes_hidden_collection_context(
        self, db_session, parser_setup, make_collection
    ):
        user, _ = parser_setup
        make_collection("hidden-secret")
        user.is_superuser = False
        db_session.add(user)
        db_session.commit()
        provider = ParseProvider()
        parsing.parse(db_session, user, "part", provider_factory=lambda *_: provider)
        context = json.loads(provider.requests[0].text)
        assert context["choices"]["collections"] == []
        assert context["choices"]["printers"] == []
        assert "hidden-secret" not in provider.requests[0].text

    def test_discards_reply_after_user_revokes_consent(self, db_session, parser_setup):
        user, _ = parser_setup
        provider = ParseProvider(
            before_reply=lambda: preferences.update(
                db_session, user, SearchPreferencesPatch(nl_filters_enabled=False)
            )
        )
        result = parsing.parse(
            db_session, user, "original", provider_factory=lambda *_: provider
        )
        assert not result.parsed and result.reason == "search_parse_unavailable"


class TestPreferences:
    def test_defaults_to_disabled_without_inserting_a_row(self, db_session, make_user):
        user = make_user()
        result = preferences.read(db_session, user)
        assert not result.nl_filters_enabled and not result.available
        assert result.effective_timezone == "UTC"
        assert db_session.exec(select(UserSearchPreferences)).all() == []

    def test_keeps_personal_preferences_isolated(
        self, db_session, parser_setup, make_user
    ):
        user, _ = parser_setup
        other = make_user()
        result = preferences.update(
            db_session, user, SearchPreferencesPatch(timezone="America/New_York")
        )
        assert result.effective_timezone == "America/New_York"
        assert preferences.read(db_session, other).effective_timezone == "Europe/Madrid"
        assert not preferences.read(db_session, other).nl_filters_enabled
        assert configuration.settings(db_session).timezone == "Europe/Madrid"
        reset = preferences.update(
            db_session, user, SearchPreferencesPatch(timezone=None)
        )
        assert reset.effective_timezone == "Europe/Madrid" and reset.nl_filters_enabled


class TestParseAdmission:
    def test_rejects_excess_work_without_egress(
        self, db_session, parser_setup, monkeypatch
    ):
        import threading

        user, _ = parser_setup
        slots = threading.BoundedSemaphore(1)
        slots.acquire()
        monkeypatch.setattr(parsing, "_slots", slots)
        provider = ParseProvider()
        result = parsing.parse(
            db_session, user, "original", provider_factory=lambda *_: provider
        )
        assert (
            result.reason == "search_parse_busy" and result.residual_query == "original"
        )
        assert provider.requests == []

    def test_bounds_the_context_before_egress(self, db_session, parser_setup):
        user, _ = parser_setup
        provider = ParseProvider()
        provider.endpoint = provider.endpoint.model_copy(
            update={"max_input_characters": 1}
        )
        result = parsing.parse(
            db_session, user, "original", provider_factory=lambda *_: provider
        )
        assert result.reason == "search_parse_context_limit"
        assert provider.requests == []
