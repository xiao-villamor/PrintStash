"""Search settings replies describe accepted writes; partial edits use current state."""

from sqlalchemy import event, text
from sqlmodel import Session

from app.modules.search import configuration
from app.schemas.inference import SearchSettings


class TestUpdate:
    def test_returns_the_accepted_transaction_receipt(
        self, db_session: Session
    ) -> None:
        initial = configuration.update(db_session, SearchSettings())

        def later_write(_session: Session) -> None:
            with Session(db_session.get_bind()) as other:
                other.execute(
                    text(
                        "UPDATE system_config SET ai_search_settings_json=:value WHERE id=1"
                    ),
                    {
                        "value": SearchSettings(
                            timezone="Europe/London"
                        ).model_dump_json()
                    },
                )
                other.commit()

        event.listen(db_session, "after_commit", later_write, once=True)
        try:
            receipt = configuration.update(
                db_session, SearchSettings(timezone="Europe/Madrid"), base=initial
            )
        finally:
            event.remove(db_session, "after_commit", later_write)

        assert receipt.settings.timezone == "Europe/Madrid"
        current = configuration.read(db_session)
        assert current.settings.timezone == "Europe/London"
        assert current.edit_version > receipt.edit_version

    def test_merges_a_patch_against_current_locked_settings(
        self, db_session: Session
    ) -> None:
        configuration.update(db_session, SearchSettings())
        configuration.read(db_session)
        with Session(db_session.get_bind()) as other:
            configuration.update(other, SearchSettings(timezone="Europe/Madrid"))

        receipt = configuration.update(
            db_session, SearchSettings(enabled=True), partial=True
        )

        assert receipt.settings.enabled is True
        assert receipt.settings.timezone == "Europe/Madrid"
        current = configuration.read(db_session)
        assert current == receipt

    def test_schedules_reconciliation_after_the_settings_commit(
        self, db_session, work_engine
    ) -> None:
        from app.db.models import JobKind
        from app.modules.work.contracts import PassSubmission

        configuration.update(db_session, SearchSettings(enabled=True))

        submitted = {
            execution.submission.source
            for execution in work_engine.executions.values()
            if isinstance(execution.submission, PassSubmission)
        }
        assert submitted >= {
            JobKind.SEARCH_PROJECT,
            JobKind.SEARCH_INDEX,
            JobKind.SEARCH_REPAIR,
            JobKind.SEARCH_CAPTION_QUEUE,
            JobKind.SEARCH_EXPAND,
        }
        with Session(db_session.get_bind()) as other:
            assert configuration.read(other).settings.enabled is True
