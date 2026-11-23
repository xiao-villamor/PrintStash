"""Run PrintStash acceptance scenarios inside the pinned disposable Farm Lab."""

import json
import sys
from pathlib import Path

import printstash_http as lab


def ingest(app, name, contents):
    from native_devices import multipart

    raw, mime = multipart({"model_name": name}, "file", name, contents)
    receipt = app.post("/ingest/orca", raw=raw, headers={"Content-Type": mime})
    job = lab.wait_for(
        lambda: app.get(f"/jobs/{receipt['job_id']}"),
        lambda row: row["state"] in {"completed", "failed", "cancelled"},
        90,
        f"{name} ingest",
        lambda row: row["state"],
    )
    lab.require(
        job["state"] == "completed" and job.get("file_id"),
        f"Ingest ended in {job['state']}",
    )
    return job["file_id"]


def main():
    mode, *kinds = sys.argv[1:]
    if mode == "_mixed":
        from farm_routing import observe

        return observe()
    if mode == "_profiles":
        from farm_printers import observe

        return observe(kinds)

    # The upstream CLI owns signal handling, provisioning, CA trust and cleanup.
    # Its Bambu CA setup rewrites Compose, so apply extra observer mounts on each save.
    project = lab.compose_project
    here = Path(__file__).resolve().parent

    def observer_project(image, printers, directory):
        config, app, save_project = project(image, printers, directory)

        def save():
            save_project()
            data = json.loads(config.read_text())
            observer = data["services"]["observer"]
            for script in ("farm_observer.py", "farm_printers.py", "farm_routing.py"):
                observer["volumes"].append(lab.bind(here / script, "/" + script))
            if any(name.startswith("prusa") for name in printers):
                fixture = (
                    here.parents[2] / "backend/tests/fixtures/bgcode/prusaslicer.bgcode"
                )
                observer["volumes"].append(lab.bind(fixture, "/prusaslicer.bgcode"))
            observer["entrypoint"] = ["python", "/farm_observer.py", "_" + mode]
            config.write_text(json.dumps(data))

        save()
        return config, app, save

    lab.compose_project = observer_project
    sys.argv[1:] = kinds
    return lab.main()


if __name__ == "__main__":
    sys.exit(main())
