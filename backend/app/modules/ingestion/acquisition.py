"""Restart-safe acquisition checkpoints and identity-owned download spools.

A command's SQL envelope stores completed acquisition steps. Downloads retain
an exact staging receipt until source publication or terminal cleanup. Every
network attempt still uses the importer's redirect, SSRF and size protections.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from sqlmodel import select

from app.db.models import BackgroundJob, StagingLease
from app.db.session import SessionFactory
from app.modules.ingestion.commands import require_execution_claim
from app.modules.ingestion.staging_leases import _matching_path, record_job_lease


class AcquisitionJournal:
    def __init__(self, job_id: str, sessions: SessionFactory):
        self.job_id = job_id
        self.sessions = sessions

    def read(self, key: str) -> Any:
        with self.sessions.scoped_session() as session:
            row = session.get(BackgroundJob, self.job_id)
            if row is None:
                raise ValueError("ingestion_job_missing")
            return json.loads(row.payload_json or "{}").get("checkpoints", {}).get(key)

    def save(
        self, key: str, value: Any, *, receipt: tuple[Path, str] | None = None
    ) -> None:
        with self.sessions.scoped_session() as session:
            require_execution_claim(session)
            row = session.exec(
                select(BackgroundJob)
                .where(BackgroundJob.id == self.job_id)
                .with_for_update()
            ).one()
            if receipt is not None:
                path, digest = receipt
                record_job_lease(
                    session,
                    job_id=self.job_id,
                    staged=path,
                    size=path.stat().st_size,
                    sha256=digest,
                    owner_user_id=row.owner_user_id,
                )
            payload = json.loads(row.payload_json or "{}")
            payload.setdefault("checkpoints", {})[key] = value
            row.payload_json = json.dumps(payload, separators=(",", ":"))
            session.add(row)
            session.commit()

    def _restore_download(self, key: str) -> tuple[Path, str] | None:
        saved = self.read(key)
        if not saved:
            return None
        with self.sessions.scoped_session() as session:
            lease = session.exec(
                select(StagingLease).where(
                    StagingLease.background_job_id == self.job_id,
                    StagingLease.path == saved["path"],
                )
            ).first()
            path = _matching_path(lease) if lease else None
            return (path, saved["filename"]) if path else None

    async def download(self, key: str, url: str) -> tuple[Path, str]:
        from app.modules.ingestion.importer import download_to_staging_with_receipt

        restored = await asyncio.to_thread(self._restore_download, key)
        if restored is not None:
            return restored
        path, filename, digest = await download_to_staging_with_receipt(url)
        await asyncio.to_thread(
            self.save,
            key,
            {"path": str(path), "filename": filename},
            receipt=(path, digest),
        )
        return path, filename
