# -*- coding: utf-8 -*-
"""Kodi-free helpers for what the addon shows: labels, plots and which titles to list."""
import re
from datetime import datetime, timezone

import dao

VIDEO_KINDS = (dao.MOVIE, dao.TELEVISION)


def paragraphs(*parts):
    """A plot from its non-empty parts."""
    return '\n\n'.join(p for p in parts if p)


def due_text(due):
    return f'Due {due.astimezone():%Y-%m-%d %H:%M}' if due else ''


def time_left(due, now=None):
    """'3 days left' / '5 hours left' until a loan ends. Days are rounded, so a fresh 3-day loan
    reads 3 days, not 2."""
    hours = (due - (now or datetime.now(timezone.utc))).total_seconds() / 3600
    if hours <= 0:
        return 'due now'
    if hours < 24:
        n = max(1, int(hours))
        return f'{n} hour{"s" if n != 1 else ""} left'
    n = max(1, round(hours / 24))
    return f'{n} day{"s" if n != 1 else ""} left'


def loan_due(t):
    """When a borrowed title's loan ends. A TV season's episodes are borrowed one by one, so it's the
    soonest-due borrowed episode."""
    return t.due or min((e.due for e in t.episodes if e.borrowed and e.due), default=None)


def content_for(titles):
    """Kodi's content type, which decides the views and metadata a skin shows."""
    kinds = {t.kind for t in titles}
    if kinds == {dao.MOVIE}:
        return 'movies'
    if kinds == {dao.TELEVISION}:
        return 'tvshows'
    return 'videos'


def playable(titles):
    """Hide titles this addon can't play (ebooks, audiobooks, BingePasses, ...) that mixed lists contain."""
    return [t for t in titles or [] if t.kind in VIDEO_KINDS]


def by_season(titles):
    """Seasons in season-number order (an unnumbered season first), then everything else by title."""
    return sorted(titles, key=lambda t: (t.kind != dao.TELEVISION, t.season or 0, t.title))


def included_passes(titles):
    """BingePasses with something to watch here. Partner passes unlock another website, and a pass
    whose genres are all book/comic/music genres bundles nothing Kodi can play."""
    return [t for t in titles or [] if t.kind == dao.BINGEPASS and t.bingepass_type != dao.BINGEPASS_PARTNER
            and (not t.content_kinds or any(k in VIDEO_KINDS for k in t.content_kinds))]


BONUS_TEXT = "Bonus Borrow: this won't use one of your monthly borrows."


def borrow_allowance(t, limits):
    """The borrow dialog's line about the user's allowance. A bonus borrow doesn't use it."""
    if t.bonus:
        return BONUS_TEXT
    if not limits:
        return ''
    allowance = limits.flex_message if t.borrow_type == dao.FLEX else limits.instant_message
    return allowance or limits.message


def without_downloads(text):
    """Drop the library's sentences about downloading to mobile apps; that doesn't apply to Kodi."""
    sentences = re.split(r'(?<=[.!?])\s+', text or '')
    return ' '.join(s for s in sentences if 'download' not in s.lower()).strip()
