"""Seed an expired receipt in the real-browser suite's disposable vault."""

import hashlib
import json
import sys
import uuid
from datetime import timedelta
from pathlib import Path

from sqlmodel import Session, create_engine, select

from app.core.time import utcnow
from app.db.models import User
from app.modules.ingestion.staging_leases import create_review_lease
from tests.factories.capture import build_inbox_item


def main() -> None:
    root = Path(sys.argv[1]).resolve(strict=True)
    database = (root / "test.sqlite").resolve(strict=True)
    staged = root / "staging" / f"expired-{uuid.uuid4().hex}.stl"
    payload = b"expired browser fixture"
    engine = create_engine(f"sqlite:///{database}")
    try:
        with Session(engine) as session:
            owner = session.exec(select(User).where(User.username == "admin")).one()
            item = build_inbox_item(session, owner)
            staged.write_bytes(payload)
            create_review_lease(
                session,
                inbox_item_id=item.id,
                owner_user_id=owner.id,
                path=staged,
                size_bytes=len(payload),
                sha256=hashlib.sha256(payload).hexdigest(),
                now=utcnow() - timedelta(days=400),
            )
            session.commit()
            print(json.dumps({"path": str(staged), "inbox_item_id": item.id}))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
