#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import re
import sys
import uuid
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode

import xbmc
import xbmcaddon
import xbmcgui
import xbmcplugin

import dao
from hoopla_graphql import HooplaGraphQLDAO as BackendDAO

PLUGIN_BASE = ''
HANDLE = -1
DO_CACHE = True # set me to True when not debugging....
VIDEO_KINDS = (dao.MOVIE, dao.TELEVISION)
PAGE_SIZE = 150  # the most Hoopla returns per page; fewer pages means fewer waits
HISTORY_PAGE_SIZE = 50  # history's own limit is unknown, and a short page is how we detect the end
NOT_BORROWED = object()
ROWS = {  # row -> (label, DAO call)
    'featured': ('Featured', lambda d, kind: d.featured_titles(kind)),
    'popular': ('Popular', lambda d, kind: d.popular_titles(kind)),
    'recent': ('Recently added', lambda d, kind: d.recent_titles(kind)),
    'instant': ('Popular Instant', lambda d, kind: d.popular_titles(kind, dao.INSTANT)),
    'flex': ('Popular Flex', lambda d, kind: d.popular_titles(kind, dao.FLEX)),
}

addon = xbmcaddon.Addon('plugin.video.hoopla')

def log(txt, *args, level=xbmc.LOGINFO):
    xbmc.log('hoopla : ' + txt.format(*args), level=level)

def plugin_url(action, **params):
    return f'{PLUGIN_BASE}?' + urlencode(dict(action=action, **params))

def notify(msg):
    xbmcgui.Dialog().notification('Hoopla', msg, xbmcgui.NOTIFICATION_INFO)

def credentials():
    return addon.getSetting('username'), addon.getSetting('password')

def device_id():
    """A random id for this Kodi install, created on first use and kept in the (hidden) addon settings."""
    value = addon.getSetting('device_id')
    if not value:
        value = str(uuid.uuid4())
        addon.setSetting('device_id', value)
    return value

# Log events, never credentials or tokens: Kodi logs get shared when people ask for help.
def do_login(d):
    username, password = credentials()
    if not username or not password:
        log('Login: no username/password set, opening settings')
        addon.openSettings()
        username, password = credentials()
        if not username or not password:
            log('Login: still no username/password, giving up', level=xbmc.LOGWARNING)
            return False
    log('Logging in')
    addon.setSetting('authorization', d.login(username, password))
    log('Login OK')
    return True

def dao_call(fn):
    d = BackendDAO(session_token=addon.getSetting('authorization') or None, device_id=device_id())
    try:
        try:
            return fn(d)
        except dao.AuthError:
            log('Session rejected, logging in')
            addon.setSetting('authorization', '')
            if not do_login(d):
                return None
            return fn(d)
    except dao.AuthError as e:
        log('Login failed: {}', e, level=xbmc.LOGERROR)
        addon.setSetting('authorization', '')
        xbmcgui.Dialog().ok('Login Failed', str(e))
    except dao.LibraryError as e:
        log('API failure: {}', e, level=xbmc.LOGERROR)
        xbmcgui.Dialog().ok('Hoopla API Failure', str(e))
    return None

# Sort modes: the first method listed is the one Kodi starts with.
MENU = 'menu'        # fixed menu order
NAMES = 'names'      # folders of genres/collections: A-Z
AZ = 'az'            # titles with no meaningful order of their own: A-Z
RANKED = 'ranked'    # titles whose order means something (popularity, relevance, date): keep it
EPISODES = 'episodes'

def sort_methods(sort):
    x = xbmcplugin
    title_sorts = [x.SORT_METHOD_VIDEO_YEAR, x.SORT_METHOD_VIDEO_RATING, x.SORT_METHOD_DURATION]
    return {
        MENU: [x.SORT_METHOD_UNSORTED],
        NAMES: [x.SORT_METHOD_LABEL_IGNORE_THE, x.SORT_METHOD_UNSORTED],
        AZ: [x.SORT_METHOD_TITLE_IGNORE_THE] + title_sorts + [x.SORT_METHOD_UNSORTED],
        RANKED: [x.SORT_METHOD_UNSORTED, x.SORT_METHOD_TITLE_IGNORE_THE] + title_sorts,
        EPISODES: [x.SORT_METHOD_EPISODE, x.SORT_METHOD_TITLE_IGNORE_THE, x.SORT_METHOD_UNSORTED],
    }[sort]

def end_directory(items, succeeded=True, content=None, cache=DO_CACHE, sort=MENU):
    xbmcplugin.addDirectoryItems(HANDLE, items, len(items))
    if content:
        xbmcplugin.setContent(HANDLE, content)
    for method in sort_methods(sort):
        xbmcplugin.addSortMethod(HANDLE, method)
    xbmcplugin.endOfDirectory(HANDLE, succeeded=succeeded, cacheToDisc=cache)

def content_for(titles):
    """Kodi's content type, which decides the views and metadata a skin shows."""
    kinds = {t.kind for t in titles}
    return {frozenset([dao.MOVIE]): 'movies', frozenset([dao.TELEVISION]): 'tvshows'}.get(frozenset(kinds), 'videos')

def folder_item(label):
    item = xbmcgui.ListItem(label=label)
    item.getVideoInfoTag().setTitle(label)
    return item

def next_page(url):
    item = folder_item('Next page')
    item.setProperty('SpecialSort', 'bottom')  # stays last whatever the sort
    return url, item, True

def set_art(item, image_url):
    if image_url:
        item.setArt({'thumb': image_url, 'poster': image_url})

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

def return_menu(item_id, label):
    return ('Return', f'RunPlugin({plugin_url("return", id=item_id, label=label)})')

def rate_menu(t, label='Rate...'):
    return (label, f'RunPlugin({plugin_url("rate", title=t.id, label=t.title, stars=t.user_stars or 0)})')

def title_menu(t, can_return=True):
    menu = []
    if can_return and t.borrowed and t.kind == dao.MOVIE:
        menu.append(return_menu(t.id, t.title))
    if t.series_id:
        url = plugin_url('series', series=t.series_id, page=1)
        menu.append((f'Series: {t.series_name}' if t.series_name else 'Series', f'Container.Update({url})'))
    menu.append(('More like this', f'Container.Update({plugin_url("related", title=t.id)})'))
    menu.append(rate_menu(t))
    return menu

def title_item(t, label=None):
    label = label or t.title
    if t.borrow_type == dao.FLEX:
        label = f'[Flex] {label}'
    item = xbmcgui.ListItem(label=f'[{t.badge}] {label}' if t.badge else label)
    info = item.getVideoInfoTag()
    info.setTitle(t.title)
    info.setMediaType({dao.MOVIE: 'movie', dao.TELEVISION: 'tvshow'}.get(t.kind, 'video'))
    if t.artist:
        info.setArtists([t.artist])
    info.setPlot('\n\n'.join(s for s in (t.synopsis, due_text(t.due)) if s))
    if t.year:
        info.setYear(t.year)
    if t.duration:
        info.setDuration(t.duration)
    if t.genres:
        info.setGenres(t.genres)
    if t.directors:
        info.setDirectors(t.directors)
    if t.rating:
        info.setMpaa(t.rating)
    if t.rating_count:
        info.setRating(t.rating_average * 2, t.rating_count, 'hoopla', True)  # Kodi ratings are out of 10
    if t.user_stars:
        info.setUserRating(t.user_stars * 2)
    set_art(item, t.image_url)
    return item

def episode_item(t, e):
    label = e.title if e.borrowed else f'{e.title} (not borrowed)'
    item = xbmcgui.ListItem(label=label)
    info = item.getVideoInfoTag()
    info.setTitle(e.title)
    info.setTvShowTitle(t.title)
    info.setMediaType('episode')
    # Kodi labels episodes "SxEE" and shows -1 for an unset season; an unnumbered title is the first season.
    info.setSeason(t.season or 1)
    if e.number:
        info.setEpisode(e.number)
        info.setSortEpisode(e.number)
    if e.duration:
        info.setDuration(e.duration)
    info.setPlot('\n\n'.join(s for s in (e.synopsis, due_text(e.due)) if s))
    set_art(item, e.image_url or t.image_url)
    # Ratings belong to titles, and an episode is content within its season's title, so rate the season.
    menu = [rate_menu(t, 'Rate season...')]
    if e.borrowed:
        menu.insert(0, return_menu(e.id, e.title))
    item.addContextMenuItems(menu)
    return item

def play_entry(item, borrowed, label, **params):
    """A (url, item, is_folder) entry for something to watch. A borrowed item plays directly. Anything
    else runs the borrow dialog first, and only then starts playback; cancelling a playable item's
    resolve would make Kodi show "Playback failed"."""
    if borrowed:
        item.setProperty('IsPlayable', 'true')
        return plugin_url('play', **params), item, False
    return plugin_url('borrow', label=label, **params), item, False

def catalog_entry(t, label=None):
    """A (url, item, is_folder) entry for a title from any listing."""
    item = title_item(t, label)
    if t.kind in VIDEO_KINDS:
        item.addContextMenuItems(title_menu(t))
    if t.kind == dao.TELEVISION:
        return plugin_url('episodes', title=t.id), item, True
    if t.kind == dao.MOVIE:
        return play_entry(item, t.borrowed, t.title, title=t.id)
    if t.kind == dao.BINGEPASS:
        return plugin_url('pass', title=t.id), item, True
    return plugin_url('unsupported'), item, False

def playable(titles):
    """Hide titles this addon can't play (ebooks, audiobooks, BingePasses, ...) that mixed lists contain."""
    return [t for t in titles or [] if t.kind in VIDEO_KINDS]

def included_passes(titles):
    """BingePasses with something to watch here. Partner passes unlock another website, and a pass
    whose genres are all book/comic/music genres bundles nothing Kodi can play."""
    return [t for t in titles or [] if t.kind == dao.BINGEPASS and t.bingepass_type != dao.BINGEPASS_PARTNER
            and (not t.content_kinds or any(k in VIDEO_KINDS for k in t.content_kinds))]

def list_titles(titles, sort, cache=DO_CACHE, keep=playable):
    shown = keep(titles)
    end_directory([catalog_entry(t) for t in shown], succeeded=titles is not None, content=content_for(shown),
                  cache=cache, sort=sort)

def list_page(result, next_url, sort, cache=DO_CACHE, keep=playable):
    shown = keep(result.titles) if result else []
    items = [catalog_entry(t) for t in shown]
    if result and result.has_more:
        items.append(next_page(next_url))
    end_directory(items, succeeded=result is not None, content=content_for(shown), cache=cache, sort=sort)

def list_main():
    result = dao_call(lambda d: (d.borrow_limits(), d.kinds()))
    limits, kinds = result if result else (None, [])
    # Libraries without Flex borrowing report no Flex allowance; pass that down so the kind menu needn't ask.
    flex = int(bool(limits and limits.flex_remaining is not None))

    label = f'Borrowed - {limits.message}' if limits and limits.message else 'Borrowed'
    items = [
        (plugin_url('borrowed'), folder_item(label), True),
        (plugin_url('history', page=1), folder_item('Borrowing history'), True),
        (plugin_url('bingepass', page=1), folder_item('BingePass'), True),
    ]
    for k in kinds:
        if k.name in VIDEO_KINDS:
            items.append((plugin_url('kind', kind=k.id, label=k.label, flex=flex), folder_item(k.label), True))
    # Checked after the calls above, which log in on their own when they can.
    if not addon.getSetting('authorization'):
        items.append((plugin_url('login'), xbmcgui.ListItem('Log in'), False))
    end_directory(items, cache=False)

def list_kind(kind_id, label, flex):
    """The menu for one kind (Movies, Television). It makes no requests itself."""
    rows = [r for r in ROWS if r != 'flex' or flex]
    items = [(plugin_url('search', kind=kind_id, label=label), folder_item(f'Search {label}'), True)]
    items += [(plugin_url('row', row=r, kind=kind_id), folder_item(ROWS[r][0]), True) for r in rows]
    items += [
        (plugin_url('collections', kind=kind_id), folder_item('Collections'), True),
        (plugin_url('genres', kind=kind_id), folder_item('Genres'), True),
    ]
    end_directory(items)

def list_row(row, kind_id):
    _, fetch = ROWS[row]
    list_titles(dao_call(lambda d: fetch(d, kind_id)), RANKED)

def list_borrowed():
    titles = dao_call(lambda d: d.borrowed())
    # Borrowed BingePasses stay listed; they open as a folder of what they bundle.
    shown = playable(titles) + included_passes(titles)
    items = []
    for t in shown:
        due = loan_due(t)
        items.append(catalog_entry(t, f'{t.title} ({time_left(due)})' if due else None))
    end_directory(items, succeeded=titles is not None, content=content_for(shown), cache=False, sort=AZ)

def list_history(page):
    entries = dao_call(lambda d: d.history(page=page, page_size=HISTORY_PAGE_SIZE))
    items, shown = [], []
    for h in entries or []:
        if h.title.kind not in VIDEO_KINDS:
            continue
        shown.append(h.title)
        label = f'{h.title.title}: {h.episode_title}' if h.episode_title else h.title.title
        url, item, is_folder = catalog_entry(h.title, label)
        if h.borrowed_date:
            item.getVideoInfoTag().setPlot('\n\n'.join(
                s for s in (h.title.synopsis, f'Borrowed {h.borrowed_date.astimezone():%Y-%m-%d}') if s))
        items.append((url, item, is_folder))
    if entries and len(entries) == HISTORY_PAGE_SIZE:
        items.append(next_page(plugin_url('history', page=page + 1)))
    end_directory(items, succeeded=entries is not None, content=content_for(shown), cache=False, sort=RANKED)

def list_episodes(title_id):
    t = dao_call(lambda d: d.title(title_id))
    items = [play_entry(episode_item(t, e), e.borrowed, f'{t.title}: {e.title}', title=t.id, episode=e.id)
             for e in t.episodes] if t else []
    end_directory(items, succeeded=t is not None, content='episodes', cache=False, sort=EPISODES)

def list_bingepass(page):
    result = dao_call(lambda d: d.bingepass_titles(page=page, page_size=PAGE_SIZE))
    list_page(result, plugin_url('bingepass', page=page + 1), AZ, keep=included_passes)

def list_pass(pass_id, season_id=None):
    t = dao_call(lambda d: d.title(pass_id))
    if t is None:
        end_directory([], succeeded=False, cache=False)
        return
    if t.bingepass_type == dao.BINGEPASS_PARTNER:
        end_directory([], succeeded=False, cache=False)
        return
    included = sorted(playable(t.included), key=lambda x: (x.kind != dao.TELEVISION, x.season or 0, x.title))
    seasons = [x for x in included if x.kind == dao.TELEVISION]
    if season_id is None and len(included) == 1 and seasons:
        season_id = seasons[0].id
    if season_id is not None:
        season = next((x for x in seasons if x.id == season_id), None)
        items = [play_entry(episode_item(season, e), e.borrowed, f'{season.title}: {e.title}',
                            title=season.id, episode=e.id, bingepass=pass_id)
                 for e in season.episodes] if season else []
        end_directory(items, succeeded=season is not None, content='episodes', cache=False, sort=EPISODES)
        return
    if not included:
        xbmcgui.Dialog().ok(t.title, 'This BingePass has no movies or TV to play here.')
        end_directory([], succeeded=False, cache=False)
        return
    items = []
    for x in included:
        item = title_item(x)
        # No Return: these are "borrowed" only through the pass's loan.
        item.addContextMenuItems(title_menu(x, can_return=False))
        if x.kind == dao.TELEVISION:
            items.append((plugin_url('pass', title=pass_id, season=x.id), item, True))
        else:
            items.append(play_entry(item, x.borrowed, x.title, title=x.id, bingepass=pass_id))
    end_directory(items, content=content_for(included), cache=False, sort=RANKED)

def list_genres(kind_id):
    genres = dao_call(lambda d: d.genres(kind_id))
    # A flat list: every genre opens its titles, and a parent genre's titles include its sub-genres'.
    items = [(plugin_url('genre', genre=g.id, kind=kind_id, page=1), folder_item(g.name), True) for g in genres or []]
    end_directory(items, succeeded=genres is not None, sort=NAMES)

def list_genre(genre_id, kind_id, page):
    result = dao_call(lambda d: d.genre_titles(genre_id, page=page, page_size=PAGE_SIZE, kind_id=kind_id))
    list_page(result, plugin_url('genre', genre=genre_id, kind=kind_id, page=page + 1), AZ)

def list_collections(kind_id):
    collections = dao_call(lambda d: d.collections(kind_id))
    items = [(plugin_url('collection', collection=c.id, kind=kind_id, page=1), folder_item(c.name), True)
             for c in collections or []]
    end_directory(items, succeeded=collections is not None, sort=NAMES)

def list_collection(collection_id, kind_id, page):
    # Collections can mix kinds (e.g. "... | All Titles"); only ask for the kind this menu is about.
    result = dao_call(lambda d: d.collection_titles(collection_id, page=page, page_size=PAGE_SIZE, kind_id=kind_id))
    list_page(result, plugin_url('collection', collection=collection_id, kind=kind_id, page=page + 1), AZ)

def list_series(series_id, page):
    result = dao_call(lambda d: d.series_titles(series_id, page=page, page_size=PAGE_SIZE))
    list_page(result, plugin_url('series', series=series_id, page=page + 1), AZ)

def list_related(title_id):
    list_titles(dao_call(lambda d: d.related_titles(title_id)), RANKED)

def search(kind_id, label, query, page):
    if query is None:
        query = xbmcgui.Dialog().input(f'Search {label}')
        if not query:
            end_directory([], succeeded=False, cache=False)
            return
    result = dao_call(lambda d: d.search(query, kind_id, page=page, page_size=PAGE_SIZE))
    list_page(result, plugin_url('search', kind=kind_id, label=label, q=query, page=page + 1), RANKED, cache=False)

def rate(title_id, label, current):
    choices = [f'{"*" * n} ({n})' for n in range(5, 0, -1)]
    preselect = 5 - current if 1 <= current <= 5 else -1
    picked = xbmcgui.Dialog().select(f'Rate "{label}"', choices, preselect=preselect)
    if picked < 0:
        return
    stars = 5 - picked
    log('Rating "{}" ({}) {} of 5', label, title_id, stars)
    if dao_call(lambda d: d.rate(title_id, stars) or True):
        log('Rated "{}"', label)
        notify(f'Rated "{label}" {stars} of 5' if label else f'Rated {stars} of 5')
        xbmc.executebuiltin('Container.Refresh')

def force_login():
    log('Login requested from the menu')
    addon.setSetting('authorization', '')
    try:
        if do_login(BackendDAO(device_id=device_id())):
            notify('Logged in')
    except dao.LibraryError as e:
        log('Login failed: {}', e, level=xbmc.LOGERROR)
        xbmcgui.Dialog().ok('Login Failed', str(e))

def without_downloads(text):
    """Drop the library's sentences about downloading to mobile apps; that doesn't apply to Kodi."""
    sentences = re.split(r'(?<=[.!?])\s+', text or '')
    return ' '.join(s for s in sentences if 'download' not in s.lower()).strip()

def confirm_borrow(t, e):
    limits = dao_call(lambda d: d.borrow_limits())
    name = f'{t.title}: {e.title}' if e else t.title
    allowance = ''
    if limits:
        allowance = limits.flex_message if t.borrow_type == dao.FLEX else limits.instant_message
        allowance = allowance or limits.message
    lines = [name, allowance, without_downloads(t.lending_message)]
    return xbmcgui.Dialog().yesno('Borrow from Hoopla?', '\n'.join(s for s in lines if s),
                                  nolabel='Cancel', yeslabel='Borrow')

def borrow_for_play(title_id, episode_id, bingepass_id=None):
    """Offer to borrow an item the user tried to play (or the BingePass it came from).
    Returns True once it is borrowed."""
    t = dao_call(lambda d: d.title(bingepass_id or title_id))
    if t is None:
        return False
    e = next((e for e in t.episodes if e.id == episode_id), None) if episode_id and not bingepass_id else None
    target = e or t
    name = f'{t.title}: {e.title}' if e else t.title
    if not target.borrowable:
        log('Not borrowable: "{}" ({})', name, target.id, level=xbmc.LOGWARNING)
        xbmcgui.Dialog().ok('Hoopla', 'This title is not available to borrow right now.')
        return False
    if not confirm_borrow(t, e):
        log('Borrow declined: "{}" ({})', name, target.id)
        return False
    log('Borrowing "{}" ({})', name, target.id)
    # Playback starts right away, so the library's confirmation message isn't shown.
    message = dao_call(lambda d: d.borrow(target.id))
    if message is None:
        log('Borrow failed: "{}" ({})', name, target.id, level=xbmc.LOGERROR)
        return False
    log('Borrowed "{}": {}', name, message)
    return True

def inputstream_ready(manifest_type='mpd', drm='com.widevine.alpha'):
    """Make sure Kodi can play DRM video: inputstreamhelper installs/enables inputstream.adaptive and
    Widevine. Returns the inputstream addon to use, or None when it isn't ready and the user cancels."""
    inputstream = 'inputstream.adaptive'
    ready = False
    try:
        import inputstreamhelper
        helper = inputstreamhelper.Helper(manifest_type, drm=drm)
        ready = helper.check_inputstream()
        if ready:
            inputstream = helper.inputstream_addon
    except Exception as e:  # inputstreamhelper missing or broken: explain below rather than fail silently
        log('inputstreamhelper check failed: {}', e, level=xbmc.LOGERROR)
    if ready:
        return inputstream
    if addon.getSettingBool('force_play'):
        log('Widevine not confirmed; playing anyway, as the settings ask', level=xbmc.LOGWARNING)
        return inputstream
    log('Widevine not confirmed; asking whether to continue', level=xbmc.LOGWARNING)
    if xbmcgui.Dialog().yesno('Widevine DRM required',
                              'Widevine was not detected, and Hoopla videos are protected by DRM.\n'
                              'Use the InputStream Helper addon to install Widevine, then try again.',
                              nolabel='Cancel', yeslabel='Continue', autoclose=30000,
                              defaultbutton=xbmcgui.DLG_YESNO_NO_BTN):
        log('Continuing without confirmed Widevine')
        return inputstream
    log('Not playing: Widevine not confirmed')
    return None

def inputstream_version():
    try:
        version = xbmcaddon.Addon('inputstream.adaptive').getAddonInfo('version')
    except RuntimeError:
        return (0,)
    return tuple(int(n) for n in re.findall(r'\d+', version)[:3]) or (0,)

def set_drm_properties(item, stream):
    """inputstream.adaptive's DRM properties changed across Kodi versions:
    <= 21 (Kodi 20/21) takes license_type + license_key, and Kodi 20 still needs manifest_type;
    22+ (Kodi 22/23) takes drm_legacy, and the old properties are deprecated."""
    isa = inputstream_version()
    log('inputstream.adaptive {}', '.'.join(map(str, isa)))
    if isa[0] < 21:
        item.setProperty('inputstream.adaptive.manifest_type', stream.manifest_type)
    if not stream.drm:
        return
    headers = urlencode(stream.license_headers)
    if isa[0] >= 22:
        item.setProperty('inputstream.adaptive.drm_legacy', f'{stream.drm}|{stream.license_url}|{headers}')
    else:
        item.setProperty('inputstream.adaptive.license_type', stream.drm)
        item.setProperty('inputstream.adaptive.license_key', f'{stream.license_url}|{headers}|R{{SSM}}|')

def play(title_id, episode_id=None, bingepass_id=None, label=None, inputstream=None):
    """inputstream: set when borrow_then_play() has already run inputstream_ready()."""
    if not (inputstream or '').startswith('inputstream.'):
        inputstream = inputstream_ready()
    if not inputstream:
        xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem(offscreen=True))
        return

    def get_stream(d):
        try:
            return d.stream(title_id, episode_id, bingepass_id)
        except dao.NotBorrowedError:
            return NOT_BORROWED

    log('Play title {} episode {} bingepass {}', title_id, episode_id, bingepass_id)
    stream = dao_call(get_stream)
    if stream is NOT_BORROWED:
        log('Not borrowed yet, offering to borrow')
        stream = dao_call(get_stream) if borrow_for_play(title_id, episode_id, bingepass_id) else None
    if stream is None or stream is NOT_BORROWED:
        log('Not playing title {} episode {}', title_id, episode_id, level=xbmc.LOGWARNING)
        xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
        return
    log('Playing {} ({}, {})', stream.url, stream.manifest_type, stream.drm)  # the manifest URL is public

    item = xbmcgui.ListItem(path=stream.url, offscreen=True)
    if label:  # started with PlayMedia (after borrowing) there's no list item for Kodi to take a title from
        item.setLabel(label)
        item.getVideoInfoTag().setTitle(label)
    item.setMimeType('application/dash+xml' if stream.manifest_type == 'mpd' else 'application/vnd.apple.mpegurl')
    item.setContentLookup(False)
    item.setProperty('inputstream', inputstream)
    set_drm_properties(item, stream)
    xbmcplugin.setResolvedUrl(HANDLE, True, item)

def borrow_then_play(title_id, episode_id, bingepass_id, label):
    """Clicked an item that isn't borrowed: ask first, and only start playback once it's borrowed."""
    inputstream = inputstream_ready()  # no point spending a borrow on something Kodi can't play
    if inputstream and borrow_for_play(title_id, episode_id, bingepass_id):
        params = dict(title=title_id, label=label, inputstream=inputstream)
        if episode_id:
            params['episode'] = episode_id
        if bingepass_id:
            params['bingepass'] = bingepass_id
        xbmc.executebuiltin(f'PlayMedia({plugin_url("play", **params)})')
        xbmc.executebuiltin('Container.Refresh')  # the item now shows as borrowed

def return_item(item_id, label):
    if not xbmcgui.Dialog().yesno('Return to Hoopla?', f'Return "{label}" now?', nolabel='Cancel', yeslabel='Return'):
        log('Return declined: "{}" ({})', label, item_id)
        return
    log('Returning "{}" ({})', label, item_id)
    if dao_call(lambda d: d.return_item(item_id) or True):
        log('Returned "{}" ({})', label, item_id)
        notify('Returned')
        xbmc.executebuiltin('Container.Refresh')
    else:
        log('Return failed: "{}" ({})', label, item_id, level=xbmc.LOGERROR)

if __name__ == '__main__':
    PLUGIN_BASE = sys.argv[0]
    HANDLE = int(sys.argv[1])

    if len(sys.argv) > 2 and len(sys.argv[2]) > 1:
        args = dict(parse_qsl(sys.argv[2][1:]))
    else:
        args = {}

    action = args.get('action', None)
    page = int(args.get('page', 1))
    if not action:
        list_main()
    elif action == 'borrowed':
        list_borrowed()
    elif action == 'history':
        list_history(page)
    elif action == 'episodes':
        list_episodes(args['title'])
    elif action == 'bingepass':
        list_bingepass(page)
    elif action == 'kind':
        list_kind(args['kind'], args.get('label', ''), args.get('flex') == '1')
    elif action == 'row':
        list_row(args['row'], args['kind'])
    elif action == 'genres':
        list_genres(args['kind'])
    elif action == 'genre':
        list_genre(args['genre'], args.get('kind'), page)
    elif action == 'collections':
        list_collections(args['kind'])
    elif action == 'collection':
        list_collection(args['collection'], args.get('kind'), page)
    elif action == 'series':
        list_series(args['series'], page)
    elif action == 'related':
        list_related(args['title'])
    elif action == 'search':
        search(args['kind'], args.get('label', ''), args.get('q'), page)
    elif action == 'rate':
        rate(args['title'], args.get('label', ''), int(args.get('stars') or 0))
    elif action == 'login':
        force_login()
    elif action == 'play':
        play(args['title'], args.get('episode'), args.get('bingepass'), args.get('label'), args.get('inputstream'))
    elif action == 'borrow':
        borrow_then_play(args['title'], args.get('episode'), args.get('bingepass'), args.get('label', ''))
    elif action == 'pass':
        list_pass(args['title'], args.get('season'))
    elif action == 'return':
        return_item(args['id'], args.get('label', ''))
    elif action == 'unsupported':
        notify('Not supported yet')
    else:
        log('Unknown action in params: {}', args, level=xbmc.LOGERROR)
