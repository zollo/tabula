"""Browsing and exporting archived posts."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.post import Post, PostKind
from app.models.social_account import SocialAccount


def _filtered(
    account: SocialAccount,
    q: str | None,
    kind: PostKind | None,
    since: datetime | None,
    until: datetime | None,
):
    query = select(Post).where(Post.social_account_id == account.id)
    if q:
        query = query.where(func.lower(Post.text).contains(q.lower()))
    if kind:
        query = query.where(Post.kind == kind)
    if since:
        query = query.where(Post.posted_at >= since)
    if until:
        query = query.where(Post.posted_at <= until)
    return query


async def list_posts(
    db: AsyncSession,
    account: SocialAccount,
    *,
    q: str | None = None,
    kind: PostKind | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Post], int]:
    query = _filtered(account, q, kind, since, until)
    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    rows = await db.execute(
        query.order_by(Post.posted_at.desc(), Post.id).limit(limit).offset(offset)
    )
    return list(rows.scalars()), int(total)


async def all_posts(
    db: AsyncSession,
    account: SocialAccount,
    *,
    q: str | None = None,
    kind: PostKind | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[Post]:
    rows = await db.execute(
        _filtered(account, q, kind, since, until).order_by(Post.posted_at.desc(), Post.id)
    )
    return list(rows.scalars())
