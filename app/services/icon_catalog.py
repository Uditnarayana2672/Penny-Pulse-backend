"""Icon token resolution and the catalog response, framework-free.

The resolver is the interesting part and it is pure: a token and the catalog go in, a
render value comes out. It exists in exactly one place because it has two callers with
opposite temperaments — the write validator, which must reject an unknown token, and the
read path, which must never fail on one.

That asymmetry is deliberate. A category written years ago against a pack since disabled
still has to draw *something*, and a blank tile on the entry grid is a worse outcome than a
wrong-looking one. So `resolve_for_render` falls back and `is_writable_token` refuses.
"""

import hashlib

from app.schemas.icon_catalog import (
    ColourSwatchOut,
    IconAssetOut,
    IconCatalogOut,
    IconPackOut,
)
from app.services.me import RowDict, as_int, as_str_or_none

# A token with no namespace is a pre-0011 value. 0011 backfills the stored data, but a
# client that has not refreshed still holds bare tokens in memory, and an offline queue can
# replay one weeks later.
DEFAULT_PACK = "tabler"

# Every screen's last resort. `tabler:dots` is the seeded "Other" glyph, so an unresolvable
# icon looks like a category nobody bothered to style — which is what it is.
FALLBACK_TOKEN = "tabler:dots"
FALLBACK_RENDER_VALUE = "ti ti-dots"


def normalise_token(token: str) -> str:
    """`utensils` -> `tabler:utensils`; `emoji:ramen` -> unchanged.

    Trimmed but not lowercased: `render_value` is case-sensitive for a URL, and the tokens
    that exist are already lowercase, so folding case here would only mask a typo.
    """
    trimmed = token.strip()
    if ":" in trimmed:
        return trimmed
    return f"{DEFAULT_PACK}:{trimmed}"


def index_by_token(icons: list[RowDict]) -> dict[str, RowDict]:
    return {str(icon["token"]): icon for icon in icons}


def is_writable_token(token: str, by_token: dict[str, RowDict]) -> bool:
    """Whether `POST`/`PATCH` may store this token.

    Strict on purpose: an unknown or disabled token is rejected at write time so it can
    never reach the database, which is what keeps the read-side fallback rare enough to
    stay a safety net rather than a design.
    """
    icon = by_token.get(normalise_token(token))
    return icon is not None and bool(icon["is_enabled"])


def resolve_for_render(token: str, by_token: dict[str, RowDict]) -> tuple[str, str]:
    """`(token, render_value)` — never raises, never returns an empty glyph.

    A disabled asset still resolves to its own glyph rather than the fallback: disabling a
    pack stops it being *offered*, and rewriting what existing categories look like is a
    different and much ruder decision.
    """
    normalised = normalise_token(token)
    icon = by_token.get(normalised)
    if icon is None:
        return FALLBACK_TOKEN, FALLBACK_RENDER_VALUE
    return normalised, str(icon["render_value"])


def visible_packs(packs: list[RowDict], app_build: int | None) -> list[RowDict]:
    """Packs this client can draw.

    `min_app_build` is how an image-backed pack ships without breaking a phone that has not
    updated. An unknown build is treated as old — the conservative direction, since the cost
    of hiding a pack is a smaller picker and the cost of showing it is a broken one.
    """
    visible: list[RowDict] = []
    for pack in packs:
        if not pack["is_enabled"]:
            continue
        minimum = pack["min_app_build"]
        if minimum is not None and (app_build is None or app_build < as_int(minimum, "min_app_build")):
            continue
        visible.append(pack)
    return visible


def catalog_version(
    packs: list[RowDict], icons: list[RowDict], colours: list[RowDict]
) -> str:
    """A stable digest of exactly what is being served.

    Content-derived rather than a max(updated_at): re-running the seed must not invalidate
    four clients' caches when nothing they can see has changed. Sorted before hashing so
    row order out of Postgres cannot alter the answer.
    """
    parts: list[str] = []
    parts.extend(sorted(f"p:{pack['pack_key']}:{pack['renderer']}" for pack in packs))
    parts.extend(sorted(f"i:{icon['token']}:{icon['render_value']}" for icon in icons))
    parts.extend(sorted(f"c:{colour['hex']}" for colour in colours))
    digest = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
    return digest[:16]


def catalog_out(
    packs: list[RowDict], icons: list[RowDict], colours: list[RowDict]
) -> IconCatalogOut:
    pack_keys = {str(pack["pack_key"]) for pack in packs}
    # An asset whose pack is hidden would be unrenderable: the client learns how to draw
    # from the pack, so serving the asset alone would hand it a value and no renderer.
    servable = [icon for icon in icons if str(icon["pack_key"]) in pack_keys]

    return IconCatalogOut(
        catalog_version=catalog_version(packs, servable, colours),
        packs=[
            IconPackOut(
                pack_key=str(pack["pack_key"]),
                label=str(pack["label"]),
                renderer=str(pack["renderer"]),
                sort_order=as_int(pack["sort_order"], "sort_order"),
            )
            for pack in packs
        ],
        icons=[
            IconAssetOut(
                token=str(icon["token"]),
                pack_key=str(icon["pack_key"]),
                render_value=str(icon["render_value"]),
                label=str(icon["label"]),
                keywords=list(icon["keywords"] or []),  # type: ignore[arg-type]
                kind_hint=as_str_or_none(icon["kind_hint"]),  # type: ignore[arg-type]
                sort_order=as_int(icon["sort_order"], "sort_order"),
            )
            for icon in servable
        ],
        colours=[
            ColourSwatchOut(
                hex=str(colour["hex"]),
                label=str(colour["label"]),
                sort_order=as_int(colour["sort_order"], "sort_order"),
            )
            for colour in colours
        ],
    )
