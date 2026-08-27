"""Defends require superuser at the admin API integration boundary.

A regression could bypass an admin boundary or leave recovery state inconsistent.
"""

from __future__ import annotations

from ._admin_shared import (
    Collection,
    File,
    FileType,
    HTTPException,
    Model,
    Session,
    SQLModel,
    Tag,
    TestClient,
    UserUpdate,
    _headers,
    _user,
    admin_api,
    create_engine,
    pytest,
    threading,
    time,
    utcnow,
)


class TestRequireSuperuser:
    def test_non_superuser_blocked(
        self, client: TestClient, db_session: Session
    ) -> None:
        user = _user(db_session, "regular", superuser=False)
        resp = client.get("/api/v1/admin/users", headers=_headers(user))
        assert resp.status_code == 403
        assert resp.json()["detail"] == "admin_required"


class TestListUsers:
    def test_list_excludes_deleted(
        self, client: TestClient, db_session: Session
    ) -> None:
        admin = _user(db_session, "admin1")
        gone = _user(db_session, "gone", superuser=False)
        gone.deleted_at = utcnow()
        db_session.add(gone)
        db_session.commit()

        resp = client.get("/api/v1/admin/users", headers=_headers(admin))
        assert resp.status_code == 200
        usernames = {u["username"] for u in resp.json()}
        assert "admin1" in usernames
        assert "gone" not in usernames


class TestCreateUser:
    def test_create_duplicate_username_conflict(
        self, client: TestClient, db_session: Session
    ) -> None:
        admin = _user(db_session, "admin2")
        payload = {"username": "dupe", "password": "Password123"}
        first = client.post(
            "/api/v1/admin/users", json=payload, headers=_headers(admin)
        )
        assert first.status_code == 201
        second = client.post(
            "/api/v1/admin/users", json=payload, headers=_headers(admin)
        )
        assert second.status_code == 409
        assert second.json()["detail"] == "user_already_exists"

    def test_create_user_success_not_superuser(
        self, client: TestClient, db_session: Session
    ) -> None:
        admin = _user(db_session, "admin3")
        resp = client.post(
            "/api/v1/admin/users",
            json={"username": "newbie", "password": "Password123"},
            headers=_headers(admin),
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["is_superuser"] is False
        assert body["is_active"] is True


class TestUpdateUser:
    def test_update_not_found(self, client: TestClient, db_session: Session) -> None:
        admin = _user(db_session, "admin4")
        resp = client.patch(
            "/api/v1/admin/users/999",
            json={"email": "x@x.com"},
            headers=_headers(admin),
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "user_not_found"

    def test_update_deleted_user_404(
        self, client: TestClient, db_session: Session
    ) -> None:
        admin = _user(db_session, "admin5")
        target = _user(db_session, "deleted-target", superuser=False)
        target.deleted_at = utcnow()
        db_session.add(target)
        db_session.commit()

        resp = client.patch(
            f"/api/v1/admin/users/{target.id}",
            json={"email": "x@x.com"},
            headers=_headers(admin),
        )
        assert resp.status_code == 404

    def test_demote_last_superuser_blocked(
        self, client: TestClient, db_session: Session
    ) -> None:
        admin = _user(db_session, "sole-admin")
        resp = client.patch(
            f"/api/v1/admin/users/{admin.id}",
            json={"is_superuser": False},
            headers=_headers(admin),
        )
        assert resp.status_code == 400
        assert resp.json()["detail"] == "last_superuser_required"

    def test_deactivate_last_superuser_blocked(
        self, client: TestClient, db_session: Session
    ) -> None:
        admin = _user(db_session, "sole-admin-2")
        resp = client.patch(
            f"/api/v1/admin/users/{admin.id}",
            json={"is_active": False},
            headers=_headers(admin),
        )
        assert resp.status_code == 400
        assert resp.json()["detail"] == "last_superuser_required"

    def test_demote_superuser_allowed_when_another_remains(
        self, client: TestClient, db_session: Session
    ) -> None:
        admin1 = _user(db_session, "admin-a")
        admin2 = _user(db_session, "admin-b")
        resp = client.patch(
            f"/api/v1/admin/users/{admin1.id}",
            json={"is_superuser": False},
            headers=_headers(admin2),
        )
        assert resp.status_code == 200
        assert resp.json()["is_superuser"] is False

    def test_update_email_and_flags(
        self, client: TestClient, db_session: Session
    ) -> None:
        admin = _user(db_session, "admin-c")
        other = _user(db_session, "plain-user", superuser=False)
        resp = client.patch(
            f"/api/v1/admin/users/{other.id}",
            json={"email": "user@example.com", "is_superuser": True, "is_active": True},
            headers=_headers(admin),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["email"] == "user@example.com"
        assert body["is_superuser"] is True

    def test_update_non_superuser_not_locked_out(
        self, client: TestClient, db_session: Session
    ) -> None:
        # Guard only fires for users who ARE currently superuser+active.
        admin = _user(db_session, "admin-d")
        plain = _user(db_session, "plain-2", superuser=False)
        resp = client.patch(
            f"/api/v1/admin/users/{plain.id}",
            json={"is_active": False},
            headers=_headers(admin),
        )
        assert resp.status_code == 200
        assert resp.json()["is_active"] is False


class TestResetPassword:
    def test_reset_password_not_found(
        self, client: TestClient, db_session: Session
    ) -> None:
        admin = _user(db_session, "admin-e")
        resp = client.post(
            "/api/v1/admin/users/999/password",
            json={"password": "NewPassword123"},
            headers=_headers(admin),
        )
        assert resp.status_code == 404

    def test_reset_password_success(
        self, client: TestClient, db_session: Session
    ) -> None:
        admin = _user(db_session, "admin-f")
        target = _user(db_session, "reset-me", superuser=False)
        resp = client.post(
            f"/api/v1/admin/users/{target.id}/password",
            json={"password": "NewPassword123"},
            headers=_headers(admin),
        )
        assert resp.status_code == 200

    def test_reset_password_invalidates_existing_access_and_refresh_tokens(
        self, client: TestClient, db_session: Session
    ) -> None:
        admin = _user(db_session, "admin-reset-sessions")
        target = _user(db_session, "reset-sessions", superuser=False)
        login = client.post(
            "/api/v1/auth/login",
            json={"username": "reset-sessions", "password": "Password123"},
        ).json()

        response = client.post(
            f"/api/v1/admin/users/{target.id}/password",
            json={"password": "NewPassword123"},
            headers=_headers(admin),
        )

        assert response.status_code == 200
        assert (
            client.get(
                "/api/v1/auth/me",
                headers={"Authorization": f"Bearer {login['access_token']}"},
            ).status_code
            == 401
        )
        assert (
            client.post(
                "/api/v1/auth/refresh",
                json={"refresh_token": login["refresh_token"]},
            ).status_code
            == 401
        )
        assert (
            client.post(
                "/api/v1/auth/login",
                json={
                    "username": "reset-sessions",
                    "password": "NewPassword123",
                },
            ).status_code
            == 200
        )


class TestDeactivateUser:
    def test_deactivate_not_found(
        self, client: TestClient, db_session: Session
    ) -> None:
        admin = _user(db_session, "admin-g")
        resp = client.delete("/api/v1/admin/users/999", headers=_headers(admin))
        assert resp.status_code == 404

    def test_deactivate_already_deleted_404(
        self, client: TestClient, db_session: Session
    ) -> None:
        admin = _user(db_session, "admin-h")
        target = _user(db_session, "already-gone", superuser=False)
        target.deleted_at = utcnow()
        db_session.add(target)
        db_session.commit()
        resp = client.delete(
            f"/api/v1/admin/users/{target.id}", headers=_headers(admin)
        )
        assert resp.status_code == 404

    def test_deactivate_last_superuser_blocked(
        self, client: TestClient, db_session: Session
    ) -> None:
        admin = _user(db_session, "sole-admin-3")
        resp = client.delete(f"/api/v1/admin/users/{admin.id}", headers=_headers(admin))
        assert resp.status_code == 400
        assert resp.json()["detail"] == "last_superuser_required"

    def test_deactivate_success(self, client: TestClient, db_session: Session) -> None:
        admin = _user(db_session, "admin-i")
        target = _user(db_session, "deactivate-me", superuser=False)
        resp = client.delete(
            f"/api/v1/admin/users/{target.id}", headers=_headers(admin)
        )
        assert resp.status_code == 204
        db_session.refresh(target)
        assert target.is_active is False

    def test_deactivate_invalidates_existing_access_and_refresh_tokens(
        self, client: TestClient, db_session: Session
    ) -> None:
        admin = _user(db_session, "admin-deactivate-sessions")
        target = _user(db_session, "deactivate-sessions", superuser=False)
        login = client.post(
            "/api/v1/auth/login",
            json={"username": target.username, "password": "Password123"},
        ).json()

        response = client.delete(
            f"/api/v1/admin/users/{target.id}", headers=_headers(admin)
        )

        assert response.status_code == 204
        assert (
            client.get(
                "/api/v1/auth/me",
                headers={"Authorization": f"Bearer {login['access_token']}"},
            ).status_code
            == 401
        )
        assert (
            client.post(
                "/api/v1/auth/refresh",
                json={"refresh_token": login["refresh_token"]},
            ).status_code
            == 401
        )
        reactivated = client.patch(
            f"/api/v1/admin/users/{target.id}",
            json={"is_active": True},
            headers=_headers(admin),
        )
        assert reactivated.status_code == 200
        assert (
            client.post(
                "/api/v1/auth/refresh",
                json={"refresh_token": login["refresh_token"]},
            ).status_code
            == 401
        )

    def test_concurrent_admin_lockout_attempts_leave_one_active_superuser(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        engine = create_engine(
            f"sqlite:///{tmp_path / 'admin-race.db'}",
            connect_args={"check_same_thread": False, "timeout": 5},
        )
        SQLModel.metadata.create_all(engine)
        with Session(engine) as session:
            first = _user(session, "race-admin-a")
            second = _user(session, "race-admin-b")
            user_ids = [first.id, second.id]

        original_count = admin_api._active_superuser_count  # noqa: SLF001

        def slow_count(session: Session) -> int:
            count = original_count(session)
            time.sleep(0.1)
            return count

        monkeypatch.setattr(admin_api, "_active_superuser_count", slow_count)
        start = threading.Barrier(3)
        outcomes: list[int] = []

        def deactivate(user_id: int) -> None:
            with Session(engine) as session:
                start.wait(timeout=5)
                try:
                    admin_api.update_user(
                        user_id,
                        UserUpdate(is_active=False),
                        session,
                    )
                except HTTPException as exc:
                    outcomes.append(exc.status_code)
                else:
                    outcomes.append(200)

        threads = [
            threading.Thread(target=deactivate, args=(user_id,)) for user_id in user_ids
        ]
        for thread in threads:
            thread.start()
        start.wait(timeout=5)
        for thread in threads:
            thread.join(timeout=10)

        assert sorted(outcomes) == [200, 400]
        with Session(engine) as session:
            assert admin_api._active_superuser_count(session) == 1  # noqa: SLF001


class TestAdminDeleteResource:
    def test_unknown_resource_404(
        self, client: TestClient, db_session: Session
    ) -> None:
        admin = _user(db_session, "admin-j")
        resp = client.delete("/api/v1/admin/bogus/1", headers=_headers(admin))
        assert resp.status_code == 404
        assert resp.json()["detail"] == "resource_not_found"

    def test_unknown_resource_id_404(
        self, client: TestClient, db_session: Session
    ) -> None:
        admin = _user(db_session, "admin-k")
        resp = client.delete("/api/v1/admin/tags/999", headers=_headers(admin))
        assert resp.status_code == 404
        assert resp.json()["detail"] == "resource_id_not_found"

    def test_soft_delete_tag(self, client: TestClient, db_session: Session) -> None:
        admin = _user(db_session, "admin-l")
        tag = Tag(name="soft", slug="soft")
        db_session.add(tag)
        db_session.commit()
        db_session.refresh(tag)

        resp = client.delete(f"/api/v1/admin/tags/{tag.id}", headers=_headers(admin))
        assert resp.status_code == 204
        db_session.refresh(tag)
        assert tag.deleted_at is not None

    def test_hard_delete_collection(
        self, client: TestClient, db_session: Session
    ) -> None:
        admin = _user(db_session, "admin-m")
        col = Collection(name="Hard", slug="hard", path="hard")
        db_session.add(col)
        db_session.commit()
        db_session.refresh(col)

        col_id = col.id
        resp = client.delete(
            f"/api/v1/admin/collections/{col_id}?hard=true", headers=_headers(admin)
        )
        assert resp.status_code == 204
        db_session.expire_all()
        assert db_session.get(Collection, col_id) is None

    def test_hard_delete_file_also_removes_blob(
        self, client: TestClient, db_session: Session, tmp_path
    ) -> None:
        from app.services.storage_backend import get_backend
        from app.services.storage_ownership import record_creation

        admin = _user(db_session, "admin-n")
        backend = get_backend()
        key = backend.blob_key("host", 1, "test-admin-hard-delete.bin")
        record_creation(
            db_session,
            backend.create_bytes(b"hello", key),
            object_kind="artifact",
        )
        db_session.commit()
        assert backend.exists(key)

        model = Model(name="host", slug="host", hash="9" * 64)
        db_session.add(model)
        db_session.commit()
        db_session.refresh(model)

        file_row = File(
            model_id=model.id,
            path=key,
            original_filename="f.bin",
            file_type=FileType.STL,
            size_bytes=5,
            sha256="0" * 64,
        )
        db_session.add(file_row)
        db_session.commit()
        db_session.refresh(file_row)

        file_id = file_row.id
        resp = client.delete(
            f"/api/v1/admin/files/{file_id}?hard=true", headers=_headers(admin)
        )
        assert resp.status_code == 204
        assert not backend.exists(key)
        db_session.expire_all()
        assert db_session.get(File, file_id) is None

    def test_hard_delete_external_file_preserves_nas_bytes(
        self, client: TestClient, db_session: Session, tmp_path
    ) -> None:
        admin = _user(db_session, "admin-external-file")
        nas_path = tmp_path / "linked-model.stl"
        original = b"user-owned-nas-bytes"
        nas_path.write_bytes(original)

        model = Model(name="linked", slug="linked", hash="8" * 64)
        db_session.add(model)
        db_session.commit()
        db_session.refresh(model)
        file_row = File(
            model_id=model.id,
            path=str(nas_path),
            original_filename=nas_path.name,
            file_type=FileType.STL,
            size_bytes=len(original),
            sha256="1" * 64,
            is_external=True,
        )
        db_session.add(file_row)
        db_session.commit()
        db_session.refresh(file_row)
        file_id = file_row.id

        response = client.delete(
            f"/api/v1/admin/files/{file_id}?hard=true",
            headers=_headers(admin),
        )

        assert response.status_code == 204
        db_session.expire_all()
        assert db_session.get(File, file_id) is None
        assert nas_path.read_bytes() == original
