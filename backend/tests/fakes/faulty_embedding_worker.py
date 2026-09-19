"""Adversarial native IPC peer; uses real child pipes without loading a model."""

import json
import struct
import sys
import time


def main():
    mode = sys.argv[1]
    size = struct.unpack("!I", sys.stdin.buffer.read(4))[0]
    request = json.loads(sys.stdin.buffer.read(size))
    reply = {
        "config_hash": request["config_hash"],
        "vectors": [[1.0, 0.0, 0.0]],
        "truncated": [False],
    }
    if mode == "exit":
        return
    if mode == "identity":
        reply["config_hash"] = "foreign"
    if mode == "vectors":
        reply["vectors"] = []
    if mode == "truncations":
        reply["truncated"] = []
    body = b"private-invalid-output" if mode == "json" else json.dumps(reply).encode()
    frame = struct.pack("!I", len(body)) + body
    if mode == "oversized":
        frame = struct.pack("!I", 1024**2 + 1)
    if mode == "trailing":
        frame += b"excess"
    sys.stdout.buffer.write(frame)
    sys.stdout.buffer.flush()
    time.sleep(30)


if __name__ == "__main__":
    main()
