"""Virtual SFTP directory with measured READDIR requests and SSH response bytes."""

import asyncio
import logging
import os
import stat
import time
from contextlib import contextmanager
from multiprocessing import get_context
from threading import Thread

import asyncssh
import psutil

from tests.fakes.discovery_http import _Metrics
from tests.fakes.mock_sftp import _AuthenticationServer


def _serve(count, counters, ready, observed_pid, delay):
    metrics = _Metrics(counters)

    def sample_memory():
        observed = psutil.Process(observed_pid)
        while True:
            metrics["peak_rss"] = max(metrics["peak_rss"], observed.memory_info().rss)
            time.sleep(0.01)

    Thread(target=sample_memory, daemon=True).start()

    class ReadDirCounter(logging.Handler):
        def emit(self, record):
            if "Received readdir for handle" in record.getMessage():
                metrics["requests"] += 1

    asyncssh.set_debug_level(1)
    logger = logging.getLogger("asyncssh")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(ReadDirCounter())

    class Authentication(_AuthenticationServer):
        def connection_made(self, connection):
            super().connection_made(connection)
            metrics["connections"] += 1
            transport = connection._transport
            write = transport.write

            def measured(data):
                metrics["bytes"] += len(data)
                write(data)

            transport.write = measured

    class Directory(asyncssh.SFTPServer):
        async def scandir(self, path):
            if path != b"library/models":
                raise asyncssh.SFTPNoSuchFile("outside virtual directory")
            if delay:
                await asyncio.sleep(delay)
            for index in range(count):
                yield asyncssh.SFTPName(
                    filename=f"{index:06}.gcode".encode(),
                    attrs=asyncssh.SFTPAttrs(
                        permissions=stat.S_IFREG | 0o644, size=6, mtime=1_788_220_800
                    ),
                )

    async def run():
        key = asyncssh.generate_private_key("ssh-ed25519")
        server = await asyncssh.listen(
            "127.0.0.1",
            0,
            server_host_keys=[key],
            server_factory=lambda: Authentication("contract"),
            sftp_factory=Directory,
        )
        port = server.get_port()
        ready.send(
            (
                port,
                f"[127.0.0.1]:{port} {key.export_public_key('openssh').decode().strip()}",
            )
        )
        ready.close()
        await asyncio.Future()

    asyncio.run(run())


@contextmanager
def sftp_directory_server(count, *, response_delay=0):
    context = get_context("spawn")
    counters = context.Array("q", [0, 0, 0, 0])
    parent, child = context.Pipe(duplex=False)
    process = context.Process(
        target=_serve,
        args=(count, counters, child, os.getpid(), response_delay),
        daemon=True,
    )
    process.start()
    child.close()
    try:
        if not parent.poll(15):
            raise RuntimeError("sftp_directory_server_start_timeout")
        port, known_host = parent.recv()
        yield port, known_host, _Metrics(counters)
    finally:
        parent.close()
        process.terminate()
        process.join(timeout=5)
        if process.is_alive():
            process.kill()
            process.join(timeout=5)
