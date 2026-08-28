"""Loopback AsyncSSH server for the OpenDAL SFTP storage contract."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import asyncssh


class _AuthenticationServer(asyncssh.SSHServer):
    def __init__(self, password: str | None) -> None:
        self._password = password

    def begin_auth(self, username: str) -> bool:
        del username
        return True

    def password_auth_supported(self) -> bool:
        return self._password is not None

    def validate_password(self, username: str, password: str) -> bool:
        return username == "printstash" and password == self._password

    def public_key_auth_supported(self) -> bool:
        return self._password is None

    def validate_public_key(self, username: str, key: asyncssh.SSHKey) -> bool:
        del key
        return username == "printstash" and self._password is None


async def serve(
    *,
    host: str,
    port: int,
    root: Path,
    password: str | None,
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    host_key = asyncssh.generate_private_key("ssh-ed25519")
    await asyncssh.listen(
        host,
        port,
        server_host_keys=[host_key],
        server_factory=lambda: _AuthenticationServer(password),
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
    parser.add_argument("--authorized-keys", type=Path)
    parser.add_argument("--password")
    args = parser.parse_args()
    asyncio.run(
        serve(
            host=args.host,
            port=args.port,
            root=args.root,
            password=args.password,
        )
    )


if __name__ == "__main__":  # pragma: no cover - subprocess entrypoint
    main()
