"""Drive Chromium on the host against the bundled app and isolated native printers."""

import json
import os
import re
import secrets
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

import printstash_http as lab
from farm_observer import ingest

HERE = Path(__file__).resolve().parent
FIXTURE = Path("/browser-session.json")


def seed(kinds):
    from devices import load_pool
    from kinds import KINDS

    app = lab.App()
    app.setup()
    devices = load_pool("/test-pool.json")
    printers = []
    revisions = {}
    for kind in kinds:
        device = devices[kind + "-spare-1"]
        lab.wait_for(
            device.status, lambda s: s["state"] == "ready", 90, kind, lab.native_summary
        )
        travel_x = None
        if device.backend == "klipper":
            travel_x = device.call("/printer/objects/query?toolhead")["status"][
                "toolhead"
            ]["axis_maximum"][0]
        if device.bambu:
            device.material(
                {
                    "units": [[{"material": "PETG", "color": "#008080"}, {}, {}, {}]],
                    "external": {},
                    "active": 0,
                }
            )
        model = KINDS[device.kind]
        printer = app.post(
            "/printers",
            {
                "name": "Browser " + kind,
                "model_name": model,
                **lab.registration(device, app),
            },
        )
        name = (
            "browser-prusa.bgcode" if device.backend == "prusalink" else kind + ".gcode"
        )
        contents = (
            Path("/prusaslicer.bgcode").read_bytes()
            if name.endswith(".bgcode")
            else f"; {kind}\nG28\nG1 X10 Y10 F1200\nG4 P1000\nM400\n".encode()
        )
        if name not in revisions:
            revisions[name] = ingest(app, name, contents)
        file_id = revisions[name]
        printers.append(
            {
                "kind": kind,
                "name": printer["name"],
                "id": printer["id"],
                "fileId": file_id,
                "travelX": travel_x,
                "revisionName": name,
                "canList": printer["capabilities"]["can_list_files"],
            }
        )
    FIXTURE.write_text(
        json.dumps(
            {
                "token": app.headers["Authorization"].removeprefix("Bearer "),
                "user": app.get("/auth/me"),
                "printers": printers,
            }
        )
    )


def verify():
    from devices import load_pool

    devices = load_pool("/test-pool.json")
    session = json.loads(FIXTURE.read_text())
    app = lab.App()
    app.headers["Authorization"] = "Bearer " + session["token"]
    failed = False
    for printer in session["printers"]:
        kind = printer["kind"]
        device = devices[kind + "-spare-1"]
        history = app.get(f"/printers/{printer['id']}/jobs")
        files = [lab.basename(row["path"]) for row in device.files()]
        live = device.status()
        ok = (
            len(history) == 1
            and history[0]["state"] == "completed"
            and history[0]["started_at"] is None
            and history[0]["file_id"] == printer["fileId"]
            and lab.basename(history[0]["remote_filename"]) in files
            and live["state"] == "ready"
        )
        print(
            f"browser native {kind} {'PASS' if ok else 'FAIL'}: app jobs "
            f"{[(r['state'], r['remote_filename'], r['started_at']) for r in history]}, "
            f"native {lab.native_summary(live)}, files {files}",
            flush=True,
        )
        failed |= not ok
    return int(failed)


def run(kinds):
    docker = lab.docker_command()
    project = "farm-browser-" + secrets.token_hex(5)
    with tempfile.TemporaryDirectory(prefix=project) as tmp:
        directory = Path(tmp)
        config, app, save = lab.compose_project(
            os.environ["FARM_TEST_PRINTSTASH_IMAGE"], kinds, directory
        )
        compose = [
            *docker,
            "compose",
            "--project-directory",
            str(lab.LAB),
            "--env-file",
            "/dev/null",
            "-p",
            project,
            "-f",
            str(config),
        ]

        def command(*args, **kwargs):
            return subprocess.run([*compose, *args], check=True, **kwargs)

        try:
            command("up", "-d", "--build", *(k + "-spare-1" for k in kinds))
            if "bambu" in kinds:

                def read_ca():
                    return subprocess.run(
                        [
                            *compose,
                            "exec",
                            "-T",
                            "bambu-spare-1",
                            "cat",
                            "/etc/ssl/certs/ca-certificates.crt",
                            "/data/tls/ca.pem",
                        ],
                        check=False,
                        capture_output=True,
                    )

                ca = directory / "ca.pem"
                ca.write_bytes(
                    lab.wait_for(
                        read_ca,
                        lambda r: r.returncode == 0,
                        45,
                        "Bambu CA",
                        lambda r: r.returncode,
                    ).stdout
                )
                target = "/run/farm-test/ca.pem"
                ca.chmod(0o444)
                app["volumes"].append(lab.bind(ca, target))
                app["environment"].update(
                    SSL_CERT_FILE=target, REQUESTS_CA_BUNDLE=target
                )
                save()
            data = json.loads(config.read_text())
            observer = data["services"]["observer"]
            observer["user"] = f"{os.getuid()}:{os.getgid()}"
            session = directory / "session.json"
            session.touch(mode=0o600)
            observer["volumes"].append(
                {**lab.bind(session, str(FIXTURE)), "read_only": False}
            )
            for script in ("farm_browser.py", "farm_observer.py"):
                observer["volumes"].append(lab.bind(HERE / script, "/" + script))
            observer["volumes"].append(
                lab.bind(
                    HERE.parents[2]
                    / "backend/tests/fixtures/bgcode/prusaslicer.bgcode",
                    "/prusaslicer.bgcode",
                )
            )
            observer["entrypoint"] = ["python", "/farm_browser.py"]
            config.write_text(json.dumps(data))
            command("build", "observer")
            command("up", "-d", "printstash")
            address = command(
                "port", "printstash", "3000", capture_output=True, text=True
            ).stdout.strip()
            lab.require(
                re.fullmatch(r"127\.0\.0\.1:\d+", address),
                "Expected disposable loopback port",
            )
            command("run", "--rm", "-T", "--no-deps", "observer", "_seed", *kinds)
            result = subprocess.run(
                ["pnpm", "run", "test:e2e:farm"],
                check=False,
                cwd=HERE.parents[2] / "frontend",
                env={
                    **os.environ,
                    "FARM_BROWSER_URL": "http://" + address,
                    "FARM_BROWSER_SESSION": str(session),
                    "FARM_BROWSER_OUTPUT": str(directory / "results"),
                },
            )
            native = subprocess.run(
                [
                    *compose,
                    "run",
                    "--rm",
                    "-T",
                    "--no-deps",
                    "observer",
                    "_verify",
                    *kinds,
                ],
                check=False,
            )
            return int(bool(result.returncode or native.returncode))
        finally:
            command(
                "down",
                "--timeout",
                "5",
                "--volumes",
                "--remove-orphans",
                "--rmi",
                "local",
            )


if __name__ == "__main__":
    if sys.argv[1] == "_seed":
        seed(sys.argv[2:])
    elif sys.argv[1] == "_verify":
        sys.exit(verify())
    else:

        def interrupt(signum, frame):
            raise KeyboardInterrupt

        signal.signal(signal.SIGTERM, interrupt)
        sys.exit(run(sys.argv[1:]))
