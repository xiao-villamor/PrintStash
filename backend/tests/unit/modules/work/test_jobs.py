"""The display-safe shape of a Job's status, independent of where it is stored.

A Job's status is rendered in a browser, and its inputs are filesystem paths,
provider URLs and error strings from third-party code. A signed download URL is
a credential; a local path discloses the server's layout; a control character
can rewrite a terminal or a log line. So every value merged into a status goes
through a sanitizer, and these rows defend each one without a database.

The merge is also where progress stays honest: it never moves backwards, never
claims 100% before the terminal transition, and never reports a negative count.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from app.db.models import Job, JobKind, JobState, WorkPriority
from app.modules.work.jobs import (
    _merge,
    _safe_result,
    failure_of,
    safe_error,
    safe_item,
    status_of,
)
from app.schemas.jobs import JobFailedItem, JobStatus


class TestSafeItem:
    def test_reduces_an_item_name_to_its_safe_basename(self) -> None:
        # A newline in a filename is what turns one log line into two, and the
        # directory is the server's layout rather than anything the user needs.
        assert safe_item("/mnt/nas/private/Cube\n.stl") == "Cube.stl"

    def test_reduces_a_windows_path_to_its_basename(self) -> None:
        assert safe_item("C:\\Users\\me\\part.3mf") == "part.3mf"

    def test_bounds_a_long_name(self) -> None:
        assert len(safe_item("a" * 500) or "") == 180

    @pytest.mark.parametrize("value", [None, "", "/srv/dir/", "\n\t"])
    def test_an_empty_name_is_absent(self, value: str | None) -> None:
        assert safe_item(value) is None


class TestFailureOf:
    def test_records_the_sanitized_message(self) -> None:
        assert failure_of(RuntimeError("read /srv/vault/a.stl failed")) == (
            "read [path] failed"
        )

    def test_names_an_exception_raised_without_a_message(self) -> None:
        # A failed Job must say why; an empty message still names the type.
        assert failure_of(TimeoutError()) == "TimeoutError"


class TestSafeError:
    def test_leaks_neither_path_nor_credential_from_an_error_message(self) -> None:
        error = safe_error("failed /mnt/nas/private/Cube.stl?api_key=hunter2")

        assert error is not None
        assert "/mnt/nas" not in error
        assert "hunter2" not in error

    @pytest.mark.parametrize(
        "name", ["token", "key", "secret", "password", "cookie", "signature"]
    )
    def test_redacts_each_secret_bearing_query_value(self, name: str) -> None:
        error = safe_error(f"provider refused {name}=hunter2&page=2")

        assert error is not None
        assert "hunter2" not in error
        assert f"{name}=[redacted]" in error

    def test_collapses_whitespace_so_one_error_is_one_line(self) -> None:
        assert safe_error("download\nfailed\r\n  twice") == "download failed twice"

    def test_bounds_a_long_error(self) -> None:
        assert len(safe_error("x" * 2000) or "") == 500

    @pytest.mark.parametrize("value", [None, ""])
    def test_an_empty_error_is_absent(self, value: str | None) -> None:
        assert safe_error(value) is None


class TestSafeResult:
    def test_sanitizes_nested_errors(self) -> None:
        result = _safe_result(
            {"errors": ["/srv/private/models/broken.stl: token=secret"]}
        )

        assert "/srv/private" not in str(result)
        assert "secret" not in str(result)

    def test_keeps_archive_entry_names_as_archive_relative_paths(self) -> None:
        # The UI submits these names back as the selection; a basename would
        # select nothing.
        entries = [{"name": "parts/deep/cube.stl", "entry_id": "e1"}]

        assert _safe_result({"entries": entries}) == {"entries": entries}

    def test_reduces_a_name_outside_entries_to_its_basename(self) -> None:
        assert _safe_result({"items": [{"name": "/srv/x/cube.stl"}]}) == {
            "items": [{"name": "cube.stl"}]
        }

    def test_leaves_values_that_are_not_display_text_alone(self) -> None:
        assert _safe_result({"imported": 2, "model_id": 7, "path": "a/b"}) == {
            "imported": 2,
            "model_id": 7,
            "path": "a/b",
        }


class TestMerge:
    def test_progress_never_moves_backwards(self) -> None:
        payload = _merge({}, {"progress": 40})

        assert _merge(payload, {"progress": 10})["progress"] == 40.0

    @pytest.mark.parametrize(("value", "expected"), [(-5, 0.0), (150, 99.0)])
    def test_progress_is_held_below_completion(
        self, value: float, expected: float
    ) -> None:
        # 100% is reserved for the terminal transition: a running Job that says
        # 100 reads as done to a user who then navigates away.
        assert _merge({}, {"progress": value})["progress"] == expected

    def test_counts_are_never_negative(self) -> None:
        assert _merge({}, {"processed": -3, "failed": -1}) == {
            "processed": 0,
            "failed": 0,
        }

    def test_ignores_fields_that_are_not_status(self) -> None:
        # ``state`` and ``job_id`` are row columns; letting the payload carry
        # them would let a stale payload contradict the row.
        assert _merge({}, {"state": "completed", "job_id": "x", "bogus": 1}) == {}

    def test_a_none_value_leaves_the_field_unchanged(self) -> None:
        assert _merge({"total": 3}, {"total": None}) == {"total": 3}

    def test_sanitizes_the_error(self) -> None:
        assert _merge({}, {"error": "read /srv/a.stl failed"}) == {
            "error": "read [path] failed"
        }

    def test_reduces_the_current_item_to_its_basename(self) -> None:
        assert _merge({}, {"current_item": "/srv/a/b.stl"}) == {"current_item": "b.stl"}

    def test_strips_the_server_path_from_a_failed_item(self) -> None:
        payload = _merge(
            {},
            {
                "failed_items": [
                    {
                        "name": "/srv/private/models/broken.stl",
                        "reason": "read /srv/private/models/broken.stl failed",
                        "retryable": True,
                    }
                ]
            },
        )

        (item,) = payload["failed_items"]
        assert item["name"] == "broken.stl"
        assert "/srv/private" not in item["reason"]
        assert item["retryable"] is True

    def test_strips_a_credential_from_a_failed_item(self) -> None:
        payload = _merge(
            {},
            {
                "failed_items": [
                    JobFailedItem(
                        name="broken.stl",
                        reason="read https://host/f.stl?token=secret failed",
                    )
                ]
            },
        )

        assert "secret" not in payload["failed_items"][0]["reason"]

    def test_refuses_a_failed_item_that_does_not_say_what_failed(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            _merge({}, {"failed_items": [{}]})

    def test_redacts_a_failed_item_sanitized_to_nothing(self) -> None:
        payload = _merge({}, {"failed_items": [{"name": "/", "reason": " "}]})

        assert payload["failed_items"] == [
            {"name": "[redacted]", "reason": "[redacted]", "retryable": False}
        ]

    def test_bounds_the_failed_items_list(self) -> None:
        items = [{"name": f"{i}.stl", "reason": "bad"} for i in range(250)]

        assert len(_merge({}, {"failed_items": items})["failed_items"]) == 100

    def test_renders_a_datetime_as_utc_iso(self) -> None:
        stamp = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)

        assert _merge({}, {"committed_at": stamp}) == {
            "committed_at": "2026-01-02T03:04:05+00:00"
        }


def _status(**fields) -> JobStatus:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    base = {
        "job_id": "j",
        "staging": None,
        "kind": JobKind.SOURCES_SCAN,
        "state": JobState.RUNNING,
        "priority": WorkPriority.BACKFILL,
        "attempts": 1,
        "resubmits": 0,
        "created_at": now,
        "updated_at": now,
    }
    return JobStatus.model_validate({**base, **fields})


class TestStatusOf:
    def test_reads_row_columns_over_the_payload(self) -> None:
        row = Job(
            id="j1",
            kind=JobKind.INGESTION_UPLOAD,
            subject_key="s",
            state=JobState.RUNNING,
            priority=WorkPriority.BACKFILL,
            status_json=json.dumps({"stage": "hashing", "unknown": 1}),
            attempts=2,
        )

        status = status_of(row)

        assert (status.job_id, status.kind, status.state, status.priority) == (
            "j1",
            JobKind.INGESTION_UPLOAD,
            "running",
            "backfill",
        )
        assert (status.stage, status.attempts) == ("hashing", 2)

    def test_a_terminal_state_reads_as_terminal(self) -> None:
        assert _status(state=JobState.CANCELLED).terminal is True
        assert _status(state=JobState.INTERRUPTED).terminal is False

    def test_progress_schema_rejects_unknown_stage(self) -> None:
        with pytest.raises(ValueError):
            _status(stage="uploading")

    def test_owner_is_never_serialized(self) -> None:
        status = _status(owner_user_id=7)

        assert "owner_user_id" not in status.model_dump(mode="json")
