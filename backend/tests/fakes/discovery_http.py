"""Generated S3/WebDAV directory protocols with observable pagination and traffic."""

import os
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from multiprocessing import get_context
from threading import Thread
from urllib.parse import parse_qs, urlsplit

import psutil


class _Metrics(dict):
    def __init__(self, counters):
        super().__init__(requests=0, connections=0, bytes=0, peak_rss=0)
        self.counters = counters

    def __getitem__(self, key):
        return self.counters[list(self.keys()).index(key)]

    def __setitem__(self, key, value):
        self.counters[list(self.keys()).index(key)] = value


def _serve(count, counters, ready, observed_pid, response_delay):
    metrics = _Metrics(counters)

    def sample_memory():
        observed = psutil.Process(observed_pid)
        while True:
            metrics["peak_rss"] = max(metrics["peak_rss"], observed.memory_info().rss)
            time.sleep(0.01)

    Thread(target=sample_memory, daemon=True).start()

    class Server(ThreadingHTTPServer):
        daemon_threads = True

        def get_request(self):
            metrics["connections"] += 1
            return super().get_request()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            pass

        def _send(self, chunks, status=200):
            if response_delay:
                time.sleep(response_delay)
            self.send_response(status)
            self.send_header("Content-Type", "application/xml")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for chunk in chunks:
                data = chunk.encode()
                metrics["bytes"] += len(data)
                self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
            self.wfile.write(b"0\r\n\r\n")

        def do_GET(self):
            metrics["requests"] += 1
            query = parse_qs(urlsplit(self.path).query)
            prefix = query.get("prefix", [""])[0]
            after = int(query.get("continuation-token", ["0"])[0])
            size = min(int(query.get("max-keys", ["1000"])[0]), 1000)
            end = min(count, after + size)

            def listing():
                yield '<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><Name>library</Name>'
                yield f"<IsTruncated>{str(end < count).lower()}</IsTruncated>"
                if end < count:
                    yield f"<NextContinuationToken>{end}</NextContinuationToken>"
                for index in range(after, end):
                    yield f'<Contents><Key>{prefix}{index:06}.gcode</Key><Size>6</Size><ETag>"stable"</ETag><LastModified>2026-09-01T00:00:00Z</LastModified></Contents>'
                yield "</ListBucketResult>"

            self._send(listing())

        def do_PROPFIND(self):
            metrics["requests"] += 1
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            prefix = urlsplit(self.path).path.rstrip("/") + "/"

            def listing():
                yield '<d:multistatus xmlns:d="DAV:">'
                yield f"<d:response><d:href>{prefix}</d:href><d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype><d:getlastmodified>Tue, 01 Sep 2026 00:00:00 GMT</d:getlastmodified></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
                for index in range(count):
                    yield f'<d:response><d:href>{prefix}{index:06}.gcode</d:href><d:propstat><d:prop><d:resourcetype/><d:getcontentlength>6</d:getcontentlength><d:getetag>"stable"</d:getetag><d:getlastmodified>Tue, 01 Sep 2026 00:00:00 GMT</d:getlastmodified></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>'
                yield "</d:multistatus>"

            self._send(listing(), 207)

    server = Server(("127.0.0.1", 0), Handler)
    ready.send(server.server_port)
    ready.close()
    server.serve_forever()


@contextmanager
def directory_server(count: int, *, webdav: bool = False, response_delay: float = 0):
    # OpenDAL's blocking binding may hold the GIL. The actual protocol server
    # must run in another process, as the other native-client contracts do.
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
            raise RuntimeError("directory_server_start_timeout")
        port = parent.recv()
        yield (
            f"http://127.0.0.1:{port}" + ("/dav" if webdav else ""),
            _Metrics(counters),
        )
    finally:
        parent.close()
        process.terminate()
        process.join(timeout=5)
        if process.is_alive():
            process.kill()
            process.join(timeout=5)
