"""Independent pipe oracle for inference retirement wire exit 73."""

import json
import struct
import sys


def main() -> int:
    case = sys.argv[1]
    if case == "first-frame":
        from app.modules.inference.worker import serve

        assets = sys.argv[2]
        sys.argv = ["worker", assets, "two-tower-contract", "1", "--persistent"]
        print("ready", file=sys.stderr, flush=True)
        return serve(sys.stdin.buffer, sys.stdout.buffer, idle_pressure=lambda: True)
    if case in ("retire", "oom", "failed"):
        return {"retire": 73, "oom": 72, "failed": 1}[case]
    header = sys.stdin.buffer.read(4)
    if len(header) != 4:
        return 2
    payload = sys.stdin.buffer.read(struct.unpack("!I", header)[0])
    request = json.loads(payload)
    response = json.dumps(
        {
            "config_hash": request["config_hash"],
            "vectors": [[1.0, 0.0, 0.0]],
            "truncated": [False],
        }
    ).encode()
    if case == "malformed":
        sys.stdout.buffer.write(struct.pack("!I", len(response)) + response + b"extra")
    elif case == "partial":
        sys.stdout.buffer.write(struct.pack("!I", len(response)) + response[:2])
    elif case == "reply":
        sys.stdout.buffer.write(struct.pack("!I", len(response)) + response)
    else:
        raise ValueError(case)
    sys.stdout.buffer.flush()
    return 73


if __name__ == "__main__":
    raise SystemExit(main())
