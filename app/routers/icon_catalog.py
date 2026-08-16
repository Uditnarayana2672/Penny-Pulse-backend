"""`GET /icon-catalog` — the pickable icon and colour set, served rather than hardcoded.

`category.icon` was always a semantic token rather than a Tabler class, but nothing served
the set, so the client shipped its own token-to-glyph map and adding an icon meant an app
release. This endpoint is what turns that into a data change.

Cached by `catalog_version` and revalidated with `If-None-Match`. The payload is small
enough that the 304 is about correctness rather than bytes: four users on a phone should
not re-parse a catalog that has not moved.
"""

from typing import Annotated

from fastapi import APIRouter, Header, Query, Response

from app.auth import DbSession, UserId
from app.repositories import icon_catalog as icon_repo
from app.schemas.icon_catalog import IconCatalogOut
from app.services import icon_catalog as icon_service

router = APIRouter(tags=["icon-catalog"])


@router.get("/icon-catalog", response_model=IconCatalogOut)
def get_icon_catalog(
    user_id: UserId,
    db: DbSession,
    response: Response,
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
    app_build: Annotated[int | None, Query(ge=1)] = None,
) -> IconCatalogOut | Response:
    """The enabled catalog, filtered to what this client can draw.

    Authenticated but profile-free: the picker is reachable during onboarding, before a
    `profile` row exists, so requiring one would make the first category the user creates
    the one they cannot choose an icon for.

    `app_build` is the client's build number and gates packs behind `min_app_build`. It is
    optional, and omitting it hides every gated pack — the safe direction, because a pack
    the client cannot render is worse than one it never sees.
    """
    packs = icon_service.visible_packs(icon_repo.list_packs(db), app_build)
    catalog = icon_service.catalog_out(
        packs,
        icon_repo.list_enabled_icons(db),
        icon_repo.list_enabled_colours(db),
    )

    etag = f'"{catalog.catalog_version}"'
    if if_none_match is not None and etag in {
        candidate.strip() for candidate in if_none_match.split(",")
    }:
        # 304 carries no body, so the ETag has to go on this response too or the client
        # loses the validator it just proved it had.
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "no-cache"})

    response.headers["ETag"] = etag
    # `no-cache` means revalidate, not "do not store" — the client keeps the payload and
    # asks whether it is still current, which is the whole point of the version.
    response.headers["Cache-Control"] = "no-cache"
    return catalog
