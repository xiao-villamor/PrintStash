"""Loopback AsyncSSH server for the OpenDAL SFTP storage contract."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import asyncssh


async def serve(*, host: str, port: int, root: Path, authorized_keys: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    host_key = asyncssh.generate_private_key("ssh-ed25519")
    await asyncssh.listen(
        host,
        port,
        server_host_keys=[host_key],
        authorized_client_keys=str(authorized_keys),
        sftp_factory=lambda channel: asyncssh.SFTPServer(
            channel, chroot=str(root).encode()
        ),
    )
    print("READY", flush=True)
    await asyncio.Future()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--authorized-keys", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(
        serve(
            host=args.host,
            port=args.port,
            root=args.root,
            authorized_keys=args.authorized_keys,
        )
    )


if __name__ == "__main__":  # pragma: no cover - subprocess entrypoint
    main()
