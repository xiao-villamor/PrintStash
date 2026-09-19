"""Dispatch durable commands into ingestion capability owners.

The command store owns acceptance and leases independently of these handlers.
"""

from sqlmodel import select

from app.modules.ingestion.commands import (
    CommandClaim,
    CommandDeferred,
    DependencyPending,
    execution_scope,
    require_execution_claim,
)


async def execute(claim: CommandClaim, sessions) -> None:
    with execution_scope(claim):
        await _execute(claim, sessions)


async def _execute(claim: CommandClaim, sessions) -> None:
    """Dispatch only known commands into the existing capability owners."""
    import asyncio
    from pathlib import Path

    from app.db.models import SUFFIX_TO_FILE_TYPE, File, StagingLease
    from app.modules.ingestion import background, importer
    from app.modules.ingestion.ingestion import ingest_mesh, ingest_orca_gcode
    from app.modules.ingestion.staging_leases import _matching_path
    from app.schemas.ingest import UrlIngestRequest

    args = dict(claim.arguments)
    common = {"job_id": claim.job_id, "session_factory": sessions}
    if claim.command in {
        "artifact",
        "verified_upload",
        "archive_inspection",
        "archive",
    }:
        # Replay after source commit does not require the already-consumed spool.
        with sessions.scoped_session() as session:
            committed = session.exec(
                select(File.id).where(File.ingestion_key == claim.job_id)
            ).first()
            lease = session.exec(
                select(StagingLease).where(
                    StagingLease.background_job_id == claim.job_id
                )
            ).first()
            path = _matching_path(lease) if lease else None
            if path is None and committed is None:
                raise ValueError("staging_identity_unavailable")
            path = path or Path(args.get("staged_path", ""))
        if claim.command == "artifact":
            filename = args.pop("original_filename")
            args.pop("staged_path", None)
            suffix = Path(filename).suffix.lower()
            operation = (
                ingest_orca_gcode
                if suffix in background.GCODE_SUFFIXES
                else ingest_mesh
            )
            if operation is ingest_mesh:
                args["file_type"] = SUFFIX_TO_FILE_TYPE[suffix]
            await asyncio.to_thread(
                operation,
                **common,
                staged_path=path,
                original_filename=filename,
                **args,
            )
        elif claim.command == "verified_upload":
            from app.modules.ingestion.artifact_uploads.handoff import (
                run_verified_upload_ingestion,
            )

            await asyncio.to_thread(
                run_verified_upload_ingestion,
                **common,
                upload_id=args["upload_id"],
                staged_path=path,
            )
        elif claim.command == "archive_inspection":
            await background.inspect_uploaded_archive(
                job_id=claim.job_id, staged=path, **args
            )
        else:
            await asyncio.to_thread(
                importer.import_archive, **common, archive_path=path, **args
            )
            from app.modules.ingestion.staging_leases import release_job_files
            from app.runtime.jobs import registry

            status = registry.get(claim.job_id)
            if status is not None and status.state == "completed" and not status.failed:
                with sessions.scoped_session() as session:
                    require_execution_claim(session)
                    release_job_files(session, claim.job_id)
                    session.commit()
    elif claim.command == "url":
        from app.core.secrets import decrypt_secret

        request = dict(args["request"])
        encrypted = request.pop("thingiverse_cookie_encrypted", None)
        if encrypted:
            request["thingiverse_cookie"] = decrypt_secret(encrypted)
        args["request"] = request
        await background.import_from_url(
            **common,
            req=UrlIngestRequest.model_validate(args["request"]),
            actor_user_id=args["actor_user_id"],
        )
    elif claim.command == "file_selection":
        from app.modules.ingestion.import_resolvers import ModelFile

        args["files"] = [ModelFile(**file) for file in args["files"]]
        await background.run_file_selection_import(**common, **args)
    elif claim.command == "collection_selection":
        from app.modules.ingestion.import_resolvers import CollectionMember

        args["members"] = [CollectionMember(**member) for member in args["members"]]
        await background.run_collection_member_import(**common, **args)
    elif claim.command == "inbox":
        from app.modules.ingestion.inbox import execute_import

        await execute_import(args["item_id"], args["context"], sessions)

    elif claim.command == "capture_enrichment":
        from app.modules.ingestion.inbox import _finish_import
        from app.runtime.jobs import registry

        source = registry.get(args["source_job_id"])
        if source is not None and source.state in {"pending", "running"}:
            raise DependencyPending("capture_source_pending")
        finished = await asyncio.to_thread(
            _finish_import,
            args["item_id"],
            args["source_job_id"],
            sessions,
            enrich=True,
        )
        if not finished:
            raise CommandDeferred("capture_enrichment_pending")
        registry.finish(claim.job_id, state="completed", completion="complete")
