"""SQL for the icon catalog.

These four functions are the documented exception to "every repository function takes
`user_id`" (`.claude/rules/repositories.md`). `icon_pack`, `icon_asset` and `colour_swatch`
are reference tables with no tenant column at all — the same shape as
`list_offered_templates` and `enabled_currency_codes`, and for the same reason. There is no
row here that could belong to one brother and not another, so there is nothing to filter
and no leak to cause.

Ordering is applied here because it is stable, cheap and indexed by the primary key; the
service does the parts that need testing without Postgres.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.reference import ColourSwatch, IconAsset, IconPack

RowValue = str | int | bool | list[str] | None
RowDict = dict[str, RowValue]


def list_packs(db: Session) -> list[RowDict]:
    """Every pack, enabled or not — `visible_packs` decides, so the rule stays testable."""
    stmt = select(
        IconPack.pack_key,
        IconPack.label,
        IconPack.renderer,
        IconPack.is_enabled,
        IconPack.min_app_build,
        IconPack.sort_order,
    ).order_by(IconPack.sort_order, IconPack.pack_key)

    return [dict(row) for row in db.execute(stmt).mappings()]


def list_enabled_icons(db: Session) -> list[RowDict]:
    """Only enabled assets: this list is what the picker offers."""
    stmt = (
        select(
            IconAsset.token,
            IconAsset.pack_key,
            IconAsset.render_value,
            IconAsset.label,
            IconAsset.keywords,
            IconAsset.kind_hint,
            IconAsset.is_enabled,
            IconAsset.sort_order,
        )
        .where(IconAsset.is_enabled.is_(True))
        .order_by(IconAsset.pack_key, IconAsset.sort_order, IconAsset.token)
    )

    return [dict(row) for row in db.execute(stmt).mappings()]


def list_icons_for_validation(db: Session) -> list[RowDict]:
    """Every asset including disabled ones.

    The write validator needs `is_enabled` to reject a disabled token, and the renderer
    needs disabled rows present so an existing category keeps its glyph after its pack is
    retired. One query serves both; splitting them would let the two disagree.
    """
    stmt = select(
        IconAsset.token,
        IconAsset.pack_key,
        IconAsset.render_value,
        IconAsset.is_enabled,
    ).order_by(IconAsset.token)

    return [dict(row) for row in db.execute(stmt).mappings()]


def list_enabled_colours(db: Session) -> list[RowDict]:
    stmt = (
        select(ColourSwatch.hex, ColourSwatch.label, ColourSwatch.sort_order)
        .where(ColourSwatch.is_enabled.is_(True))
        .order_by(ColourSwatch.sort_order, ColourSwatch.hex)
    )

    return [dict(row) for row in db.execute(stmt).mappings()]
