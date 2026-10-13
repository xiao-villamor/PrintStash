"""Controlled S3 read endpoint for native I/O responsiveness contracts.

Runs in a separate process: a native binding holding the client GIL must not
delay the server as well, which would conceal the regression being measured.
"""

import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

PAYLOAD = bytes(range(256)) * 256


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_HEAD(self):
        self.respond(head=True)

    def do_GET(self):
        self.respond(head=False)

    def respond(self, *, head):
        path = urlsplit(self.path)
        time.sleep(
            2.0
            if "very-slow" in self.path
            else 0.4
            if "slow" in self.path or path.path.rstrip("/") == "/test"
            else 0.01
        )
        if "missing" in path.path:
            self.send_error(404)
            return
        if self.headers.get("If-Match", '"contract"') != '"contract"':
            self.send_error(412)
            return
        query = parse_qs(path.query)
        if "list-type" in query:
            body = b"""<?xml version="1.0" encoding="UTF-8"?>
<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
<Name>test</Name><Prefix></Prefix><KeyCount>1</KeyCount><MaxKeys>1000</MaxKeys>
<IsTruncated>false</IsTruncated><Contents><Key>slow/object</Key>
<LastModified>2026-01-01T00:00:00.000Z</LastModified><ETag>"contract"</ETag>
<Size>65536</Size><StorageClass>STANDARD</StorageClass></Contents>
</ListBucketResult>"""
        else:
            body = PAYLOAD
        status = 200
        byte_range = self.headers.get("Range")
        content_range = None
        if byte_range and not head:
            first, last = byte_range.removeprefix("bytes=").split("-")
            start, end = int(first), int(last) if last else len(body) - 1
            end = min(end, len(body) - 1)
            content_range = f"bytes {start}-{end}/{len(body)}"
            body = body[start : end + 1]
            status = 206
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("ETag", '"contract"')
        self.send_header("Last-Modified", "Thu, 01 Jan 2026 00:00:00 GMT")
        if content_range:
            self.send_header("Content-Range", content_range)
        self.end_headers()
        if not head:
            try:
                self.wfile.write(body)
            except BrokenPipeError, ConnectionResetError:
                pass


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    print(server.server_port, flush=True)
    server.serve_forever()
