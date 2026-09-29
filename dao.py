#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""API-neutral library models and the DAO contract the addon is written against."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

MOVIE = 'MOVIE'
TELEVISION = 'TELEVISION'
BINGEPASS = 'BINGEPASS'

# How a title is lent: Instant titles are always available; Flex titles have limited copies.
INSTANT = 'INSTANT'
FLEX = 'FLEX'

# BingePasses: one borrow unlocks a bundle for some days. INCLUDED passes bundle titles of this service
# (Title.included); PARTNER passes unlock a partner's own website instead.
BINGEPASS_INCLUDED = 'INCLUDED'
BINGEPASS_PARTNER = 'PARTNER'

# DRM key systems (Stream.drm).
WIDEVINE = 'com.widevine.alpha'


class LibraryError(Exception):
    pass


class AuthError(LibraryError):
    pass


class NotBorrowedError(LibraryError):
    """Raised by stream() when the item has to be borrowed first."""


@dataclass
class Kind:
    id: str
    name: str
    label: str


@dataclass
class Genre:
    id: str
    name: str


@dataclass
class Episode:
    id: str
    number: int
    title: str
    synopsis: str = ''
    image_url: Optional[str] = None
    borrowed: bool = False
    due: Optional[datetime] = None
    borrowable: bool = False
    duration: int = 0  # seconds


@dataclass
class Title:
    id: str
    title: str
    kind: Optional[str] = None
    image_url: Optional[str] = None
    artist: Optional[str] = None
    borrowed: bool = False
    due: Optional[datetime] = None
    badge: Optional[str] = None
    episodes: List[Episode] = field(default_factory=list)
    borrowable: bool = False
    synopsis: str = ''
    year: Optional[int] = None
    duration: int = 0  # seconds
    rating: Optional[str] = None
    genres: List[str] = field(default_factory=list)
    directors: List[str] = field(default_factory=list)
    lending_message: str = ''
    borrow_type: Optional[str] = None  # INSTANT or FLEX
    series_id: Optional[str] = None
    series_name: Optional[str] = None
    rating_average: float = 0  # community rating, 0-5 stars
    rating_count: int = 0
    user_stars: Optional[int] = None  # this user's own rating, 1-5
    season: Optional[int] = None  # for TV: which season this title is, when the service says
    bingepass_type: Optional[str] = None  # BINGEPASS_INCLUDED or BINGEPASS_PARTNER
    included: List[Title] = field(default_factory=list)  # an INCLUDED BingePass's movies and seasons
    content_kinds: List[str] = field(default_factory=list)  # kinds its genres belong to, when known
    bonus: bool = False  # a bonus borrow: doesn't use one of the monthly borrows


@dataclass
class Collection:
    id: str
    name: str


@dataclass
class HistoryItem:
    title: Title
    borrowed_date: Optional[datetime] = None
    episode_title: Optional[str] = None


@dataclass
class TitlePage:
    titles: List[Title]
    total: int
    page: int
    page_size: int

    @property
    def has_more(self) -> bool:
        return self.page * self.page_size < self.total


@dataclass
class BorrowLimits:
    message: str = ''
    instant_message: str = ''
    flex_remaining: Optional[int] = None
    flex_message: str = ''


@dataclass
class Stream:
    """What a player needs to play one borrowed item."""
    url: str
    manifest_type: str  # 'mpd' (DASH)
    drm: Optional[str] = None  # key system, e.g. WIDEVINE
    license_url: Optional[str] = None
    license_headers: Dict[str, str] = field(default_factory=dict)


class LibraryDAO(ABC):
    """Ids passed in and out are opaque strings; callers only hand them back to the same DAO.

    A page_size of None asks for the largest page the backend serves."""

    def __init__(self, session_token: Optional[str] = None, device_id: Optional[str] = None):
        """device_id: a stable, random per-install id, used where the service personalizes by device."""
        self.session_token = session_token
        self.device_id = device_id

    @abstractmethod
    def login(self, username: str, password: str) -> str:
        """Authenticate, apply the session to this DAO, and return an opaque token the caller may persist."""

    @abstractmethod
    def kinds(self) -> List[Kind]: ...

    @abstractmethod
    def genres(self, kind_id: str) -> List[Genre]: ...

    @abstractmethod
    def borrowed(self) -> List[Title]: ...

    @abstractmethod
    def borrow_limits(self) -> BorrowLimits: ...

    @abstractmethod
    def bingepass_titles(self, page: int = 1, page_size: Optional[int] = None) -> TitlePage: ...

    @abstractmethod
    def genre_titles(self, genre_id: str, page: int = 1, page_size: Optional[int] = None,
                     kind_id: Optional[str] = None) -> TitlePage:
        """kind_id, when given, keeps only titles of that kind (genres and collections can mix kinds)."""

    @abstractmethod
    def search(self, query: str, kind_id: str, page: int = 1, page_size: Optional[int] = None) -> TitlePage: ...

    @abstractmethod
    def top_rated(self, kind_id: str, genre_id: Optional[str] = None, page: int = 1,
                  page_size: Optional[int] = None) -> TitlePage:
        """Well-rated titles available now, most popular first; within one genre when given."""

    @abstractmethod
    def featured_titles(self, kind_id: str) -> List[Title]: ...

    @abstractmethod
    def popular_titles(self, kind_id: str, borrow_type: Optional[str] = None) -> List[Title]:
        """borrow_type: None for all, or INSTANT / FLEX."""

    @abstractmethod
    def recent_titles(self, kind_id: str) -> List[Title]: ...

    @abstractmethod
    def collections(self, kind_id: str) -> List[Collection]: ...

    @abstractmethod
    def collection_titles(self, collection_id: str, page: int = 1, page_size: Optional[int] = None,
                          kind_id: Optional[str] = None) -> TitlePage:
        """kind_id, when given, keeps only titles of that kind."""

    @abstractmethod
    def series_titles(self, series_id: str, page: int = 1, page_size: Optional[int] = None) -> TitlePage: ...

    @abstractmethod
    def related_titles(self, title_id: str) -> List[Title]: ...

    @abstractmethod
    def bonus_titles(self, kind_ids: List[str]) -> List[Title]:
        """The library's current bonus borrows (a monthly promotion) of the given kinds."""

    @abstractmethod
    def history(self, page: int = 1, page_size: int = 50) -> List[HistoryItem]:
        """Previously borrowed titles, newest first. A short page means there are no more."""

    @abstractmethod
    def rate(self, title_id: str, stars: int) -> None:
        """Set this user's 1-5 star rating for a title."""

    @abstractmethod
    def title(self, title_id: str) -> Title:
        """Full details, including episodes and whether each is borrowed."""

    @abstractmethod
    def stream(self, title_id: str, episode_id: Optional[str] = None, bingepass_id: Optional[str] = None) -> Stream:
        """Playback info for a borrowed title, or for one borrowed episode of it.

        bingepass_id: the BingePass the title was reached through; its loan covers the title."""

    @abstractmethod
    def borrow(self, item_id: str) -> str:
        """Borrow a title (or an episode, by its id). Returns the library's confirmation message."""

    @abstractmethod
    def return_item(self, item_id: str) -> None:
        """Return a borrowed title (or an episode, by its id)."""
