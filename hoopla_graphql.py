#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LibraryDAO backed by Hoopla's patron GraphQL gateway (the API behind www.hoopladigital.com)."""
import base64
import json
import re
from datetime import datetime

import requests

from dao import (BINGEPASS_INCLUDED, BINGEPASS_PARTNER, FLEX, INSTANT, MOVIE, TELEVISION, WIDEVINE, AuthError,
                 BorrowLimits, Collection, Episode, Genre, HistoryItem, Kind, LibraryDAO, LibraryError,
                 NotBorrowedError, Stream, Title, TitlePage)

GATEWAY = 'https://patron-api-gateway.hoopladigital.com'
GRAPHQL_URL = GATEWAY + '/graphql'
TOKENS_URL = GATEWAY + '/core/tokens'
ART_URL = 'https://cover.hoopladigital.com/{}_270.jpeg'
DASH_URL = 'https://dash.hoopladigital.com/{}/Manifest.mpd'
DRMTODAY_WIDEVINE_URL = 'https://lic.drmtoday.com/license-proxy-widevine/cenc/?specConform=true'
TIMEOUT = 30
# "Top rated": search's star filter (a string; "4" means 4 stars and up), sorted by popularity. Most titles
# are rated 4+, so popularity does most of the work; the filter keeps out popular but poorly rated ones.
TOP_RATED_STARS = '4'
# The most titles search will return per page (asking for more still returns 150).
MAX_PAGE_SIZE = 150
# The web client's audience for grown-up (non kids-mode) browsing.
AUDIENCE = 'ANY'
# Hoopla calls Instant borrowing PPU (pay per use) and Flex borrowing EST.
BORROW_TYPES = {'PPU': INSTANT, 'EST': FLEX}
BINGEPASS_TYPES = {'INTERNAL': BINGEPASS_INCLUDED, 'EXTERNAL': BINGEPASS_PARTNER}
# Bonus borrows have no type or search filter; they come as monthly featured collections named like
# "Bonus Borrows September 2026 | All Video" (also "| All Movies", "| All Titles"). Matching by name is
# the only handle, so it's kept to these constants.
BONUS_PREFIX = 'Bonus Borrows'
BONUS_ALL_VIDEO = 'All Video'
BONUS_ALL_TITLES = 'All Titles'
# A bonus title's overlay badge. Borrowing one doesn't change the monthly count (human-tested in Kodi).
BONUS_OVERLAY = 'Bonus Borrow'

HEADERS = {
    "apollographql-client-name": "hoopla-www",
    "apollographql-client-version": "5.12.5",
    "hoopla-version": "5.12.5",
    "binge-pass-external-enabled": "true",
    "binge-pass-internal-enabled": "true",
    "traditional-manga-enabled": "true",
    "User-Agent": "Mozilla/5.0 (X11; Windows 11; rv:147.0) Gecko/20100101 Firefox/147.0",
    "device-model": "147.0",
    "device-version": "Firefox",
    "os": "Windows",
    "ws-api": "2.1",
    "Host": "patron-api-gateway.hoopladigital.com",
    "Origin": "https://www.hoopladigital.com",
    "Referer": "https://www.hoopladigital.com/",
    "Accept": "application/json, */*",
}
# Extra headers the web client's REST client sends to /core.
CORE_HEADERS = {
    "app": "WWW",
    "Content-Type": "application/x-www-form-urlencoded",
}

KINDS_QUERY = """query GetKindsQuery {
  kinds {
    id
    name
    singular
    plural
    enabled
    __typename
  }
}"""

GENRES_QUERY = """query GetGenresListQuery($kindId: ID!, $audience: GenresAudience) {
  genres(criteria: {kindId: $kindId, type: TOP_LEVEL, audience: $audience}) {
    isParent
    id
    name
    __typename
  }
}"""

BORROWED_QUERY = """query GetBorrowedTitlesQuery($criteria: CurrentlyBorrowedCriteriaInput) {
  currentlyBorrowed(criteria: $criteria) {
    artKey
    bingePassType
    bundledContent {
      id
      artKey
      kind {
        name
        __typename
      }
      primaryArtist {
        id
        name
        __typename
      }
      title
      tracks {
        contentId
        id
        mediaKey
        name
        seconds
        segmentNumber
        __typename
      }
      __typename
    }
    childrens
    circulation {
      id
      dueDate
      __typename
    }
    episodes {
      artKey
      circulation {
        dueDate
        __typename
      }
      episode
      id
      status
      synopsis
      title
      __typename
    }
    externalCatalogUrl
    id
    issueNumberDescription
    kind {
      name
      __typename
    }
    licenseType
    parentalAdvisory
    playbackPosition {
      percentComplete
      __typename
    }
    primaryArtist {
      id
      name
      __typename
    }
    status
    title
    titleId
    tracks {
      contentId
      id
      mediaKey
      name
      seconds
      segmentNumber
      __typename
    }
    __typename
  }
}"""

REMAINING_BORROWS_QUERY = """query GetRemainingBorrowsQuery {
  remainingBorrows {
    borrowsRemaining
    borrowsRemainingMessage
    flexBorrowsRemaining
    flexBorrowsRemainingMessage
    instantBorrowsRemaining
    instantBorrowsRemainingMessage
    __typename
  }
}"""

BINGEPASS_QUERY = """query GetBingePassTitlesQuery($audience: SearchAudience, $sort: Sort = POPULARITY, $pagination: PaginationInput) {
  search(
    criteria: {kindId: 11, audience: $audience, pagination: $pagination}
    sort: $sort
  ) {
    found
    hits {
      id
      title
      artKey
      kind {
        name
        __typename
      }
      overlay {
        name
        backColor
        foreColor
        __typename
      }
      licenseType
      status
      bingePassType
      genres {
        name
        kind {
          name
          __typename
        }
        __typename
      }
      __typename
    }
    __typename
  }
}"""

# Our selection set for title lists, spliced into the list queries below where the web client
# uses its TitleListItemFragment (every field here also appears in the web client's documents).
HITS = """
      id
      titleId
      title
      artKey
      kind {
        name
        __typename
      }
      licenseType
      overlay {
        name
        backColor
        foreColor
        __typename
      }
      parentalAdvisory
      primaryArtist {
        name
        __typename
      }
      releaseDate
      year
      seconds
      rating
      genres {
        id
        name
        __typename
      }
      status
      subtitle
      synopsis
      series {
        id
        name
        __typename
      }
      titleRating {
        totalCount
        weightedAverage
        __typename
      }
      patronRating {
        stars
        __typename
      }
      __typename"""

# The web client's GetFilterSearchQuery, which takes any SearchCriteria (q, genreId, collectionId, seriesId, ...).
SEARCH_QUERY = """query GetFilterSearchQuery($criteria: SearchCriteria!, $sort: Sort) {
  search(criteria: $criteria, sort: $sort) {
    found
    hits {""" + HITS + """
    }
    __typename
  }
}"""

FEATURED_QUERY = """query GetFeaturedQuery($kindId: ID!, $audience: SearchAudience!) {
  featured: search(
    criteria: {kindId: $kindId, featured: true, availability: AVAILABLE_NOW, pagination: {page: 1, pageSize: 25}, audience: $audience, facets: [NONE]}
  ) {
    hits {""" + HITS + """
    }
    __typename
  }
}"""

RECENT_QUERY = """query GetRecentQuery($kindId: ID!, $availability: AvailabilityFilter, $audience: SearchAudience!) {
  recent: search(
    criteria: {kindId: $kindId, availability: $availability, pagination: {page: 1, pageSize: 25}, audience: $audience, facets: [NONE]}
    sort: RELEASE_DATE
  ) {
    hits {""" + HITS + """
    }
    __typename
  }
}"""


def _popular_query(operation, alias, borrow_type):
    """The web client's GetPopularTitlesQuery / GetPopularInstantQuery / GetPopularFlexQuery."""
    extra = f', borrowType: {borrow_type}' if borrow_type else ''
    return f"""query {operation}($audience: SearchAudience!, $kindId: ID!) {{
  {alias}: popularTitles(
    criteria: {{audience: $audience, availability: AVAILABLE_NOW, kindId: $kindId{extra}}}
  ) {{
    algorithm
    hits {{""" + HITS + """
    }
    __typename
  }
}"""


# borrow type -> (operation, alias, query)
POPULAR_QUERIES = {
    borrow_type: (operation, alias, _popular_query(operation, alias, hoopla_type))
    for borrow_type, hoopla_type, operation, alias in [
        (None, None, 'GetPopularTitlesQuery', 'popular'),
        (INSTANT, 'PPU', 'GetPopularInstantQuery', 'popularInstant'),
        (FLEX, 'EST', 'GetPopularFlexQuery', 'popularFlex'),
    ]
}

COLLECTIONS_QUERY = """query GetCollectionsListQuery($kindId: ID!, $audience: CollectionsAudience!) {
  library: collections(
    criteria: {kindId: $kindId, audience: $audience, type: PRIVATE, pagination: {page: 1, pageSize: 10}}
  ) {
    id
    name
    __typename
  }
  featured: collections(
    criteria: {kindId: $kindId, audience: $audience, type: FEATURED, pagination: {page: 1, pageSize: 10}}
  ) {
    id
    name
    __typename
  }
  all: collections(
    criteria: {kindId: $kindId, audience: $audience, type: PUBLIC, pagination: {page: 1, pageSize: 43}}
  ) {
    id
    name
    __typename
  }
}"""

RELATED_QUERY = """query GetRelatedTitlesForTitle($titleId: ID!, $hooplaDeviceId: String!, $audience: SearchAudience!) {
  relatedTitlesForTitle(
    criteria: {titleId: $titleId, hooplaDeviceId: $hooplaDeviceId, audience: $audience}
  ) {
    algorithm
    collectionTitle
    hits {""" + HITS + """
    }
    __typename
  }
}"""

HISTORY_QUERY = """query GetPatronHistoryQuery($historyAudience: HistoryAudience!, $page: ConstrainNumberMIN1, $pageSize: ConstrainNumberMIN1) {
  history(
    criteria: {pagination: {page: $page, pageSize: $pageSize}, audience: $historyAudience}
  ) {
    id
    borrowedDate
    episode {
      title
      __typename
    }
    title {""" + HITS + """
    }
    licenseType
    __typename
  }
}"""

# Trimmed from the web client's GetFetchTitleDetailQuery (the full one also pulls related titles, reviews, ...).
TITLE_QUERY = """query GetFetchTitleDetailQuery($id: ID!, $includeDeleted: Boolean) {
  title(criteria: {id: $id, includeDeleted: $includeDeleted}) {
    id
    title
    artKey
    kind {
      name
      __typename
    }
    synopsis
    year
    seconds
    rating
    status
    licenseType
    lendingMessage
    mediaKey
    releaseDate
    genres {
      id
      name
      __typename
    }
    primaryArtist {
      id
      name
      __typename
    }
    directors {
      id
      name
      __typename
    }
    circulation {
      id
      dueDate
      patron {
        id
        __typename
      }
      __typename
    }
    episodes {
      id
      episode
      title
      synopsis
      artKey
      seconds
      status
      mediaKey
      circulation {
        id
        dueDate
        patron {
          id
          __typename
        }
        __typename
      }
      __typename
    }
    playbackPosition {
      percentComplete
      __typename
    }
    overlay {
      name
      __typename
    }
    series {
      id
      name
      __typename
    }
    patronRating {
      stars
      __typename
    }
    titleRating {
      totalCount
      weightedAverage
      __typename
    }
    bingePassType
    externalCatalogUrl
    bundledContent {
      id
      artKey
      episode
      episodeTitle
      kind {
        name
        __typename
      }
      mediaKey
      rating
      season
      seconds
      synopsis
      title
      titleId
      year
      __typename
    }
    __typename
  }
}"""

# The web client's GetFetchBorrowCirculationQuery, plus the patron id that playback needs.
CIRCULATION_QUERY = """query GetFetchBorrowCirculationQuery($id: ID!) {
  title(criteria: {id: $id}) {
    id
    circulation {
      id
      patron {
        id
        __typename
      }
      __typename
    }
    __typename
  }
}"""

# Trimmed from the web client's GetPatronQuery.
PATRON_QUERY = """query GetPatronQuery {
  patron {
    id
    hooplaUserId
    __typename
  }
}"""


def _obj(d, key):
    return d.get(key) or {}


def _parse_time(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace('Z', '+00:00'))
    except ValueError:
        return None


def _art(art_key):
    return ART_URL.format(art_key) if art_key else None


def _is_borrowed(item):
    return bool(item.get('circulation')) or item.get('status') == 'BORROWED'


def _is_borrowable(item):
    return item.get('status') == 'BORROW'


def _year(t):
    y = str(t.get('year') or t.get('releaseDate') or '')[:4]
    return int(y) if y.isdigit() else None


def _season(t):
    """Hoopla has no season field: a TV title is named like "Show - Season 6". The first season is
    often just "Show", which has no number, so that gives None."""
    m = re.search(r'\b(?:Season|Series|Volume|Vol\.?)\s*(\d+)', t.get('title') or '', re.IGNORECASE)
    return int(m.group(1)) if m else None


def _mpaa(rating):
    """Hoopla writes ratings compactly (PG13, NC17, TVMA, TVY7); NRA means none given."""
    if not rating or rating == 'NRA':
        return None
    if rating.startswith('TV') and len(rating) > 2 and rating[2] != '-':
        return 'TV-' + rating[2:]
    return {'PG13': 'PG-13', 'NC17': 'NC-17'}.get(rating, rating)


def _names(items):
    return [i['name'] for i in items or [] if i.get('name')]


def _episode(e):
    return Episode(
        id=str(e.get('id') or ''),
        number=int(e.get('episode') or 0),
        title=e.get('title') or '',
        synopsis=e.get('synopsis') or '',
        image_url=_art(e.get('artKey')),
        borrowed=_is_borrowed(e),
        due=_parse_time(_obj(e, 'circulation').get('dueDate')),
        borrowable=_is_borrowable(e),
        duration=int(e.get('seconds') or 0),
    )


def _title(t):
    episodes = [_episode(e) for e in t.get('episodes') or []]
    return Title(
        id=str(t.get('titleId') or t.get('id') or ''),
        title=t.get('title') or '',
        kind=_obj(t, 'kind').get('name'),
        image_url=_art(t.get('artKey')),
        artist=_obj(t, 'primaryArtist').get('name'),
        borrowed=_is_borrowed(t) or any(e.borrowed for e in episodes),
        due=_parse_time(_obj(t, 'circulation').get('dueDate')),
        badge=_obj(t, 'overlay').get('name'),
        episodes=episodes,
        borrowable=_is_borrowable(t),
        synopsis=t.get('synopsis') or '',
        year=_year(t),
        duration=int(t.get('seconds') or 0),
        rating=_mpaa(t.get('rating')),
        genres=_names(t.get('genres')),
        bonus=_obj(t, 'overlay').get('name') == BONUS_OVERLAY,
        content_kinds=sorted({_obj(g, 'kind').get('name') for g in t.get('genres') or []} - {None}),
        directors=_names(t.get('directors')),
        lending_message=t.get('lendingMessage') or '',
        borrow_type=BORROW_TYPES.get(t.get('licenseType')),
        series_id=str(_obj(t, 'series').get('id') or '') or None,
        series_name=_obj(t, 'series').get('name'),
        rating_average=float(_obj(t, 'titleRating').get('weightedAverage') or 0),
        rating_count=int(_obj(t, 'titleRating').get('totalCount') or 0),
        user_stars=_obj(t, 'patronRating').get('stars') or None,
        season=_season(t),
        bingepass_type=BINGEPASS_TYPES.get(t.get('bingePassType')),
        included=_included(t, borrowed=bool(t.get('circulation')), borrowable=_is_borrowable(t)),
    )


def _included(pass_data, borrowed, borrowable):
    """An INCLUDED BingePass's bundled content, grouped into its movies and TV seasons (in bundle order)."""
    groups = {}
    for c in pass_data.get('bundledContent') or []:
        groups.setdefault(str(c.get('titleId') or c.get('id')), []).append(c)
    titles = []
    for title_id, items in groups.items():
        first = items[0]
        kind = _obj(first, 'kind').get('name')
        t = Title(id=title_id, title=first.get('title') or '', kind=kind, image_url=_art(first.get('artKey')),
                  year=_year(first), rating=_mpaa(first.get('rating')), borrowed=borrowed, borrowable=borrowable)
        if kind == TELEVISION:
            season = str(first.get('season') or '')
            t.season = int(season) if season.isdigit() else _season(first)
            t.episodes = [Episode(
                id=str(c.get('id') or ''),
                number=int(c.get('episode') or 0),
                title=re.sub(r'^\d+-', '', c.get('episodeTitle') or c.get('title') or ''),  # "01-All About Hats"
                synopsis=c.get('synopsis') or '',
                image_url=_art(c.get('artKey')),
                borrowed=borrowed,
                borrowable=borrowable,
                duration=int(c.get('seconds') or 0),
            ) for c in items]
        elif kind == MOVIE:
            t.synopsis = first.get('synopsis') or ''
            t.duration = int(first.get('seconds') or 0)
        titles.append(t)
    return titles


def _history_item(h):
    return HistoryItem(
        title=_title(_obj(h, 'title')),
        borrowed_date=_parse_time(h.get('borrowedDate')),
        episode_title=_obj(h, 'episode').get('title'),
    )


def _page_size(page_size):
    return min(page_size or MAX_PAGE_SIZE, MAX_PAGE_SIZE)


def _with_kind(criteria, kind_id):
    return dict(criteria, kindId=str(kind_id)) if kind_id else criteria


def _page(data, page, page_size):
    search = _obj(data, 'search')
    return TitlePage(
        titles=[_title(h) for h in search.get('hits') or []],
        total=int(search.get('found') or 0),
        page=page,
        page_size=page_size,
    )


def _genre(g):
    return Genre(id=str(g.get('id') or ''), name=g.get('name') or '')


def _message(res):
    """The "message" a JSON error or confirmation body carries, if any."""
    try:
        return res.json().get('message')
    except (ValueError, AttributeError):
        return None


def _json(res, what):
    try:
        return res.json()
    except ValueError as e:
        raise LibraryError(f'{what}: response was not JSON') from e


class HooplaGraphQLDAO(LibraryDAO):
    def __init__(self, session_token=None, device_id=None, sess=None):
        super().__init__(session_token, device_id)
        self.sess = sess if sess is not None else requests.Session()
        self.sess.headers.update(HEADERS)
        self._apply_token(session_token)

    def _apply_token(self, token):
        self.session_token = token
        if token:
            self.sess.headers['Authorization'] = 'Bearer ' + token
        else:
            self.sess.headers.pop('Authorization', None)

    def _send(self, method, url, what, auth_codes=(401, 403), **kwargs):
        """Every HTTP request goes through here. Returns the 2xx response; an HTTP status in auth_codes
        raises AuthError, and any other failure LibraryError."""
        try:
            res = self.sess.request(method, url, timeout=TIMEOUT, **kwargs)
        except requests.RequestException as e:
            raise LibraryError(f'{what} request failed: {e}') from e
        if res.status_code in auth_codes:
            raise AuthError(f'{what}: {res.status_code} {res.reason}')
        if not 200 <= res.status_code < 300:
            raise LibraryError(f'{what}: {_message(res) or f"{res.status_code} {res.reason}"}')
        return res

    def _core(self, method, path, what, patron_id, form=None):
        """A call to the gateway's /core REST API, with the headers the web client sends there."""
        headers = dict(CORE_HEADERS, **{'patron-id': patron_id})
        return self._send(method, GATEWAY + path, what, headers=headers, data=form)

    def _query(self, operation, query, variables=None):
        res = self._send('POST', GRAPHQL_URL, operation, json={
            'operationName': operation,
            'query': query,
            'variables': variables or {},
        })
        body = _json(res, operation)
        errors = body.get('errors') or []
        if errors:
            msg = f'{operation}: ' + '; '.join(str(e.get('message', 'unknown error')) for e in errors)
            if any(_obj(e, 'extensions').get('code') == 'UNAUTHENTICATED' for e in errors):
                raise AuthError(msg)
            raise LibraryError(msg)
        return body.get('data') or {}

    def login(self, username, password):
        # Form-encoded; a JSON body gets a 500.
        res = self._send('POST', TOKENS_URL, 'Login', auth_codes=(400, 401, 403),
                         data={'username': username, 'password': password})
        body = _json(res, 'Login')

        status = str(body.get('tokenStatus') or 'SUCCESS').upper()
        token = body.get('token')
        if status != 'SUCCESS' or not token:
            raise AuthError(f'Login failed: {status}')
        self._apply_token(token)
        return token

    def kinds(self):
        data = self._query('GetKindsQuery', KINDS_QUERY)
        return [
            Kind(id=str(k['id']),
                 name=(k.get('name') or '').upper(),
                 label=k.get('plural') or k.get('singular') or k.get('name') or '')
            for k in data.get('kinds') or []
            if k.get('id') and k.get('enabled', True)
        ]

    def genres(self, kind_id):
        data = self._query('GetGenresListQuery', GENRES_QUERY, {'kindId': str(kind_id)})
        return [_genre(g) for g in data.get('genres') or []]

    def borrowed(self):
        data = self._query('GetBorrowedTitlesQuery', BORROWED_QUERY, {'criteria': {}})
        return [_title(t) for t in data.get('currentlyBorrowed') or []]

    def borrow_limits(self):
        r = _obj(self._query('GetRemainingBorrowsQuery', REMAINING_BORROWS_QUERY), 'remainingBorrows')
        return BorrowLimits(
            message=r.get('borrowsRemainingMessage') or '',
            instant_message=r.get('instantBorrowsRemainingMessage') or '',
            flex_remaining=r.get('flexBorrowsRemaining'),
            flex_message=r.get('flexBorrowsRemainingMessage') or '',
        )

    def bingepass_titles(self, page=1, page_size=None):
        page_size = _page_size(page_size)
        data = self._query('GetBingePassTitlesQuery', BINGEPASS_QUERY, {
            'pagination': {'page': page, 'pageSize': page_size},
            'sort': 'A_Z',
        })
        return _page(data, page, page_size)

    def _search(self, criteria, sort, page, page_size):
        page_size = _page_size(page_size)
        criteria = dict(criteria, pagination={'page': page, 'pageSize': page_size})
        return _page(self._query('GetFilterSearchQuery', SEARCH_QUERY, {'criteria': criteria, 'sort': sort}),
                     page, page_size)

    def genre_titles(self, genre_id, page=1, page_size=None, kind_id=None):
        return self._search(_with_kind({'genreId': str(genre_id)}, kind_id), 'A_Z', page, page_size)

    def top_rated(self, kind_id, genre_id=None, page=1, page_size=None):
        criteria = {'kindId': str(kind_id), 'audience': AUDIENCE, 'availability': 'AVAILABLE_NOW',
                    'stars': TOP_RATED_STARS}
        if genre_id:
            criteria['genreId'] = str(genre_id)
        return self._search(criteria, 'POPULARITY', page, page_size)

    def search(self, query, kind_id, page=1, page_size=None):
        return self._search({'q': query, 'kindId': str(kind_id), 'audience': AUDIENCE}, 'RELEVANCE', page, page_size)

    def collection_titles(self, collection_id, page=1, page_size=None, kind_id=None):
        return self._search(_with_kind({'collectionId': str(collection_id), 'audience': AUDIENCE}, kind_id),
                            'A_Z', page, page_size)

    def series_titles(self, series_id, page=1, page_size=None):
        return self._search({'seriesId': str(series_id), 'audience': AUDIENCE}, 'A_Z', page, page_size)

    def featured_titles(self, kind_id):
        data = self._query('GetFeaturedQuery', FEATURED_QUERY, {'kindId': str(kind_id), 'audience': AUDIENCE})
        return [_title(h) for h in _obj(data, 'featured').get('hits') or []]

    def recent_titles(self, kind_id):
        data = self._query('GetRecentQuery', RECENT_QUERY,
                           {'kindId': str(kind_id), 'availability': 'AVAILABLE_NOW', 'audience': AUDIENCE})
        return [_title(h) for h in _obj(data, 'recent').get('hits') or []]

    def popular_titles(self, kind_id, borrow_type=None):
        operation, alias, query = POPULAR_QUERIES[borrow_type]
        data = self._query(operation, query, {'kindId': str(kind_id), 'audience': AUDIENCE})
        return [_title(h) for h in _obj(data, alias).get('hits') or []]

    def collections(self, kind_id):
        data = self._query('GetCollectionsListQuery', COLLECTIONS_QUERY, {'kindId': str(kind_id), 'audience': AUDIENCE})
        seen, result = set(), []
        for group in ('library', 'featured', 'all'):
            for c in data.get(group) or []:
                if c.get('id') and str(c['id']) not in seen:
                    seen.add(str(c['id']))
                    result.append(Collection(id=str(c['id']), name=c.get('name') or ''))
        return result

    def related_titles(self, title_id):
        if not self.device_id:
            raise LibraryError('Related titles need a device id')
        data = self._query('GetRelatedTitlesForTitle', RELATED_QUERY,
                           {'titleId': str(title_id), 'hooplaDeviceId': self.device_id, 'audience': AUDIENCE})
        return [_title(h) for h in _obj(data, 'relatedTitlesForTitle').get('hits') or []]

    def bonus_titles(self, kind_ids):
        all_video, per_kind = None, []
        for kind_id in kind_ids:
            for c in self.collections(kind_id):
                if not c.name.startswith(BONUS_PREFIX):
                    continue
                if BONUS_ALL_VIDEO in c.name:
                    all_video = c.id
                elif BONUS_ALL_TITLES not in c.name:  # mostly books; the per-kind ones cover video
                    per_kind.append((c.id, kind_id))
        sources = [(all_video, None)] if all_video else per_kind
        titles, seen = [], set()
        for collection_id, kind_id in sources:
            for t in self.collection_titles(collection_id, page_size=MAX_PAGE_SIZE, kind_id=kind_id).titles:
                if t.id not in seen:
                    seen.add(t.id)
                    titles.append(t)
        return titles

    def history(self, page=1, page_size=50):
        data = self._query('GetPatronHistoryQuery', HISTORY_QUERY,
                           {'historyAudience': AUDIENCE, 'page': page, 'pageSize': page_size})
        return [_history_item(h) for h in data.get('history') or []]

    def _title_data(self, title_id):
        data = self._query('GetFetchTitleDetailQuery', TITLE_QUERY, {'id': str(title_id), 'includeDeleted': False})
        if not data.get('title'):
            raise LibraryError(f'Title {title_id} not found')
        return data['title']

    def title(self, title_id):
        return _title(self._title_data(title_id))

    def stream(self, title_id, episode_id=None, bingepass_id=None):
        item = self._title_data(title_id)
        if episode_id is not None:
            item = next((e for e in item.get('episodes') or [] if str(e.get('id')) == str(episode_id)), None)
            if item is None:
                raise LibraryError(f'Episode {episode_id} not found')
        circ = _obj(item, 'circulation')
        if not circ and bingepass_id:
            # Content reached through a borrowed BingePass has no loan of its own; it plays under the pass's.
            data = self._query('GetFetchBorrowCirculationQuery', CIRCULATION_QUERY, {'id': str(bingepass_id)})
            circ = _obj(_obj(data, 'title'), 'circulation')
        patron_id = _obj(circ, 'patron').get('id')
        media_key = item.get('mediaKey')
        if not circ.get('id') or not patron_id:
            raise NotBorrowedError('This title is not borrowed')
        if not media_key:
            raise LibraryError('This title has no video to play')

        # A DRMtoday "upfront" JWT scoped to this rental; the license server checks it instead of calling Hoopla.
        res = self._send('GET', f'{GATEWAY}/license/castlabs/upfront-auth-tokens/{media_key}/{patron_id}/{circ["id"]}',
                         'Playback authorization')
        token = res.text.strip().strip('"')
        custom_data = json.dumps({'userId': str(patron_id), 'sessionId': str(circ['id']), 'merchant': 'hoopla'})
        return Stream(
            url=DASH_URL.format(media_key),
            manifest_type='mpd',
            drm=WIDEVINE,
            license_url=DRMTODAY_WIDEVINE_URL,
            license_headers={
                'x-dt-auth-token': token,
                'x-dt-custom-data': base64.b64encode(custom_data.encode()).decode(),
                'Content-Type': 'application/octet-stream',
            },
        )

    def _patron(self):
        p = _obj(self._query('GetPatronQuery', PATRON_QUERY), 'patron')
        if not p.get('id') or not p.get('hooplaUserId'):
            raise LibraryError('Could not load your library account')
        return str(p['hooplaUserId']), str(p['id'])

    def borrow(self, item_id):
        user_id, patron_id = self._patron()
        res = self._core('POST', f'/core/v2/users/{user_id}/patrons/{patron_id}/borrowed-titles/{item_id}'
                                 '?returnBorrowedTitles=true', 'Borrow', patron_id)
        return _message(res) or 'Borrowed'

    def rate(self, title_id, stars):
        if not 1 <= int(stars) <= 5:
            raise ValueError('stars must be 1-5')
        _, patron_id = self._patron()
        self._core('POST', f'/core/titles/{title_id}/patron-ratings', 'Rating', patron_id, form={'stars': int(stars)})

    def return_item(self, item_id):
        user_id, patron_id = self._patron()
        self._core('DELETE', f'/core/users/{user_id}/patrons/{patron_id}/borrowed-titles/{item_id}', 'Return',
                   patron_id)
