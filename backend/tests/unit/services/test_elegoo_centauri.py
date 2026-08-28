"""The legacy constructor still in front of the extracted Centauri client.

The client moved into `printstash-core` and is tested there against fake
connections (`printers/test_elegoo_centauri.py`). This facade is more than a
signature adapter, though, and that is why it still has tests of its own.

The core config validates eagerly: a second-generation Centauri without an access
code is not a buildable configuration. The legacy constructor cannot be that
strict, because callers build a client from a stored printer row and only then
find out the row is incomplete — raising at construction turns a printer the user
can see and fix into an error on an unrelated page. So `_compat_config`
substitutes placeholders to get past validation and the constructor writes the
caller's real values back over the attributes, which keeps the failure where it
belongs: on the first connection attempt, as a credentials error.

That trick is exactly the kind of thing that decays silently. If the write-back
stops happening, the client connects to a placeholder host; if the placeholder
stops being substituted, every CC2 row raises at construction. Both are tested
here.
"""

from __future__ import annotations

import pytest
from printstash_core.printers.elegoo_centauri import (
    ElegooCentauriClient as CoreElegooCentauriClient,
)
from printstash_core.printers.elegoo_centauri import (
    ElegooCentauriError as CoreElegooCentauriError,
)

from app.services.elegoo_centauri import ElegooCentauriClient, ElegooCentauriError

HOST = "192.168.1.50"
CC1 = "elegoo_centauri_carbon"
CC2 = "elegoo_centauri_carbon_2"
ACCESS_CODE = "0000"


class TestElegooCentauriClient:
    def test_builds_a_core_client_from_the_legacy_arguments(self) -> None:
        client = ElegooCentauriClient(HOST, model=CC1)

        assert isinstance(client, CoreElegooCentauriClient)

    def test_keeps_the_caller_values_the_connection_code_reads(self) -> None:
        client = ElegooCentauriClient(
            HOST, model=CC2, access_code=ACCESS_CODE, mainboard_id="mb-1"
        )

        # `_compat_config` may have substituted placeholders to satisfy the core
        # config's eager validation; the constructor writes the caller's values
        # back, and these four attributes are what the inherited connection code
        # actually dials.
        assert (client.host, client.model) == (HOST, CC2)
        assert (client.access_code, client.mainboard_id) == (ACCESS_CODE, "mb-1")

    def test_accepts_a_second_generation_row_with_no_access_code(self) -> None:
        client = ElegooCentauriClient(HOST, model=CC2)

        # The core config refuses this outright. Refusing it here would raise
        # while merely listing printers, so the row builds and keeps `None`.
        assert client.access_code is None

    def test_accepts_an_unrecognised_model_name(self) -> None:
        client = ElegooCentauriClient(HOST, model="elegoo_something_new")

        # A model string the core config does not know still has to build: the
        # value came out of the database, and a stored row must never make an
        # endpoint unconstructable.
        assert client.model == "elegoo_something_new"

    @pytest.mark.asyncio
    async def test_reports_a_missing_access_code_on_the_first_connection(self) -> None:
        client = ElegooCentauriClient(HOST, model=CC2)

        with pytest.raises(ElegooCentauriError) as error:
            await client.query_status()

        # The deferred half of the trade-off above: the incomplete row surfaces
        # as a credentials error against that printer, which is actionable, and
        # not as an exception on whatever page happened to build the client.
        assert error.value.code == "provider_credentials_missing"

    def test_re_exports_the_error_class_core_actually_raises(self) -> None:
        assert ElegooCentauriError is CoreElegooCentauriError
