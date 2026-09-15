"""Native-pipe peer for malformed sparse responses; no model or application mocks."""

import struct
import sys
import time


def main():
    payload = sys.argv[1].encode()
    while header := sys.stdin.buffer.read(4):
        if len(header) != 4:
            return
        sys.stdin.buffer.read(struct.unpack("!I", header)[0])
        if payload == b"stall":
            time.sleep(60)
        sys.stdout.buffer.write(struct.pack("!I", len(payload)) + payload)
        sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
