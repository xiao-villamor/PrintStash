"""Individually selectable printer checks. Only Farm Lab disposable devices are used."""

from pathlib import Path

import printstash_http as lab
from farm_observer import ingest


def upload(app, path, name, contents):
    file_id = ingest(app, name, contents)
    job = app.post(
        path + "/send",
        {"file_id": file_id, "remote_filename": name, "start_print": False},
    )
    lab.require(job["started_at"] is None, f"Unexpected print start: {job}")
    return job


def moonraker(app, path, device):
    def native_head():
        return device.call("/printer/objects/query?toolhead")["status"]["toolhead"]

    head = native_head()
    limit = head["axis_maximum"][:3]
    expected = {
        "klipper": [235, 235, 250],
        "elegoo_neptune4": [225, 225, 265],
        "elegoo_neptune4_plus": [320, 320, 385],
    }[device.kind]
    lab.require(limit == expected, f"Native travel limits {limit}, expected {expected}")
    config = app.get(path + "/config")["klipper_config"]["configfile"]["settings"]
    displayed = [float(config["stepper_" + axis]["position_max"]) for axis in "xyz"]
    lab.require(
        displayed == limit, f"Config travel limits {displayed}, native toolhead {limit}"
    )
    app.post(path + "/home", {"axes": "x"})
    homed = lab.wait_for(
        native_head, lambda h: "x" in h["homed_axes"], 30, "native X home"
    )
    target = homed["position"][0] + 1
    app.post(path + "/jog", {"axis": "x", "distance": 1})
    lab.wait_for(
        native_head, lambda h: abs(h["position"][0] - target) < 0.01, 20, "native X jog"
    )
    lab.wait_for(
        lambda: app.get(path + "/status")["snapshot"].get("toolhead", {}),
        lambda h: (
            "x" in h.get("homed_axes", "") and abs(h["position"][0] - target) < 0.01
        ),
        25,
        f"PrintStash jog readback, native X={target}",
    )
    return f"XYZ travel limits {displayed} mm, native and app X reached {target} mm"


def bambu(app, path, device):
    name = "bambu-ftps.gcode"
    contents = b"G90\nG28\nM400\n"
    upload(app, path, name, contents)

    def files():
        return [lab.basename(item["path"]) for item in device.files()]

    lab.require(name in files(), f"FTPS upload missing: native files {files()}")
    rows = app.post(path + "/files/sync")
    item = next((r for r in rows if lab.basename(r["remote_filename"]) == name), None)
    lab.require(item and item["size_bytes"] == len(contents), f"FTPS inventory: {rows}")
    try:
        app.call("DELETE", path + f"/files/{item['id']}")
    except lab.Failure as error:
        lab.require(
            "HTTP 409 (operation_not_supported_for_provider)" in str(error), str(error)
        )
    else:
        raise lab.Failure("Bambu accepted deletion despite its read-only inventory")
    lab.require(name in files(), f"Rejected delete changed native files: {files()}")
    external = {"material": "PLA", "color": "#CC5500"}
    tray = {"material": "PETG", "color": "#008080"}
    for inserted in (True, False):
        desired = {
            "units": [[tray if inserted else {}, {}, {}, {}]],
            "external": external,
            "active": 0 if inserted else 254,
        }
        device.material(desired)
        lab.wait_for(
            device.status,
            lambda live, desired=desired: live.get("material") == desired,
            15,
            "native AMS change",
        )

        def matches(slots, inserted=inserted):
            by_key = {s["slot_key"]: s for s in slots}
            spool = by_key.get("external", {})
            slot = by_key.get("ams:0:0", {})
            return (
                spool.get("material_type") == "PLA"
                and spool.get("color_hex") == external["color"]
                and slot.get("state") == ("loaded" if inserted else "empty")
                and slot.get("material_type") == ("PETG" if inserted else None)
            )

        lab.wait_for(
            lambda: app.get(path + "/material-state")["slots"],
            matches,
            25,
            f"MQTT material readback for {desired}",
        )
    return "FTPS list, byte count and rejected deletion, MQTT tray insert/remove with external spool retained"


def octoprint(app, path, device):
    name = "octoprint-external-pause.gcode"
    file = upload(app, path, name, b"G90\nG28\n" + b"G4 P2000\n" * 35)
    job = app.post(
        path + "/start",
        {"remote_filename": file["remote_filename"], "file_id": file["file_id"]},
    )
    lab.wait_for(
        device.status,
        lambda s: s["state"] == "printing",
        30,
        "OctoPrint print start",
        lab.native_summary,
    )
    # Simulate the operator pausing in OctoPrint while watching PrintStash.
    device.control("pause", name)
    flags = lab.wait_for(
        lambda: device.call("/api/printer")["state"]["flags"],
        lambda f: f["paused"] and f["printing"],
        30,
        "OctoPrint paused flags",
    )

    def current():
        state = app.get(path + "/status")["printer"]["status"]
        history = next(
            (r["state"] for r in app.get(path + "/jobs") if r["id"] == job["id"]), None
        )
        return {"status": state, "history": history}

    lab.wait_for(
        current,
        lambda state: state == {"status": "paused", "history": "paused"},
        20,
        f"External OctoPrint pause, native paused={flags['paused']}, printing={flags['printing']}",
    )
    return "external VIRTUAL pause reflected in both app state and history"


def prusalink(app, path, device):
    model = device.call("/api/version")["printer"]
    expected = {
        "prusalink": "MK4",
        "prusa_mini": "2.1.0",
        "prusa_mk4s": "1.4.1",
        "prusa_core_one": "7.1.0",
    }
    lab.require(
        model == expected[device.kind], f"Unexpected native printer model {model}"
    )
    contents = Path("/prusaslicer.bgcode").read_bytes()
    name = "prusa-binary.bgcode"
    upload(app, path, name, contents)
    native = device.call("/api/v1/files/usb/" + name)
    lab.require(
        native["size"] == len(contents) and device.status()["state"] == "ready",
        f"Native USB file {native}, device {lab.native_summary(device.status())}",
    )
    rows = app.post(path + "/files/sync")
    item = next((r for r in rows if lab.basename(r["remote_filename"]) == name), None)
    lab.require(
        item and item["size_bytes"] == len(contents),
        f"PrintStash inventory {rows}, native USB {native}",
    )
    app.call("DELETE", path + f"/files/{item['id']}")
    lab.require(
        name not in [row["path"] for row in device.files()],
        "Deleted binary still on native USB",
    )
    return f"native model {model}, Digest login, binary USB file {len(contents)} bytes, delete"


def centauri(app, path, device):
    # The only heater commands are after the 1 MiB chunk boundary.
    contents = (
        b";0123456789abcdef\n" * 62000
        + b"G28\nM104 S57\nM140 S34\nG4 P30000\nM104 S0\nM140 S0\n"
    )
    name = "centauri-chunked.gcode"
    file = upload(app, path, name, contents)
    lab.require(
        name in [lab.basename(r["path"]) for r in device.files()]
        and device.status()["state"] == "ready",
        f"Upload response {file}; native {lab.native_summary(device.status())}",
    )
    app.post(
        path + "/start",
        {"remote_filename": file["remote_filename"], "file_id": file["file_id"]},
    )
    native = lab.wait_for(
        device.status,
        lambda s: (
            s["state"] == "printing"
            and lab.basename(s["file"]) == name
            and s["nozzle"][1] == 57
            and s["bed"][1] == 34
        ),
        30,
        "native final upload chunk",
        lab.native_summary,
    )
    lab.wait_for(
        lambda: app.get(path + "/status")["snapshot"],
        lambda s: (
            s.get("extruder", {}).get("target") == 57
            and s.get("heater_bed", {}).get("target") == 34
        ),
        20,
        f"App heater targets, native {lab.native_summary(native)}",
        lambda s: (s.get("extruder"), s.get("heater_bed")),
    )
    app.post(path + "/cancel")
    lab.wait_for(
        device.status,
        lambda s: s["state"] == "cancelled",
        25,
        "native stop",
        lab.native_summary,
    )
    return f"native storage, {len(contents)}-byte transfer, last chunk executed, cancellation"


CHECKS = {
    "klipper": moonraker,
    "bambu": bambu,
    "octoprint": octoprint,
    "prusalink": prusalink,
    "elegoo_centauri": centauri,
    "elegoo_centauri_2": centauri,
}


def observe(kinds):
    from devices import DeviceError, load_pool

    devices = load_pool("/test-pool.json")
    app = lab.App()
    app.setup()
    failures = 0
    for kind in kinds:
        device = devices[kind + "-spare-1"]
        try:
            lab.wait_for(
                device.status,
                lambda s: s["state"] == "ready",
                90,
                "native ready",
                lab.native_summary,
            )
            fields = lab.registration(device, app)
            if device.backend == "prusalink":
                fields["prusalink_auth_mode"] = "digest"
                fields["prusalink_username"] = device.connection()["digest_username"]
                fields["prusalink_password"] = fields.pop("prusalink_api_key")
            printer = app.post("/printers", {"name": device.config["name"], **fields})
            path = f"/printers/{printer['id']}"
            lab.wait_for(
                lambda path=path: app.get(path + "/status")["printer"]["status"],
                lambda s: s == "ready",
                45,
                "PrintStash ready",
            )
            result = CHECKS[device.backend](app, path, device)
            print(f"printer {kind} PASS: {result}", flush=True)
        except (
            lab.Failure,
            DeviceError,
            OSError,
            KeyError,
            ValueError,
            TypeError,
        ) as error:
            failures += 1
            print(
                f"printer {kind} FAIL: {app.redact(str(error))}; native {lab.native_summary(device.status())}",
                flush=True,
            )
    return int(bool(failures))
