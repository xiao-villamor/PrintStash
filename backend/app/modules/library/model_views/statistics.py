"""Batched library and print statistics with bounded caches."""

from __future__ import annotations

from datetime import datetime, timedelta
from time import monotonic
from typing import Optional

from sqlalchemy import case, func
from sqlmodel import Session, select

from app.core.config import settings
from app.core.time import ensure_utc, utcnow
from app.db.models import (
    SENTINEL_MODEL_HASH,
    Collection,
    File,
    FileType,
    Metadata,
    Model,
    Printer,
    PrintJob,
    PrintJobState,
    Tag,
    User,
)
from app.db.scopes import live
from app.schemas.models import (
    CollectionStatRead,
    FilamentStatRead,
    ModelStatRead,
    PrintDataCoverageRead,
    PrinterFailuresRead,
    PrinterFilamentStatRead,
    PrinterStatRead,
    PrintOutcomesRead,
    PrintStatisticsRead,
    StorageUsageRead,
    TimeBucketRead,
    VaultStatsRead,
)

from .access import _apply_model_access

# ---------------------------------------------------------------------------
# Vault stats
# ---------------------------------------------------------------------------


# ``backend.usage()`` walks the whole storage tree (or lists the bucket). The
# dashboard calls it on every load, where a slightly stale total is fine.
_USAGE_TTL_S = 60.0

_usage_cache: dict[tuple[str, str], tuple[float, dict]] = {}


def _cached_storage_usage(session: Session) -> dict:
    """Storage usage, recomputed at most once per minute per configured backend.

    Keyed on the effective backend + data dir so a runtime reconfiguration (and
    each test's tmp_path) gets its own entry. Failures are not cached: a
    transient S3 error should not pin an error state for a minute.
    """
    key = (str(settings.storage_backend), str(settings.data_dir))
    now = monotonic()
    hit = _usage_cache.get(key)
    if hit is not None and now - hit[0] < _USAGE_TTL_S:
        return hit[1]
    from app.modules.storage.storage_inventory import legacy_usage

    usage = legacy_usage(session)
    _usage_cache[key] = (now, usage)
    return usage


def vault_stats(session: Session, user: User) -> VaultStatsRead:
    live_model_ids = select(Model.id).where(
        live(Model), Model.hash != SENTINEL_MODEL_HASH
    )
    live_model_ids = _apply_model_access(live_model_ids, session, user).cte(
        "vault_models"
    )
    file_stats = (
        select(
            func.count(File.id).label("file_count"),
            func.coalesce(func.sum(File.size_bytes), 0).label("indexed_size"),
            func.coalesce(
                func.sum(case((File.file_type != FileType.GCODE, 1), else_=0)), 0
            ).label("source_file_count"),
            func.coalesce(
                func.sum(case((File.file_type == FileType.GCODE, 1), else_=0)), 0
            ).label("gcode_file_count"),
        )
        .join(live_model_ids, live_model_ids.c.id == File.model_id)
        .where(live(File))
        .cte("vault_file_stats")
    )
    counts = session.execute(
        select(
            select(func.count()).select_from(live_model_ids).scalar_subquery(),
            file_stats.c.file_count,
            file_stats.c.indexed_size,
            file_stats.c.source_file_count,
            file_stats.c.gcode_file_count,
            select(func.count(Collection.id)).where(live(Collection)).scalar_subquery(),
            select(func.count(Tag.id)).where(live(Tag)).scalar_subquery(),
            select(func.count(Printer.id)).where(live(Printer)).scalar_subquery(),
        ).select_from(file_stats)
    ).one()
    (
        model_count,
        file_count,
        indexed_size,
        source_file_count,
        gcode_file_count,
        collection_count,
        tag_count,
        printer_count,
    ) = counts

    try:
        storage_usage = StorageUsageRead(**_cached_storage_usage(session))
    except Exception as exc:
        storage_usage = StorageUsageRead(
            backend=settings.storage_backend,
            ok=False,
            error=exc.__class__.__name__,
        )

    return VaultStatsRead(
        model_count=int(model_count or 0),
        file_count=int(file_count or 0),
        source_file_count=int(source_file_count or 0),
        gcode_file_count=int(gcode_file_count or 0),
        collection_count=int(collection_count or 0),
        tag_count=int(tag_count or 0),
        printer_count=int(printer_count or 0),
        indexed_size_bytes=int(indexed_size or 0),
        storage=storage_usage,
    )


# Supported preset windows → lookback in days; None means "all time".
_STATS_PERIODS: dict[str, Optional[int]] = {
    "7d": 7,
    "30d": 30,
    "90d": 90,
    "1y": 365,
    "all": None,
}


def print_statistics(session: Session, period: str) -> PrintStatisticsRead:
    """Aggregate print outcomes and completed-job costs over a preset time window.

    Reads the cost and effective filament grams that were resolved and
    frozen once, at completion time (see
    ``print_results.resolve_completion_cost``), rather than re-hydrating
    every job row and re-matching a filament profile on every dashboard
    load. A profile's price edited after a print completed does not change
    that print's historical cost.
    """
    if period not in _STATS_PERIODS:
        period = "30d"
    lookback_days = _STATS_PERIODS[period]

    end_at = utcnow()
    start_at: Optional[datetime] = (
        end_at - timedelta(days=lookback_days) if lookback_days is not None else None
    )
    # Manually-logged jobs may not set finished_at; fall back to created_at so
    # they still land in the right window.
    anchor = func.coalesce(PrintJob.finished_at, PrintJob.created_at)
    printer_label = func.coalesce(Printer.name, PrintJob.printer_name)

    query = (
        select(
            PrintJob.cost,
            PrintJob.filament_g_effective,
            PrintJob.filament_used_g,
            PrintJob.actual_duration_s,
            PrintJob.finished_at,
            PrintJob.created_at,
            Model.collection_id,
            Model.id,
            Model.name,
            Model.hash,
            Collection.name,
            Collection.path,
            Printer.id,
            Printer.name,
            printer_label,
            Metadata.material_type,
            Metadata.material_brand,
            Metadata.estimated_time_s,
        )
        .join(File, File.id == PrintJob.file_id)
        .outerjoin(Metadata, Metadata.file_id == File.id)
        .join(Model, Model.id == PrintJob.model_id)
        .outerjoin(Collection, Collection.id == Model.collection_id)
        .outerjoin(Printer, Printer.id == PrintJob.printer_id)
        .where(
            live(PrintJob),
            PrintJob.state == PrintJobState.COMPLETED,
        )
    )
    if start_at is not None:
        query = query.where(anchor >= start_at)  # type: ignore[operator]

    rows = session.exec(query).all()

    terminal = select(PrintJob.state, func.count()).where(
        live(PrintJob),
        PrintJob.state.in_((PrintJobState.FAILED, PrintJobState.CANCELLED)),  # type: ignore[attr-defined]
    )
    if start_at is not None:
        terminal = terminal.where(anchor >= start_at)  # type: ignore[operator]
    terminal_counts = dict(session.exec(terminal.group_by(PrintJob.state)).all())
    failed = terminal_counts.get(PrintJobState.FAILED, 0)

    printer_failures: list[PrinterFailuresRead] = []
    if failed:
        failure_count = func.sum(
            case((PrintJob.state == PrintJobState.FAILED, 1), else_=0)
        )
        completion_count = func.sum(
            case((PrintJob.state == PrintJobState.COMPLETED, 1), else_=0)
        )
        failures = (
            select(PrintJob.printer_id, printer_label, failure_count, completion_count)
            .outerjoin(Printer, Printer.id == PrintJob.printer_id)
            .where(
                live(PrintJob),
                PrintJob.state.in_((PrintJobState.FAILED, PrintJobState.COMPLETED)),  # type: ignore[attr-defined]
            )
            .group_by(PrintJob.printer_id, printer_label)
            .having(failure_count > 0)
            .order_by(
                failure_count.desc(),
                func.coalesce(PrintJob.printer_id, 0),
                printer_label,
            )
            .limit(10)
        )
        if start_at is not None:
            failures = failures.where(anchor >= start_at)  # type: ignore[operator]
        printer_failures = [
            PrinterFailuresRead(
                printer_id=printer_id,
                name=name,
                failed=failure_count,
                completed=completion_count,
            )
            for printer_id, name, failure_count, completion_count in session.exec(
                failures
            )
        ]

    recorded_time = slicer_time = recorded_filament = slicer_filament = known_cost = 0

    total_cost = 0.0
    has_cost = False
    total_filament_g = 0.0
    has_filament = False
    total_duration_s = 0
    grams_samples = 0
    grams_sum = 0.0

    bucket_monthly = lookback_days is None or lookback_days > 90

    collection_acc: dict[Optional[int], dict] = {}
    filament_acc: dict[tuple, dict] = {}
    printer_filament_acc: dict[tuple, dict] = {}
    model_acc: dict[int, dict] = {}
    printer_acc: dict[Optional[int], dict] = {}
    time_acc: dict[str, dict] = {}

    for (
        cost,
        grams,
        measured_grams,
        duration,
        finished_at,
        created_at,
        cid,
        model_id,
        model_name,
        model_hash,
        cname,
        cpath,
        printer_id,
        registered_printer_name,
        printer_name,
        mtype,
        mbrand,
        estimated_time_s,
    ) in rows:
        if duration is not None:
            recorded_time += 1
        elif estimated_time_s is not None:
            slicer_time += 1
            duration = estimated_time_s
        if measured_grams is not None:
            recorded_filament += 1
        elif grams is not None:
            slicer_filament += 1
        if cost is not None:
            known_cost += 1
            total_cost += cost
            has_cost = True
        if grams is not None:
            total_filament_g += grams
            has_filament = True
            grams_sum += grams
            grams_samples += 1
        if duration is not None:
            total_duration_s += duration

        # Top collections (Uncategorized bucket when the model has no collection).
        c = collection_acc.setdefault(
            cid,
            {
                "name": cname if cid is not None else "Uncategorized",
                "path": cpath if cid is not None else None,
                "print_count": 0,
                "total_cost": 0.0,
                "has_cost": False,
            },
        )
        c["print_count"] += 1
        if cost is not None:
            c["total_cost"] += cost
            c["has_cost"] = True

        if model_hash != SENTINEL_MODEL_HASH:
            model_stat = model_acc.setdefault(
                model_id,
                {"name": model_name, "print_count": 0, "total_g": 0.0, "has_g": False},
            )
            model_stat["print_count"] += 1
            if grams is not None:
                model_stat["total_g"] += grams
                model_stat["has_g"] = True

        printer_stat = printer_acc.setdefault(
            printer_id,
            {
                "name": registered_printer_name or "Unassigned / manual",
                "print_count": 0,
                "print_time_s": 0,
            },
        )
        printer_stat["print_count"] += 1
        if duration is not None:
            printer_stat["print_time_s"] += duration

        # Top filaments grouped by (material_type, material_brand).
        f = filament_acc.setdefault(
            (mtype, mbrand),
            {
                "material_type": mtype,
                "material_brand": mbrand,
                "print_count": 0,
                "total_g": 0.0,
                "has_g": False,
                "total_cost": 0.0,
                "has_cost": False,
            },
        )
        f["print_count"] += 1
        if grams is not None:
            f["total_g"] += grams
            f["has_g"] = True
        if cost is not None:
            f["total_cost"] += cost
            f["has_cost"] = True

        if grams is not None:
            usage = printer_filament_acc.setdefault(
                (printer_id, printer_name, mtype, mbrand),
                {
                    "total_g": 0.0,
                    "print_count": 0,
                },
            )
            usage["total_g"] += grams
            usage["print_count"] += 1

        # Cost-over-time buckets keyed by day (≤90d) or month (longer/all).
        when = ensure_utc(finished_at or created_at)
        key = when.strftime("%Y-%m") if bucket_monthly else when.strftime("%Y-%m-%d")
        b = time_acc.setdefault(
            key,
            {
                "cost": 0.0,
                "has_cost": False,
                "filament_g": 0.0,
                "has_g": False,
                "print_count": 0,
            },
        )
        b["print_count"] += 1
        if cost is not None:
            b["cost"] += cost
            b["has_cost"] = True
        if grams is not None:
            b["filament_g"] += grams
            b["has_g"] = True

    top_collections = [
        CollectionStatRead(
            collection_id=cid,
            name=v["name"],
            path=v["path"],
            print_count=v["print_count"],
            total_cost=round(v["total_cost"], 4) if v["has_cost"] else None,
        )
        for cid, v in sorted(
            collection_acc.items(), key=lambda kv: kv[1]["print_count"], reverse=True
        )
    ][:10]

    top_filaments = [
        FilamentStatRead(
            material_type=v["material_type"],
            material_brand=v["material_brand"],
            print_count=v["print_count"],
            total_g=round(v["total_g"], 2) if v["has_g"] else None,
            total_cost=round(v["total_cost"], 4) if v["has_cost"] else None,
        )
        for v in sorted(
            filament_acc.values(), key=lambda x: x["print_count"], reverse=True
        )
    ][:10]

    top_models = [
        ModelStatRead(
            model_id=model_id,
            name=v["name"],
            print_count=v["print_count"],
            total_g=round(v["total_g"], 2) if v["has_g"] else None,
        )
        for model_id, v in sorted(
            model_acc.items(), key=lambda kv: kv[1]["print_count"], reverse=True
        )
    ][:10]

    top_printers = [
        PrinterStatRead(
            printer_id=printer_id,
            name=v["name"],
            print_count=v["print_count"],
            print_time_s=v["print_time_s"],
        )
        for printer_id, v in sorted(
            printer_acc.items(), key=lambda kv: kv[1]["print_time_s"], reverse=True
        )
    ][:10]

    cost_over_time = [
        TimeBucketRead(
            bucket=key,
            cost=round(v["cost"], 4) if v["has_cost"] else None,
            filament_g=round(v["filament_g"], 2) if v["has_g"] else None,
            print_count=v["print_count"],
        )
        for key, v in sorted(time_acc.items())
    ]

    return PrintStatisticsRead(
        period=period,
        start_at=start_at,
        end_at=end_at,
        total_prints=len(rows),
        total_cost=round(total_cost, 4) if has_cost else None,
        total_filament_g=round(total_filament_g, 2) if has_filament else None,
        avg_filament_g=(round(grams_sum / grams_samples, 2) if grams_samples else None),
        total_print_time_s=total_duration_s,
        top_collections=top_collections,
        top_filaments=top_filaments,
        top_models=top_models,
        top_printers=top_printers,
        cost_over_time=cost_over_time,
        outcomes=PrintOutcomesRead(
            failed=failed, cancelled=terminal_counts.get(PrintJobState.CANCELLED, 0)
        ),
        printer_failures=printer_failures,
        printer_filaments=[
            PrinterFilamentStatRead(
                printer_id=printer_id,
                printer_name=printer_name,
                material_type=material,
                material_brand=brand,
                print_count=usage["print_count"],
                total_g=round(usage["total_g"], 2),
            )
            for (printer_id, printer_name, material, brand), usage in sorted(
                printer_filament_acc.items(),
                key=lambda pair: pair[1]["total_g"],
                reverse=True,
            )[:10]
        ],
        data_coverage=PrintDataCoverageRead(
            recorded_time=recorded_time,
            slicer_time=slicer_time,
            recorded_filament=recorded_filament,
            slicer_filament=slicer_filament,
            known_cost=known_cost,
        ),
    )
