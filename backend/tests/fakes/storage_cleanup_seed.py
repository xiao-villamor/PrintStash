"""Give the real-browser cleanup flow one receipt-verified expired file."""

import hashlib
import json
import uuid
from datetime import timedelta

from sqlmodel import select

from app.core.config import settings
from app.core.time import utcnow
from app.db.models import User
from app.db.session import get_session_factory
from app.modules.ingestion.staging_leases import create_review_lease
from tests.factories.capture import build_inbox_item


def main() -> None:
    with get_session_factory().session() as session:
        actor = session.exec(select(User).where(User.username == "admin")).one()
        item = build_inbox_item(session, actor)
        path = settings.staging_dir / f"e2e-expired-{uuid.uuid4().hex}.stl"
        payload = b"expired browser cleanup fixture"
        path.write_bytes(payload)
        create_review_lease(
            session,
            inbox_item_id=item.id,
            owner_user_id=actor.id,
            path=path,
            size_bytes=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
            now=utcnow() - timedelta(days=400),
        )
        session.commit()
        print(json.dumps({"itemId": item.id, "path": str(path)}))


if __name__ == "__main__":
    main()
