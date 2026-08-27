"""Defends printer permission administration and collection boundaries.

Direct printer grants are security-sensitive rows: only a superuser may change
them, and list/dashboard/history responses must not disclose ungranted devices.
"""

from __future__ import annotations

from app.core.time import utcnow

from ._printers_shared import (
    Printer,
    PrinterPermission,
    PrinterRole,
    PrintJob,
    PrintJobState,
    Session,
    TestClient,
    User,
    _grant_printer,
    _user_headers,
    pytest,
    select,
)


def _printer(db_session: Session, *, name: str = "Permission printer") -> Printer:
    printer = Printer(name=name, moonraker_url="http://printer.local:7125")
    db_session.add(printer)
    db_session.commit()
    db_session.refresh(printer)
    return printer


def _user(db_session: Session, username: str) -> User:
    _user_headers(db_session, username)
    return db_session.exec(select(User).where(User.username == username)).one()


class TestListPrinterPermissions:
    def test_lists_exact_direct_permissions(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
    ) -> None:
        printer = _printer(db_session)
        alpha = _user(db_session, "alpha-grantee")
        beta = _user(db_session, "beta-grantee")
        db_session.add_all(
            [
                PrinterPermission(
                    printer_id=printer.id, user_id=beta.id, role=PrinterRole.PRINT
                ),
                PrinterPermission(
                    printer_id=printer.id, user_id=alpha.id, role=PrinterRole.VIEW
                ),
            ]
        )
        db_session.commit()

        response = client.get(
            f"/api/v1/printers/{printer.id}/permissions", headers=auth_headers
        )

        assert response.status_code == 200, response.text
        assert [(row["username"], row["role"]) for row in response.json()] == [
            ("alpha-grantee", "view"),
            ("beta-grantee", "print"),
        ]

    def test_denies_a_delegated_printer_admin(
        self, client: TestClient, db_session: Session
    ) -> None:
        printer = _printer(db_session, name="Delegated permission printer")
        headers = _user_headers(db_session, "delegated-permission-admin")
        _grant_printer(
            db_session, "delegated-permission-admin", printer, PrinterRole.ADMIN
        )

        response = client.get(
            f"/api/v1/printers/{printer.id}/permissions", headers=headers
        )

        assert response.status_code == 403


class TestUpsertPrinterPermission:
    @pytest.mark.parametrize("role", list(PrinterRole), ids=lambda role: role.value)
    def test_grants_every_registered_printer_role(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
        role: PrinterRole,
    ) -> None:
        printer = _printer(db_session, name=f"{role.value} permission printer")
        target = _user(db_session, f"{role.value}-grantee")

        response = client.put(
            f"/api/v1/printers/{printer.id}/permissions/{target.id}",
            json={"role": role.value},
            headers=auth_headers,
        )

        assert response.status_code == 200, response.text
        permission = db_session.exec(
            select(PrinterPermission).where(
                PrinterPermission.printer_id == printer.id,
                PrinterPermission.user_id == target.id,
            )
        ).one()
        assert permission.role == role

    def test_updates_an_existing_permission_without_duplicating_it(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
    ) -> None:
        printer = _printer(db_session, name="Updated permission printer")
        target = _user(db_session, "updated-grantee")
        existing = PrinterPermission(
            printer_id=printer.id, user_id=target.id, role=PrinterRole.VIEW
        )
        db_session.add(existing)
        db_session.commit()

        response = client.put(
            f"/api/v1/printers/{printer.id}/permissions/{target.id}",
            json={"role": "control"},
            headers=auth_headers,
        )

        assert response.status_code == 200, response.text
        permissions = db_session.exec(
            select(PrinterPermission).where(
                PrinterPermission.printer_id == printer.id,
                PrinterPermission.user_id == target.id,
            )
        ).all()
        assert len(permissions) == 1
        assert permissions[0].role == PrinterRole.CONTROL

    def test_returns_not_found_for_an_unknown_user(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
    ) -> None:
        printer = _printer(db_session, name="Unknown user permission printer")

        response = client.put(
            f"/api/v1/printers/{printer.id}/permissions/999999",
            json={"role": "view"},
            headers=auth_headers,
        )

        assert response.status_code == 404
        assert response.json()["detail"] == "user_not_found"

    def test_denies_a_delegated_printer_admin(
        self, client: TestClient, db_session: Session
    ) -> None:
        printer = _printer(db_session, name="Denied grant printer")
        target = _user(db_session, "denied-grantee")
        headers = _user_headers(db_session, "denied-grant-admin")
        _grant_printer(db_session, "denied-grant-admin", printer, PrinterRole.ADMIN)

        response = client.put(
            f"/api/v1/printers/{printer.id}/permissions/{target.id}",
            json={"role": "view"},
            headers=headers,
        )

        assert response.status_code == 403
        assert (
            db_session.exec(
                select(PrinterPermission).where(
                    PrinterPermission.printer_id == printer.id,
                    PrinterPermission.user_id == target.id,
                )
            ).first()
            is None
        )


class TestDeletePrinterPermission:
    def test_removes_the_direct_permission_row(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
    ) -> None:
        printer = _printer(db_session, name="Revoked permission printer")
        target = _user(db_session, "revoked-grantee")
        permission = PrinterPermission(
            printer_id=printer.id, user_id=target.id, role=PrinterRole.PRINT
        )
        db_session.add(permission)
        db_session.commit()

        response = client.delete(
            f"/api/v1/printers/{printer.id}/permissions/{target.id}",
            headers=auth_headers,
        )

        assert response.status_code == 204
        assert (
            db_session.exec(
                select(PrinterPermission).where(
                    PrinterPermission.printer_id == printer.id,
                    PrinterPermission.user_id == target.id,
                )
            ).first()
            is None
        )

    def test_returns_not_found_for_a_missing_permission(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
    ) -> None:
        printer = _printer(db_session, name="Missing permission printer")
        target = _user(db_session, "missing-grantee")

        response = client.delete(
            f"/api/v1/printers/{printer.id}/permissions/{target.id}",
            headers=auth_headers,
        )

        assert response.status_code == 404
        assert response.json()["detail"] == "permission_not_found"

    def test_denies_a_delegated_printer_admin(
        self, client: TestClient, db_session: Session
    ) -> None:
        printer = _printer(db_session, name="Denied revoke printer")
        target = _user(db_session, "retained-grantee")
        permission = PrinterPermission(
            printer_id=printer.id, user_id=target.id, role=PrinterRole.VIEW
        )
        db_session.add(permission)
        db_session.commit()
        headers = _user_headers(db_session, "denied-revoke-admin")
        _grant_printer(db_session, "denied-revoke-admin", printer, PrinterRole.ADMIN)

        response = client.delete(
            f"/api/v1/printers/{printer.id}/permissions/{target.id}",
            headers=headers,
        )

        assert response.status_code == 403
        assert db_session.get(PrinterPermission, permission.id) is not None


class TestPrinterVisibilityBoundaries:
    def test_dashboard_counts_only_granted_printers(
        self, client: TestClient, db_session: Session
    ) -> None:
        visible = _printer(db_session, name="Visible dashboard printer")
        hidden = _printer(db_session, name="Hidden dashboard printer")
        visible.status = "ready"
        hidden.status = "printing"
        db_session.add_all([visible, hidden])
        db_session.commit()
        headers = _user_headers(db_session, "dashboard-viewer")
        _grant_printer(db_session, "dashboard-viewer", visible, PrinterRole.VIEW)

        response = client.get("/api/v1/printers/dashboard", headers=headers)

        assert response.status_code == 200, response.text
        assert response.json()["total_printers"] == 1
        assert response.json()["status_counts"] == {"ready": 1}

    def test_jobs_limit_returns_only_the_newest_rows(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
    ) -> None:
        from app.db.models import File, FileType, Model

        printer = _printer(db_session, name="Limited history printer")
        model = Model(name="History", slug="printer-history", hash="f" * 64)
        db_session.add(model)
        db_session.commit()
        db_session.refresh(model)
        file_row = File(
            model_id=model.id,
            path="/data/history.gcode",
            original_filename="history.gcode",
            file_type=FileType.GCODE,
            version=1,
            size_bytes=10,
            sha256="e" * 64,
        )
        db_session.add(file_row)
        db_session.commit()
        db_session.refresh(file_row)
        for index in range(3):
            db_session.add(
                PrintJob(
                    printer_id=printer.id,
                    file_id=file_row.id,
                    model_id=model.id,
                    remote_filename=f"job-{index}.gcode",
                    state=PrintJobState.COMPLETED,
                    created_at=utcnow().replace(microsecond=index),
                )
            )
        db_session.commit()

        response = client.get(
            f"/api/v1/printers/{printer.id}/jobs?limit=2", headers=auth_headers
        )

        assert response.status_code == 200, response.text
        assert [row["remote_filename"] for row in response.json()] == [
            "job-2.gcode",
            "job-1.gcode",
        ]
