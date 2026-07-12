"""Fill a PrintStash database with demo data so the UI has something to show.

Dev only — never point this at a real library. Run it inside the API container
(it uses the app's own models, so the schema always matches):

    docker cp backend/scripts/seed_demo.py printstash-api:/tmp/seed_demo.py
    docker exec printstash-api python /tmp/seed_demo.py          # add demo data
    docker exec printstash-api python /tmp/seed_demo.py --wipe   # remove it again

Everything it creates is tagged in the DB by name prefix ``[demo]``-free but
recorded in ``_DEMO_MARKER`` collections/models, so ``--wipe`` can find it.
"""

from __future__ import annotations

import random
import struct
import sys
import zlib
from datetime import timedelta
from pathlib import Path

from sqlmodel import Session, delete, select

from app.core.config import settings
from app.core.time import utcnow
from app.db.models import (
    Collection,
    File,
    FileRevisionStatus,
    FileType,
    FilamentProfile,
    Model,
    ModelTagLink,
    PrintJob,
    PrintJobState,
    Printer,
    PrinterProfile,
    PrinterProvider,
    PrinterStatus,
    Tag,
)
from app.db.session import _engine

# Marker written into every seeded row's notes/description so --wipe is exact.
_DEMO_MARKER = "seeded-demo-data"

rng = random.Random(1312)

COLLECTIONS = [
    ("Functional", None),
    ("Brackets", "Functional"),
    ("Enclosure", "Functional"),
    ("Minis", None),
    ("Prototypes", None),
]

TAGS = [
    "needs-supports",
    "petg",
    "abs",
    "print-in-place",
    "remix",
    "tested",
    "gift",
    "spare-part",
]

MODELS = [
    ("Voron Gantry Backer", "Brackets", ["spare-part", "abs"]),
    ("Filament Runout Sensor Mount", "Brackets", ["petg", "tested"]),
    ("Camera Arm v3", "Brackets", ["needs-supports"]),
    ("Spool Holder (bearing)", "Functional", ["print-in-place", "tested"]),
    ("Cable Chain Link", "Functional", ["spare-part"]),
    ("Enclosure Door Hinge", "Enclosure", ["abs", "tested"]),
    ("Enclosure Vent Grill", "Enclosure", ["petg"]),
    ("Desk Cable Comb", "Functional", ["gift"]),
    ("Hex Bit Organiser", "Functional", ["tested"]),
    ("Benchy (calibration)", "Prototypes", ["tested"]),
    ("Voron Cube", "Prototypes", []),
    ("Tolerance Test Rig", "Prototypes", ["remix"]),
    ("Dragon Miniature", "Minis", ["needs-supports", "gift"]),
    ("Tavern Table Set", "Minis", ["gift"]),
    ("Goblin Warband (x6)", "Minis", ["needs-supports"]),
    ("Raspberry Pi 5 Case", "Functional", ["petg", "tested"]),
    ("PSU Bracket 24V", "Enclosure", ["abs"]),
    ("Toolhead Umbilical Clip", "Brackets", ["spare-part", "tested"]),
]

PRINTERS = [
    ("Voron 2.4", "Voron 2.4", PrinterProvider.MOONRAKER, PrinterStatus.PRINTING, "Workshop"),
    ("Ender KE", "Creality Ender 3 V3 KE", PrinterProvider.MOONRAKER, PrinterStatus.READY, "Workshop"),
    ("MK4S", "Prusa MK4S", PrinterProvider.PRUSALINK, PrinterStatus.READY, "Office"),
    ("Carbon", "Elegoo Centauri Carbon", PrinterProvider.ELEGOO_CENTAURI, PrinterStatus.OFFLINE, "Office"),
    ("P1S", "Bambu Lab P1S", PrinterProvider.BAMBU_LAN, PrinterStatus.READY, "Workshop"),
]

FILAMENTS = [
    ("Prusament PLA Galaxy Black", "PLA", "Prusa", 29.99, 1.24),
    ("Polymaker PolyLite PETG Orange", "PETG", "Polymaker", 22.50, 1.27),
    ("Bambu ABS Blue", "ABS", "Bambu Lab", 24.99, 1.06),
    ("eSun PLA+ White", "PLA", "eSun", 18.99, 1.24),
    ("Fiberlogy ASA Grey", "ASA", "Fiberlogy", 31.00, 1.07),
    ("Sunlu Silk PLA Copper", "PLA", "Sunlu", 16.50, 1.24),
]

PRINTER_PROFILES = [
    ("Voron 0.4 draft", "Voron 2.4", "OrcaSlicer", 0.4),
    ("Voron 0.6 structural", "Voron 2.4", "OrcaSlicer", 0.6),
    ("MK4S 0.4 quality", "Prusa MK4S", "PrusaSlicer", 0.4),
    ("KE 0.4 speed", "Creality Ender 3 V3 KE", "OrcaSlicer", 0.4),
]


def _png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    """A solid-colour PNG, hand-rolled so the seeder needs no image library."""
    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def wipe(session: Session) -> None:
    models = session.exec(select(Model).where(Model.description == _DEMO_MARKER)).all()
    ids = [m.id for m in models]
    if ids:
        session.exec(delete(PrintJob).where(PrintJob.model_id.in_(ids)))
        session.exec(delete(ModelTagLink).where(ModelTagLink.model_id.in_(ids)))
        session.exec(delete(File).where(File.model_id.in_(ids)))
        for m in models:
            session.delete(m)
    for table, field in ((Printer, "notes"), (FilamentProfile, "notes"), (PrinterProfile, "notes")):
        for row in session.exec(select(table).where(getattr(table, field) == _DEMO_MARKER)).all():
            session.delete(row)
    for c in session.exec(select(Collection).where(Collection.readme == _DEMO_MARKER)).all():
        session.delete(c)
    for t in session.exec(select(Tag).where(Tag.name.in_(TAGS))).all():
        session.delete(t)
    session.commit()
    print(f"wiped {len(ids)} demo models and their jobs, printers, profiles, tags")


def seed(session: Session) -> None:
    now = utcnow()
    thumb_dir = Path(settings.thumb_dir)
    data_dir = Path(settings.data_dir)
    thumb_dir.mkdir(parents=True, exist_ok=True)

    collections: dict[str, Collection] = {}
    for name, parent in COLLECTIONS:
        slug = name.lower().replace(" ", "-")
        path = slug if parent is None else f"{collections[parent].path}/{slug}"
        c = Collection(
            name=name,
            slug=slug,
            path=path,
            parent_id=collections[parent].id if parent else None,
            readme=_DEMO_MARKER,
        )
        session.add(c)
        session.commit()
        session.refresh(c)
        collections[name] = c

    tags = {}
    for name in TAGS:
        tag = Tag(name=name, slug=name)
        session.add(tag)
        session.commit()
        session.refresh(tag)
        tags[name] = tag

    printers = []
    for name, model_name, provider, status, group in PRINTERS:
        p = Printer(
            name=name,
            model_name=model_name,
            detected_model=model_name,
            provider=provider,
            moonraker_url="http://demo.invalid" if provider == PrinterProvider.MOONRAKER else "",
            prusalink_url="http://demo.invalid" if provider == PrinterProvider.PRUSALINK else None,
            elegoo_centauri_host="10.0.0.9" if provider == PrinterProvider.ELEGOO_CENTAURI else None,
            bambu_host="10.0.0.8" if provider == PrinterProvider.BAMBU_LAN else None,
            bambu_serial="00M00A000000000" if provider == PrinterProvider.BAMBU_LAN else None,
            status=status,
            group=group,
            notes=_DEMO_MARKER,
            last_seen_at=now - timedelta(minutes=rng.randint(1, 90)),
        )
        session.add(p)
        printers.append(p)

    for name, material, brand, cost, density in FILAMENTS:
        session.add(
            FilamentProfile(
                name=name,
                material_type=material,
                material_brand=brand,
                cost_per_kg=cost,
                density_g_cm3=density,
                diameter_mm=1.75,
                notes=_DEMO_MARKER,
            )
        )
    for name, printer_model, slicer, nozzle in PRINTER_PROFILES:
        session.add(
            PrinterProfile(
                name=name,
                printer_model=printer_model,
                slicer_name=slicer,
                nozzle_diameter_mm=nozzle,
                notes=_DEMO_MARKER,
            )
        )
    session.commit()
    for p in printers:
        session.refresh(p)

    models: list[tuple[Model, list[File]]] = []
    for name, collection, tag_names in MODELS:
        slug = name.lower().replace(" ", "-").replace("(", "").replace(")", "")
        created = now - timedelta(days=rng.randint(3, 300), hours=rng.randint(0, 23))
        m = Model(
            name=name,
            slug=slug,
            hash=f"{rng.getrandbits(256):064x}",
            collection_id=collections[collection].id,
            description=_DEMO_MARKER,
            source_url=rng.choice(
                ["https://www.printables.com/model/000000", "https://makerworld.com/en/models/0", None]
            ),
            created_at=created,
            updated_at=created,
        )
        session.add(m)
        session.commit()
        session.refresh(m)

        files: list[File] = []
        mesh = File(
            model_id=m.id,
            path=str(data_dir / slug / "v1" / f"{name}.stl"),
            original_filename=f"{name}.stl",
            file_type=FileType.STL,
            version=1,
            size_bytes=rng.randint(120_000, 9_000_000),
            sha256=f"{rng.getrandbits(256):064x}",
            uploaded_at=created,
        )
        session.add(mesh)
        files.append(mesh)

        for version in range(1, rng.randint(2, 4)):
            status = rng.choice(
                [
                    FileRevisionStatus.KNOWN_GOOD,
                    FileRevisionStatus.KNOWN_GOOD,
                    FileRevisionStatus.NEEDS_TEST,
                    FileRevisionStatus.FAILED,
                ]
            )
            g = File(
                model_id=m.id,
                path=str(data_dir / slug / f"v{version}" / f"{name} v{version}.gcode"),
                original_filename=f"{name} v{version}.gcode",
                file_type=FileType.GCODE,
                version=version,
                size_bytes=rng.randint(400_000, 30_000_000),
                sha256=f"{rng.getrandbits(256):064x}",
                revision_label=rng.choice(["0.4 draft", "0.4 quality", "0.6 structural", "0.2 detail"]),
                revision_status=status,
                revision_notes=rng.choice(
                    ["Warped at the corners.", "Great surface finish.", "Needs a brim.", None]
                ),
                is_recommended=status == FileRevisionStatus.KNOWN_GOOD,
                uploaded_at=created + timedelta(days=version),
            )
            session.add(g)
            files.append(g)
        session.commit()

        for f in files:
            session.refresh(f)
        # Thumbnails: the API falls back to a legacy PNG at <thumb_dir>/<file_id>.png.
        hue = (rng.randint(40, 220), rng.randint(40, 220), rng.randint(40, 220))
        (thumb_dir / f"{mesh.id}.png").write_bytes(_png(320, 320, hue))
        m.thumbnail_file_id = mesh.id
        m.thumbnail_path = str(thumb_dir / f"{mesh.id}.png")
        for tag_name in tag_names:
            session.add(ModelTagLink(model_id=m.id, tag_id=tags[tag_name].id))
        session.add(m)
        models.append((m, files))
    session.commit()

    jobs = 0
    for _ in range(160):
        m, files = rng.choice(models)
        gcodes = [f for f in files if f.file_type == FileType.GCODE]
        if not gcodes:
            continue
        f = rng.choice(gcodes)
        printer = rng.choice(printers)
        started = now - timedelta(days=rng.randint(0, 120), hours=rng.randint(0, 23))
        duration = rng.randint(900, 15 * 3600)
        state = rng.choices(
            [PrintJobState.COMPLETED, PrintJobState.FAILED, PrintJobState.CANCELLED],
            weights=[85, 8, 7],
        )[0]
        grams = round(rng.uniform(6, 240), 1)
        filament = rng.choice(FILAMENTS)
        done = state == PrintJobState.COMPLETED
        session.add(
            PrintJob(
                printer_id=printer.id,
                printer_name=printer.name,
                file_id=f.id,
                model_id=m.id,
                remote_filename=f.original_filename,
                state=state,
                progress=1.0 if done else round(rng.uniform(0.05, 0.9), 2),
                error="Thermal runaway on heater bed" if state == PrintJobState.FAILED else None,
                source=rng.choice(["vault", "vault", "external"]),
                filament_used_g=grams,
                filament_used_mm=round(grams / (filament[4] * 2.405) * 1000, 1),
                filament_g_effective=grams,
                actual_duration_s=duration if done else int(duration * 0.4),
                cost=round(grams / 1000 * filament[3], 2) if done else None,
                spool_name=filament[0],
                started_at=started,
                finished_at=started + timedelta(seconds=duration),
                created_at=started,
                updated_at=started + timedelta(seconds=duration),
            )
        )
        jobs += 1

    # One live print so the dashboard shows an in-progress card.
    live = printers[0]
    m, files = models[0]
    gcode = next(f for f in files if f.file_type == FileType.GCODE)
    session.add(
        PrintJob(
            printer_id=live.id,
            printer_name=live.name,
            file_id=gcode.id,
            model_id=m.id,
            remote_filename=gcode.original_filename,
            state=PrintJobState.PRINTING,
            progress=0.42,
            source="vault",
            started_at=now - timedelta(hours=2, minutes=13),
            created_at=now - timedelta(hours=2, minutes=13),
            updated_at=now,
        )
    )
    session.commit()
    print(
        f"seeded {len(models)} models, {len(collections)} collections, {len(TAGS)} tags, "
        f"{len(printers)} printers, {len(FILAMENTS)} filament profiles, "
        f"{len(PRINTER_PROFILES)} printer profiles, {jobs + 1} print jobs"
    )


if __name__ == "__main__":
    with Session(_engine) as session:
        if "--wipe" in sys.argv:
            wipe(session)
        else:
            wipe(session)  # idempotent: re-seeding replaces the previous demo set
            seed(session)
