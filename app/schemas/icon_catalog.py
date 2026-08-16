"""Wire types for `GET /icon-catalog`.

The whole point of this endpoint is that the client holds no icon table. It receives the
pickable set, caches it against `catalog_version`, and revalidates. Adding an emoji pack is
then a seed row in `0011`-style SQL and nothing else — no app release, no client change.

So `render_value` is served rather than a token the client must resolve, and `renderer`
says how to draw it. A client that meets an unfamiliar `renderer` skips that pack; it does
not guess.
"""

from pydantic import BaseModel

from app.schemas.onboarding import Kind

Renderer = str


class IconPackOut(BaseModel):
    """`renderer` is `glyph_font` | `unicode` | `image_url`.

    Deliberately a plain `str` and not a `Literal`: a new renderer must be addable as seed
    data, and a `Literal` here would make the API reject a pack the database accepted.
    """

    pack_key: str
    label: str
    renderer: Renderer
    sort_order: int


class IconAssetOut(BaseModel):
    """`token` is what gets stored on `category.icon`; `render_value` is what gets drawn.

    `kind_hint` orders the picker, it never filters it — a user putting a wallet on an
    expense category is not making a mistake.
    """

    token: str
    pack_key: str
    render_value: str
    label: str
    keywords: list[str]
    kind_hint: Kind | None
    sort_order: int


class ColourSwatchOut(BaseModel):
    hex: str
    label: str
    sort_order: int


class IconCatalogOut(BaseModel):
    """`catalog_version` is the ETag. It changes only when the served content changes.

    Derived from the rows themselves rather than from a timestamp column, so re-running the
    seed does not invalidate every client's cache for no reason.
    """

    catalog_version: str
    packs: list[IconPackOut]
    icons: list[IconAssetOut]
    colours: list[ColourSwatchOut]
