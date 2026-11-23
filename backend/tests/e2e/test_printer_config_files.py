"""An administrator edits the actual Moonraker config root through HTTP."""

import asyncio

import pytest

from tests.factories import build_printer
from tests.fakes.mock_printer import create_app
from tests.fakes.server import start_server


class TestSourceConfigEditor:
    @pytest.mark.asyncio
    async def test_config_save_reads_exact_bytes_from_moonraker(
        self, api, superuser_headers, e2e_db
    ):
        fake, state = create_app()
        running = start_server(fake)
        try:
            printer = build_printer(
                e2e_db, name="Config fixture", moonraker_url=running.base_url
            )
            url = f"/api/v1/printers/{printer.id}/config/file"
            path = "includes/disposable.cfg"
            args = {"headers": superuser_headers, "params": {"path": path}}
            loaded = (await api.get(url, **args)).json()

            async def save(content):
                return await api.put(
                    url,
                    **args,
                    json={"base_revision": loaded["revision"], "content": content},
                )

            a, b = await asyncio.gather(
                save("# first\r\n\r\n"), save("# second é\r\n\r\n")
            )
            assert sorted([a.status_code, b.status_code]) == [200, 409]
            saved = a if a.status_code == 200 else b
            assert saved.json() == (await api.get(url, **args)).json()
            assert saved.json()["content"].encode() == state.config_files[path]
            assert state.config_files[path].endswith(b"\r\n\r\n")
            assert state.sim.is_active() is False
        finally:
            running.stop()
