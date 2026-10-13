"""HTTP preconditions for the supported first-party metadata edit contract."""

from typing import Annotated

from fastapi import Header

from app.schemas.editing import EditPrecondition


def edit_precondition(
    if_match: Annotated[
        str | None,
        Header(description="Strong aggregate ETag from the detail response."),
    ] = None,
    contract: Annotated[
        str | None,
        Header(
            alias="X-PrintStash-Edit-Contract",
            description="conditional-v1 requires If-Match. Omission retains unprotected legacy compatibility.",
        ),
    ] = None,
) -> EditPrecondition:
    return EditPrecondition(if_match=if_match, contract=contract)
