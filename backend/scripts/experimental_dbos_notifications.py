"""Apply the isolated DBOS notification experiment to a disposable SDK copy.

Never run this against a shared environment. The source hashes must match the
frozen baseline; this is an experimental dependency patch, not a supported API.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path

EXPECTED = {
    "_queue.py": "33c287b8f31ee498216ebb4751c914eaccc02077b09ad5be42207c86dc8ff03f",
    "_core.py": "71932bf0b5c7dc45f082bc38157e3ee13bc5c98cf0398fcb28b92a1ee68e2da2",
    "_sys_db.py": "966d2cbd1fb3ed7f7199c902b10ead239baa296dc910dbb07b953af50cfbad6a",
}


def replace_once(source: str, old: str, new: str) -> str:
    if source.count(old) != 1:
        raise ValueError(f"patch context is not unique: {old[:80]}")
    return source.replace(old, new, 1)


def patch(root: Path) -> None:
    sources = {name: (root / name).read_text() for name in EXPECTED}
    for name, digest in EXPECTED.items():
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != digest:
            raise ValueError(f"unsupported DBOS source: {name}")
    imports = (
        "from app.runtime.engine.dbos_notifications import ClaimRetry, RECOVERY_SECONDS, signals\n"
        "PRINTSTASH_NOTIFICATION_PATCH = 2\n"
    )
    for name in sources:
        s = sources[name]
        tree = ast.parse(s)
        future = [
            n
            for n in tree.body
            if isinstance(n, ast.ImportFrom) and n.module == "__future__"
        ]
        at = (
            future[-1].end_lineno
            if future
            else (
                tree.body[0].end_lineno
                if isinstance(tree.body[0], ast.Expr)
                and isinstance(tree.body[0].value, ast.Constant)
                else 0
            )
        )
        lines = s.splitlines(keepends=True)
        lines.insert(at, imports)
        sources[name] = "".join(lines)
    q = sources["_queue.py"]
    q = replace_once(
        q,
        "    polling_interval = queue._polling_interval_sec\n",
        '    polling_interval = queue._polling_interval_sec\n    notified_sqlite = signals.enabled and dbos._sys_db.engine.dialect.name == "sqlite"\n    retry = ClaimRetry()\n    if notified_sqlite:\n        signals.notify(queue.name)  # Scan persisted work once at startup.\n',
    )
    q = replace_once(
        q,
        "    while not stop_event.is_set():\n        # Reload database-backed queue config once per iteration",
        "    while not stop_event.is_set():\n        if notified_sqlite:\n            interval = (queue._polling_interval_sec if queue._limiter is not None or queue._partition_limiter is not None else RECOVERY_SECONDS)\n            if retry.delay is not None:\n                if stop_event.wait(retry.delay * random.uniform(0.95, 1.05)):\n                    return\n            elif signals.wait(queue.name, stop_event, interval):\n                return\n        # Reload AFTER the wait so a capacity-change wake uses the new limit.\n        # Reload database-backed queue config once per iteration",
    )
    q = replace_once(
        q,
        "        if stop_event.wait(timeout=polling_interval * random.uniform(0.95, 1.05)):",
        "        if not notified_sqlite and stop_event.wait(timeout=polling_interval * random.uniform(0.95, 1.05)):",
    )
    q = replace_once(
        q,
        "                latest = queue\n",
        "                if notified_sqlite:\n                    retry.failed()\n                    signals.failed(queue.name)\n                    continue\n                latest = queue\n",
    )
    q = replace_once(
        q,
        "        except OperationalError as e:\n            if isinstance(e.orig, errors.LockNotAvailable):",
        "        except OperationalError as e:\n            if notified_sqlite:\n                retry.failed()\n                signals.failed(queue.name)\n            if isinstance(e.orig, errors.LockNotAvailable):",
    )
    q = replace_once(
        q,
        "        except Exception as e:\n            if not stop_event.is_set():",
        "        except Exception as e:\n            if notified_sqlite:\n                retry.failed()\n                signals.failed(queue.name)\n            if not stop_event.is_set():",
    )
    q = replace_once(
        q,
        "        # Attempt to scale back the polling interval on each iteration",
        "        else:\n            retry.succeeded()\n\n        # Attempt to scale back the polling interval on each iteration",
    )
    sources["_queue.py"] = q
    c = sources["_core.py"]
    c = replace_once(
        c,
        "            if not buckets:\n                del self._m[key]",
        "            if not buckets:\n                del self._m[key]\n        if bucket is not None:\n            signals.notify(bucket[0])",
    )
    sources["_core.py"] = c
    s = sources["_sys_db.py"]
    for returned in ["wf_status, should_execute", "result"]:
        s = replace_once(
            s,
            "        DebugTriggers.debug_trigger_point(DebugTriggers.DEBUG_TRIGGER_INITWF_COMMIT)\n        return "
            + returned,
            '        DebugTriggers.debug_trigger_point(DebugTriggers.DEBUG_TRIGGER_INITWF_COMMIT)\n        signals.notify(status["queue_name"])\n        return '
            + returned,
        )
    s = replace_once(
        s,
        "        return inserted\n\n    def _apply_caller_schema",
        '        for status in statuses:\n            signals.notify(status["queue_name"])\n        return inserted\n\n    def _apply_caller_schema',
    )
    start = s.index("    def transition_delayed_workflows(self)")
    end = s.index("    def cancel_timed_out_workflows", start)
    old = s[start:end]
    new = replace_once(
        old, "            c.execute(", "            changed = c.execute("
    )
    new = replace_once(
        new,
        "                )\n            )\n",
        "                )\n                .returning(SystemSchema.workflow_status.c.queue_name)\n            ).scalars().all()\n        for queue_name in set(changed):\n            signals.notify(queue_name)\n",
    )
    original_body = old[old.index("        now_ms =") :]
    new_body = new[new.index("        now_ms =") :]
    header = old[: old.index("        now_ms =")]
    new = (
        header
        + '        if not signals.enabled or self.engine.dialect.name != "sqlite":\n'
        + "".join(
            "    " + line if line.strip() else line
            for line in original_body.splitlines(keepends=True)
        )
        + "            return\n"
        + new_body
    )
    s = s[:start] + new + s[end:]
    s = replace_once(
        s,
        "        if not cancel_children:\n",
        "            signals.notify_all()\n\n        if not cancel_children:\n",
    )
    marker = "    @db_retry()\n    def set_workflow_delay("
    s = replace_once(
        s,
        marker,
        "        signals.notify(queue_name if queue_name is not None else INTERNAL_QUEUE_NAME)\n\n"
        + marker,
    )
    for following in ["update_queue", "upsert_queue"]:
        marker = "    def " + following + "("
        s = replace_once(s, marker, "        signals.notify(name)\n\n" + marker)
    s = replace_once(
        s,
        "            return not existed\n",
        "        signals.notify(name)\n        return not existed\n",
    )
    sources["_sys_db.py"] = s
    for name, content in sources.items():
        ast.parse(content, filename=name)
    for name, content in sources.items():
        (root / name).write_text(content)
        print(name, hashlib.sha256(content.encode()).hexdigest())
    adapter = (
        Path(__file__).resolve().parents[1] / "app/runtime/engine/dbos_notifications.py"
    )
    manifest = {
        "patch_version": 2,
        "dbos_version": "3.3.0",
        "original_files": EXPECTED,
        "files": {
            name: hashlib.sha256(content.encode()).hexdigest()
            for name, content in sources.items()
        },
        "adapter_sha256": hashlib.sha256(adapter.read_bytes()).hexdigest(),
    }
    # Written last: interruption cannot leave an activatable partial patch.
    (root / "printstash-notifications.json").write_text(
        json.dumps(manifest, indent=2) + "\n"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sdk_directory", type=Path)
    patch(parser.parse_args().sdk_directory)
