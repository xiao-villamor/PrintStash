"""Byte transfer over Bambu's implicit-TLS FTPS, and the paths it will accept.

Two separate contracts live here.

The first is a **security boundary**. A download path arrives over MQTT — that
is, from the printer, which is a device on the LAN that PrintStash does not
control — and is then used to name a file on the host's disk. Traversal, an
absolute path, a Windows separator, a percent-encoded `..`, or a hostname
belonging to some other machine must all be refused before a socket is opened.
Every one of those refusals is a test here, because the failure mode is writing
outside the artifact directory.

The second is a **retry policy**, which is subtler than it looks. Bambu's FTPS
server drops the first connection often enough that a single replay is worth
having, but a replay is only safe when the failure was a transport outcome: a
reset, an EOF, a timeout. Replaying a 530 login rejection re-sends the access
code and risks a lockout; replaying a 552 over-quota or a missing local file
just fails again more slowly. So classification decides retryability, one
exception class at a time, and the retry itself is capped at exactly one extra
attempt. If this file goes red, either a hostile path reached the disk or a
failed upload started hammering the printer.
"""

from __future__ import annotations

import errno
import socket
import ssl
from ftplib import error_perm, error_reply, error_temp
from pathlib import Path

import pytest

from printstash_core.printers.bambu import BambuClient
from printstash_core.printers.models import ProviderError

from .conftest import (
    ACCESS_CODE,
    HOST,
    SERIAL,
    FailingFtpsClient,
    FakeFtpsClient,
    connect_attempts,
    make_client,
)

# Big enough that the guards under test are the only thing that can trip.
GENEROUS_LIMIT = 1024


class TestClassifyFtpsException:
    @pytest.mark.parametrize(
        ("exception", "action_code"),
        [
            (ssl.SSLError("handshake failure"), "bambu_ftps_tls_error"),
            (socket.timeout("timed out"), "bambu_ftps_timeout"),
            (TimeoutError(), "bambu_ftps_timeout"),
            (ConnectionResetError(), "bambu_ftps_connection_reset"),
            (EOFError(), "bambu_ftps_eof"),
            (error_perm("530 Login incorrect"), "bambu_ftps_authentication_failed"),
            (error_perm("552 storage exceeded"), "bambu_ftps_too_large"),
            (error_perm("450 file unavailable"), "bambu_ftps_not_found"),
            (error_perm("550 file unavailable"), "bambu_ftps_not_found"),
            (error_temp("451 not found on device"), "bambu_ftps_not_found"),
            (error_perm("553 bad path name"), "bambu_ftps_path_invalid"),
            (error_reply("501 command syntax error"), "bambu_ftps_server_rejected"),
            (error_reply("452 insufficient storage"), "bambu_ftps_server_rejected"),
            (socket.gaierror("name resolution failed"), "bambu_ftps_transport_error"),
            (ConnectionRefusedError(), "bambu_ftps_transport_error"),
            (RuntimeError("something else entirely"), "bambu_ftps_unknown_error"),
        ],
    )
    def test_names_an_actionable_cause_for_each_transport_failure(
        self, exception: BaseException, action_code: str
    ) -> None:
        # The action code is persisted on the job and shown to the operator, so
        # each of these is a distinct sentence in the UI rather than a category.
        assert (
            BambuClient._classify_ftps_exception(exception).action_code == action_code
        )

    @pytest.mark.parametrize(
        ("exception", "retryable"),
        [
            (ConnectionResetError(), True),
            (EOFError(), True),
            (TimeoutError(), True),
            (ConnectionRefusedError(), True),
            (error_perm("530 Login incorrect"), False),
            (error_perm("552 storage exceeded"), False),
            (error_reply("501 command syntax error"), False),
            (ssl.SSLError("handshake failure"), False),
            (FileNotFoundError("local source is missing"), False),
            (RuntimeError("something else entirely"), False),
        ],
    )
    def test_marks_only_transport_outcomes_retryable(
        self, exception: BaseException, retryable: bool
    ) -> None:
        # Replaying a 530 re-sends the access code; replaying a 552 fails again.
        # Only a dropped connection earns a second attempt.
        assert BambuClient._classify_ftps_exception(exception).retryable is retryable

    def test_keeps_a_login_failure_on_the_coarse_transport_code(self) -> None:
        error = BambuClient._classify_ftps_exception(error_perm("530 Login incorrect"))

        # The API's compatibility contract classes an FTPS login rejection as a
        # transport failure; the actionable reason rides along in `action_code`.
        assert error.code == "provider_transport_error"
        assert error.action_code == "bambu_ftps_authentication_failed"

    def test_recognizes_a_login_rejection_wrapped_in_permission_error(self) -> None:
        # Some FTPS stacks raise PermissionError rather than ftplib.error_perm.
        error = BambuClient._classify_ftps_exception(
            PermissionError("  530 Login incorrect  ")
        )

        assert error.action_code == "bambu_ftps_authentication_failed"

    def test_treats_a_local_permission_failure_as_local_not_authentication(
        self,
    ) -> None:
        error = BambuClient._classify_ftps_exception(
            PermissionError(13, "Permission denied", "/srv/auth/cache.gcode")
        )

        # The path contains "auth", which must not be mistaken for a 530 reply —
        # otherwise a local disk permission problem reads as a wrong access code.
        assert error.action_code == "bambu_ftps_local_error"

    def test_treats_a_local_file_failure_as_local(self) -> None:
        error = BambuClient._classify_ftps_exception(
            FileNotFoundError("local source is missing")
        )

        assert error.action_code == "bambu_ftps_local_error"

    def test_treats_a_networking_errno_as_a_transport_failure(self) -> None:
        error = BambuClient._classify_ftps_exception(
            OSError(errno.EHOSTUNREACH, "No route to host")
        )

        assert error.action_code == "bambu_ftps_transport_error"
        assert error.retryable is True

    def test_passes_an_already_classified_error_through_unchanged(self) -> None:
        original = ProviderError("already classified", action_code="bambu_ftps_eof")

        assert BambuClient._classify_ftps_exception(original) is original


class TestIsFtpsAuthenticationResponse:
    def test_recognizes_a_530_reply(self) -> None:
        assert BambuClient._is_ftps_authentication_response("530 Login incorrect")

    def test_recognizes_an_auth_keyword_reply(self) -> None:
        assert BambuClient._is_ftps_authentication_response("500 AUTH not understood")

    def test_ignores_the_auth_keyword_when_the_caller_forbids_it(self) -> None:
        # An OSError's message may contain a local path with "auth" in it, which
        # is not a server reply at all.
        assert not BambuClient._is_ftps_authentication_response(
            "/srv/auth/cache.gcode", allow_auth_keyword=False
        )

    def test_rejects_an_ordinary_reply(self) -> None:
        assert not BambuClient._is_ftps_authentication_response("226 Transfer complete")


class TestProviderError:
    def test_defaults_the_action_code_to_the_coarse_code(self) -> None:
        error = ProviderError("remote server detail", code="provider_timeout")

        # Anything raised without a specific action code still has one, so the
        # persisted field is never empty.
        assert error.action_code == "provider_timeout"
        assert error.detail == "remote server detail"

    def test_defaults_to_not_retryable(self) -> None:
        assert ProviderError("detail", code="provider_timeout").retryable is False


class TestWithFtpsRetry:
    def test_returns_without_a_second_attempt_when_the_first_succeeds(self) -> None:
        attempts = 0

        def operation() -> None:
            nonlocal attempts
            attempts += 1

        BambuClient._with_ftps_retry(operation)

        assert attempts == 1

    def test_replays_exactly_once_after_a_retryable_transport_failure(self) -> None:
        attempts = 0

        def operation() -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise ProviderError(
                    "temporary FTPS transport outcome",
                    action_code="bambu_ftps_transport_error",
                    retryable=True,
                )

        BambuClient._with_ftps_retry(operation)

        assert attempts == 2

    @pytest.mark.parametrize(
        ("exception", "action_code"),
        [
            (FileNotFoundError("local source is missing"), "bambu_ftps_local_error"),
            (
                PermissionError("local destination is not writable"),
                "bambu_ftps_local_error",
            ),
            (RuntimeError("unexpected callback failure"), "bambu_ftps_unknown_error"),
        ],
    )
    def test_does_not_replay_a_failure_on_the_local_side(
        self, exception: BaseException, action_code: str
    ) -> None:
        attempts = 0

        def operation() -> None:
            nonlocal attempts
            attempts += 1
            raise exception

        with pytest.raises(ProviderError) as error:
            BambuClient._with_ftps_retry(operation)

        assert error.value.action_code == action_code
        assert attempts == 1

    def test_does_not_replay_a_retryable_error_outside_the_allow_list(self) -> None:
        attempts = 0

        def operation() -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise ProviderError("temporary provider outcome", retryable=True)

        with pytest.raises(ProviderError) as error:
            BambuClient._with_ftps_retry(operation)

        # `retryable=True` alone is not a licence to replay a *file transfer*.
        # The action code has to be one this client knows is safe to repeat.
        assert error.value.action_code == "provider_error"
        assert attempts == 1

    def test_raises_the_classified_failure_after_the_replay_also_fails(self) -> None:
        attempts = 0

        def operation() -> None:
            nonlocal attempts
            attempts += 1
            raise ConnectionResetError("peer reset")

        with pytest.raises(ProviderError) as error:
            BambuClient._with_ftps_retry(operation)

        assert error.value.action_code == "bambu_ftps_connection_reset"
        assert attempts == 2


class TestCloseFtps:
    def test_falls_back_to_close_when_quit_fails(self) -> None:
        class HalfOpen:
            def __init__(self) -> None:
                self.closed = False

            def quit(self) -> None:
                raise EOFError("connection died before the greeting")

            def close(self) -> None:
                self.closed = True

        ftp = HalfOpen()

        BambuClient._close_ftps(ftp)

        # `quit` sends a command, which a half-open socket cannot carry; the
        # socket still has to be released or the connection leaks.
        assert ftp.closed is True

    def test_swallows_a_failure_from_close_itself(self) -> None:
        class Unclosable:
            def quit(self) -> None:
                raise EOFError("connection died before the greeting")

            def close(self) -> None:
                raise OSError("socket already gone")

        # Cleanup runs in a `finally`; raising here would replace the caller's
        # real error with a meaningless one.
        BambuClient._close_ftps(Unclosable())


class TestUploadViaFtps:
    def test_publishes_an_upload_by_renaming_it_into_place(
        self, source_file: Path
    ) -> None:
        ftp = FakeFtpsClient()
        client = make_client(
            ftps_client_factory=lambda: ftp, sequence_id_factory=lambda: "fixed-id"
        )

        client._upload_via_ftps(source_file, "cube.gcode")

        temporary = "cache/.cube.gcode.fixed-id.uploading"
        # A partial upload must never appear under the real name: the printer
        # would happily start printing a truncated file.
        assert ftp.calls == [
            ("connect", HOST, 990),
            ("login", "bblp", ACCESS_CODE),
            ("prot_p",),
            ("storbinary", f"STOR {temporary}"),
            ("size", temporary),
            ("rename", temporary, "cache/cube.gcode"),
            ("quit",),
        ]

    def test_sends_the_file_bytes_unchanged(self, source_file: Path) -> None:
        ftp = FakeFtpsClient()
        client = make_client(ftps_client_factory=lambda: ftp)

        client._upload_via_ftps(source_file, "cube.gcode")

        assert ftp.uploaded == b"G28\n"

    def test_replays_a_dropped_connection_on_a_fresh_client(
        self, source_file: Path
    ) -> None:
        first = FailingFtpsClient(ConnectionResetError("peer reset"))
        second = FakeFtpsClient()
        clients = iter((first, second))
        client = make_client(ftps_client_factory=lambda: next(clients))

        client._upload_via_ftps(source_file, "cube.gcode")

        # A second attempt on the *same* client would reuse a dead socket.
        assert (connect_attempts(first), connect_attempts(second)) == (1, 1)

    def test_replays_an_eof_before_the_greeting(self, source_file: Path) -> None:
        first = FailingFtpsClient(EOFError("unexpected EOF"))
        second = FakeFtpsClient()
        clients = iter((first, second))
        client = make_client(ftps_client_factory=lambda: next(clients))

        client._upload_via_ftps(source_file, "cube.gcode")

        assert (connect_attempts(first), connect_attempts(second)) == (1, 1)

    @pytest.mark.parametrize(
        "remote_filename", ["folder/cube.gcode", "/cube.gcode", "", "."]
    )
    def test_refuses_a_remote_name_that_is_not_a_bare_filename(
        self, source_file: Path, remote_filename: str
    ) -> None:
        client = make_client(ftps_client_factory=lambda: FakeFtpsClient())

        with pytest.raises(ProviderError) as error:
            client._upload_via_ftps(source_file, remote_filename)

        assert error.value.action_code == "bambu_ftps_path_invalid"

    def test_reports_a_remote_size_that_disagrees_with_the_local_file(
        self, source_file: Path
    ) -> None:
        client = make_client(ftps_client_factory=lambda: FakeFtpsClient(remote_size=99))

        with pytest.raises(ProviderError) as error:
            client._upload_via_ftps(source_file, "cube.gcode")

        # The rename is what publishes the file, so a size disagreement has to
        # stop before it — the truncated upload stays under its temporary name.
        assert error.value.action_code == "bambu_ftps_size_mismatch"

    def test_does_not_open_a_second_connection_for_a_missing_local_file(
        self, tmp_path: Path
    ) -> None:
        attempts = 0

        def factory() -> FakeFtpsClient:
            nonlocal attempts
            attempts += 1
            return FakeFtpsClient()

        client = make_client(ftps_client_factory=factory)

        with pytest.raises(ProviderError) as error:
            client._upload_via_ftps(tmp_path / "missing.gcode", "cube.gcode")

        assert error.value.action_code == "bambu_ftps_local_error"
        assert attempts == 1

    def test_does_not_replay_a_reply_the_server_rejected(
        self, source_file: Path
    ) -> None:
        attempts = 0

        def factory() -> FailingFtpsClient:
            nonlocal attempts
            attempts += 1
            return FailingFtpsClient(error_reply("501 command syntax error"))

        client = make_client(ftps_client_factory=factory)

        with pytest.raises(ProviderError) as error:
            client._upload_via_ftps(source_file, "cube.gcode")

        assert error.value.action_code == "bambu_ftps_server_rejected"
        assert attempts == 1


class TestDownloadViaFtps:
    def test_writes_the_retrieved_bytes_to_the_destination(
        self, tmp_path: Path
    ) -> None:
        ftp = FakeFtpsClient(download=b"1234")
        client = make_client(ftps_client_factory=lambda: ftp)
        destination = tmp_path / "benchy.3mf"

        client._download_via_ftps("benchy.3mf", destination, max_bytes=GENEROUS_LIMIT)

        assert destination.read_bytes() == b"1234"

    def test_reads_a_bare_filename_out_of_the_cache_directory(
        self, tmp_path: Path
    ) -> None:
        ftp = FakeFtpsClient(download=b"1234")
        client = make_client(ftps_client_factory=lambda: ftp)

        client._download_via_ftps(
            "benchy.3mf", tmp_path / "benchy.3mf", max_bytes=GENEROUS_LIMIT
        )

        assert ("retrbinary", "RETR cache/benchy.3mf") in ftp.calls

    def test_accepts_an_ftps_url_naming_this_printer_by_serial(
        self, tmp_path: Path
    ) -> None:
        ftp = FakeFtpsClient(download=b"1234")
        client = make_client(ftps_client_factory=lambda: ftp)

        client._download_via_ftps(
            f"ftps://{SERIAL}/cache/benchy.3mf",
            tmp_path / "benchy.3mf",
            max_bytes=GENEROUS_LIMIT,
        )

        # Bambu reports its own artifact URLs by serial, not by address, and
        # real serials are upper case — while `urlparse` lower-cases the host
        # component. A case-sensitive comparison here refused every genuine
        # capture URL, so no external Bambu print could ever be captured.
        assert ("retrbinary", "RETR cache/benchy.3mf") in ftp.calls

    def test_decodes_a_percent_encoded_filename(self, tmp_path: Path) -> None:
        ftp = FakeFtpsClient(download=b"1234")
        client = make_client(ftps_client_factory=lambda: ftp)

        client._download_via_ftps(
            "ftps://192.0.2.10/cache/my%20part.3mf",
            tmp_path / "part.3mf",
            max_bytes=GENEROUS_LIMIT,
        )

        assert ("retrbinary", "RETR cache/my part.3mf") in ftp.calls

    @pytest.mark.parametrize(
        "remote_path",
        [
            "cache/../benchy.3mf",
            "cache/./benchy.3mf",
            "cache/%2e%2e/benchy.3mf",
            "cache\\..\\benchy.3mf",
            "/etc/passwd.3mf",
            "uploads/benchy.3mf",
            "",
            "/",
            "http://192.0.2.10/cache/benchy.3mf",
            "file:///etc/shadow.3mf",
        ],
    )
    def test_refuses_a_path_that_could_escape_the_cache_directory(
        self, tmp_path: Path, remote_path: str
    ) -> None:
        client = make_client()

        with pytest.raises(ProviderError) as error:
            client._download_via_ftps(
                remote_path, tmp_path / "out", max_bytes=GENEROUS_LIMIT
            )

        # This path came from the printer over MQTT, so it is untrusted input
        # naming a file on the host's disk. Refuse before opening a socket.
        assert error.value.action_code == "bambu_ftps_path_invalid"

    def test_refuses_a_url_naming_another_host(self, tmp_path: Path) -> None:
        client = make_client()

        with pytest.raises(ProviderError) as error:
            client._download_via_ftps(
                "ftps://other.invalid/cache/benchy.3mf",
                tmp_path / "out",
                max_bytes=GENEROUS_LIMIT,
            )

        assert error.value.detail == "invalid_bambu_artifact_host"

    @pytest.mark.parametrize("suffix", [".txt", ".zip", "", ".gcode.exe"])
    def test_refuses_a_file_that_is_not_a_printable_artifact(
        self, tmp_path: Path, suffix: str
    ) -> None:
        client = make_client()

        with pytest.raises(ProviderError) as error:
            client._download_via_ftps(
                f"cache/benchy{suffix}", tmp_path / "out", max_bytes=GENEROUS_LIMIT
            )

        assert error.value.detail == "unsupported_bambu_artifact"

    @pytest.mark.parametrize(
        "filename", ["a.gcode", "a.g", "a.gco", "a.bgcode", "a.3mf", "A.3MF"]
    )
    def test_accepts_every_artifact_extension_bambu_can_hold(
        self, tmp_path: Path, filename: str
    ) -> None:
        ftp = FakeFtpsClient(download=b"1234")
        client = make_client(ftps_client_factory=lambda: ftp)

        client._download_via_ftps(filename, tmp_path / "out", max_bytes=GENEROUS_LIMIT)

        assert ("retrbinary", f"RETR cache/{filename}") in ftp.calls

    def test_refuses_a_file_the_server_reports_as_over_the_limit(
        self, tmp_path: Path
    ) -> None:
        client = make_client(ftps_client_factory=lambda: FakeFtpsClient(remote_size=99))

        with pytest.raises(ProviderError) as error:
            client._download_via_ftps("benchy.3mf", tmp_path / "out", max_bytes=4)

        # Checked from SIZE first, so an oversized artifact costs no bytes.
        assert error.value.action_code == "bambu_ftps_too_large"

    def test_stops_a_transfer_that_grows_past_the_limit_mid_stream(
        self, tmp_path: Path
    ) -> None:
        # SIZE said 4, the transfer delivers 5: the guard has to hold on the
        # bytes actually received, not on the server's claim about them.
        ftp = FakeFtpsClient(remote_size=4, download=b"12345")
        client = make_client(ftps_client_factory=lambda: ftp)

        with pytest.raises(ProviderError) as error:
            client._download_via_ftps("benchy.3mf", tmp_path / "out", max_bytes=4)

        assert error.value.action_code == "bambu_ftps_too_large"

    def test_reports_a_transfer_shorter_than_the_server_promised(
        self, tmp_path: Path
    ) -> None:
        ftp = FakeFtpsClient(remote_size=10, download=b"1234")
        client = make_client(ftps_client_factory=lambda: ftp)

        with pytest.raises(ProviderError) as error:
            client._download_via_ftps(
                "benchy.3mf", tmp_path / "out", max_bytes=GENEROUS_LIMIT
            )

        # A truncated artifact must not be accepted as the print's evidence.
        assert error.value.action_code == "bambu_ftps_size_mismatch"

    def test_does_not_replay_a_missing_remote_file(self, tmp_path: Path) -> None:
        attempts = 0

        def factory() -> FailingFtpsClient:
            nonlocal attempts
            attempts += 1
            return FailingFtpsClient(error_perm("450 file unavailable"))

        client = make_client(ftps_client_factory=factory)

        with pytest.raises(ProviderError) as error:
            client._download_via_ftps(
                "benchy.3mf", tmp_path / "out", max_bytes=GENEROUS_LIMIT
            )

        assert error.value.action_code == "bambu_ftps_not_found"
        assert attempts == 1

    def test_does_not_replay_an_over_quota_reply(self, tmp_path: Path) -> None:
        attempts = 0

        def factory() -> FailingFtpsClient:
            nonlocal attempts
            attempts += 1
            return FailingFtpsClient(error_perm("552 storage exceeded"))

        client = make_client(ftps_client_factory=factory)

        with pytest.raises(ProviderError) as error:
            client._download_via_ftps(
                "benchy.3mf", tmp_path / "out", max_bytes=GENEROUS_LIMIT
            )

        assert error.value.action_code == "bambu_ftps_too_large"
        assert attempts == 1
