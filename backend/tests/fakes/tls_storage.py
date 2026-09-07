"""TLS termination for the real SeaweedFS contract service.

The gateway preserves the signed Host and request target, so object authorization,
CORS and byte-range responses remain the real S3 service's responsibility.
"""

from __future__ import annotations

import http.client
import ipaddress
import ssl
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


@dataclass(frozen=True)
class TLSStorage:
    endpoint: str
    ca_file: Path

    def client_context(self) -> ssl.SSLContext:
        return ssl.create_default_context(cafile=str(self.ca_file))


@contextmanager
def tls_storage(upstream: str, directory: Path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    ca_file = directory / "storage-cert.pem"
    key_file = directory / "storage-key.pem"
    ca_file.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    key_file.chmod(0o600)
    target = urlsplit(upstream)

    class Gateway(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            pass  # Signed request targets must not reach logs.

        def forward(self):
            connection = http.client.HTTPConnection(
                target.hostname, target.port, timeout=15
            )
            try:
                length = int(self.headers.get("content-length", "0"))
                body = self.rfile.read(length) if length else None
                if self.headers.get("transfer-encoding", "").lower() == "chunked":
                    framed = bytearray()
                    while True:
                        line = self.rfile.readline()
                        framed.extend(line)
                        size = int(line.split(b";", 1)[0].strip(), 16)
                        if not size:
                            while True:
                                trailer = self.rfile.readline()
                                framed.extend(trailer)
                                if trailer == b"\r\n":
                                    break
                            break
                        framed.extend(self.rfile.read(size + 2))
                    body = bytes(framed)
                headers = {
                    name: value
                    for name, value in self.headers.items()
                    if name.lower() != "connection"
                }
                connection.request(self.command, self.path, body=body, headers=headers)
                response = connection.getresponse()
                data = response.read()
                self.send_response(response.status)
                for name, value in response.getheaders():
                    if name.lower() not in {
                        "connection",
                        "transfer-encoding",
                        "content-length",
                    }:
                        self.send_header(name, value)
                self.send_header(
                    "Content-Length",
                    response.getheader("Content-Length", "0")
                    if self.command == "HEAD"
                    else str(len(data)),
                )
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(data)
            finally:
                connection.close()

        do_GET = forward
        do_HEAD = forward
        do_PUT = forward
        do_POST = forward
        do_DELETE = forward
        do_OPTIONS = forward

    server = ThreadingHTTPServer(("127.0.0.1", 0), Gateway)
    server.daemon_threads = True
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(ca_file, key_file)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield TLSStorage(f"https://127.0.0.1:{server.server_port}", ca_file)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
