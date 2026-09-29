#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import re
import sys
import uuid
from urllib.parse import parse_qsl, urlencode

import xbmc
import xbmcaddon
import xbmcgui
import xbmcplugin

import dao
from hoopla_graphql import HooplaGraphQLDAO as BackendDAO
from presentation import (VIDEO_KINDS, borrow_allowance, by_season, content_for, due_text, included_passes, loan_due,
                          paragraphs, playable, time_left, without_downloads)

PLUGIN_BASE = ''
HANDLE = -1
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

def dao_ok(fn):
    """dao_call() for a DAO call with no result: True when it succeeded."""
    return dao_call(lambda d: fn(d) or True) is not None

# Sort modes: the first method listed is the one Kodi starts with.
_TITLE_SORTS = [xbmcplugin.SORT_METHOD_VIDEO_YEAR, xbmcplugin.SORT_METHOD_VIDEO_RATING, xbmcplugin.SORT_METHOD_DURATION]
# fixed menu order
MENU = [xbmcplugin.SORT_METHOD_UNSORTED]
# folders of genres/collections: A-Z
NAMES = [xbmcplugin.SORT_METHOD_LABEL_IGNORE_THE, xbmcplugin.SORT_METHOD_UNSORTED]
# titles: A-Z, with the server's order (popularity, relevance, date, ...) as the next choice
TITLES = [xbmcplugin.SORT_METHOD_TITLE_IGNORE_THE, xbmcplugin.SORT_METHOD_UNSORTED] + _TITLE_SORTS
# seasons (with any movies after them): Kodi has no season sort for addons, so these lists come
# pre-sorted by by_season() and keep that order
SEASONS = [xbmcplugin.SORT_METHOD_UNSORTED, xbmcplugin.SORT_METHOD_TITLE_IGNORE_THE] + _TITLE_SORTS
EPISODES = [xbmcplugin.SORT_METHOD_EPISODE, xbmcplugin.SORT_METHOD_TITLE_IGNORE_THE, xbmcplugin.SORT_METHOD_UNSORTED]

def end_directory(items, succeeded=True, content=None, sort=MENU, cache=True):
    xbmcplugin.addDirectoryItems(HANDLE, items, len(items))
    if content:
        xbmcplugin.setContent(HANDLE, content)
    for method in sort:
        xbmcplugin.addSortMethod(HANDLE, method)
    xbmcplugin.endOfDirectory(HANDLE, succeeded=succeeded, cacheToDisc=cache)

def folder_item(label):
    item = xbmcgui.ListItem(label=label)
    item.getVideoInfoTag().setTitle(label)
    return item

def pinned_folder(url, label):
    item = folder_item(label)
    item.setProperty('SpecialSort', 'top')  # stays first whatever the sort
    return url, item, True

def next_page(url):
    item = folder_item('Next page')
    item.setProperty('SpecialSort', 'bottom')  # stays last whatever the sort
    return url, item, True

def set_art(item, image_url):
    if image_url:
        item.setArt({'thumb': image_url, 'poster': image_url})

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

def title_item(t, label=None, note=None):
    """label: shown in place of the title. note: an extra line for the end of the plot."""
    title = label or t.title
    label = f'[Flex] {title}' if t.borrow_type == dao.FLEX else title
    item = xbmcgui.ListItem(label=f'[{t.badge}] {label}' if t.badge else label)
    info = item.getVideoInfoTag()
    # Kodi's sort methods label items with the info tag's title (the %T mask), not the ListItem label.
    info.setTitle(title)
    info.setSortTitle(t.title)
    info.setMediaType({dao.MOVIE: 'movie', dao.TELEVISION: 'tvshow'}.get(t.kind, 'video'))
    if t.artist:
        info.setArtists([t.artist])
    info.setPlot(paragraphs(t.synopsis, due_text(t.due), note))
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
    info.setPlot(paragraphs(e.synopsis, due_text(e.due)))
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

def catalog_entry(t, label=None, note=None):
    """A (url, item, is_folder) entry for a title from any listing."""
    item = title_item(t, label, note)
    if t.kind in VIDEO_KINDS:
        item.addContextMenuItems(title_menu(t))
    if t.kind == dao.TELEVISION:
        return plugin_url('episodes', title=t.id), item, True
    if t.kind == dao.MOVIE:
        return play_entry(item, t.borrowed, t.title, title=t.id)
    if t.kind == dao.BINGEPASS:
        return plugin_url('pass', title=t.id), item, True
    return plugin_url('unsupported'), item, False

def list_titles(titles, sort, keep=playable):
    shown = keep(titles)
    end_directory([catalog_entry(t) for t in shown], succeeded=titles is not None, content=content_for(shown), sort=sort)

def list_page(result, next_url, sort, keep=playable, first=()):
    """first: entries pinned above the titles, e.g. a genre's "Top rated" folder."""
    shown = keep(result.titles) if result else []
    items = list(first) + [catalog_entry(t) for t in shown]
    if result and result.has_more:
        items.append(next_page(next_url))
    end_directory(items, succeeded=result is not None, content=content_for(shown), sort=sort)

def list_main():
    result = dao_call(lambda d: (d.borrow_limits(), d.kinds()))
    limits, kinds = result if result else (None, [])
    # Libraries without Flex borrowing report no Flex allowance; pass that down so the kind menu needn't ask.
    flex = int(bool(limits and limits.flex_remaining is not None))

    label = f'Borrowed - {limits.message}' if limits and limits.message else 'Borrowed'
    video_kinds = ','.join(k.id for k in kinds if k.name in VIDEO_KINDS)
    items = [
        (plugin_url('borrowed'), folder_item(label), True),
        (plugin_url('bonus', kinds=video_kinds), folder_item('Bonus Borrows'), True),
        #(plugin_url('history', page=1), folder_item('Borrowing history'), True),
        (plugin_url('bingepass', page=1), folder_item('BingePass'), True),
    ]
    for k in kinds:
        if k.name in VIDEO_KINDS:
            items.append((plugin_url('kind', kind=k.id, label=k.label, flex=flex), folder_item(k.label), True))
    # Checked after the calls above, which log in on their own when they can.
    if not addon.getSetting('authorization'):
        items.append((plugin_url('login'), xbmcgui.ListItem('Log in'), False))
    end_directory(items, cache=False)  # the Borrowed label shows the current allowance

def list_kind(kind_id, label, flex):
    """The menu for one kind (Movies, Television). It makes no requests itself."""
    rows = [r for r in ROWS if r != 'flex' or flex]
    items = [
        (plugin_url('search', kind=kind_id, label=label), folder_item(f'Search {label}'), True),
        (plugin_url('top', kind=kind_id, page=1), folder_item('Top rated'), True),
    ]
    items += [(plugin_url('row', row=r, kind=kind_id), folder_item(ROWS[r][0]), True) for r in rows]
    items += [
        (plugin_url('collections', kind=kind_id), folder_item('Collections'), True),
        (plugin_url('genres', kind=kind_id), folder_item('Genres'), True),
    ]
    end_directory(items)

def list_row(row, kind_id):
    _, fetch = ROWS[row]
    list_titles(dao_call(lambda d: fetch(d, kind_id)), TITLES)

def list_borrowed():
    titles = dao_call(lambda d: d.borrowed())
    # Borrowed BingePasses stay listed; they open as a folder of what they bundle.
    shown = playable(titles) + included_passes(titles)
    items = []
    for t in shown:
        due = loan_due(t)
        items.append(catalog_entry(t, f'{t.title} ({time_left(due)})' if due else None))
    end_directory(items, succeeded=titles is not None, content=content_for(shown), sort=TITLES, cache=False)

def list_bonus(kind_ids):
    titles = dao_call(lambda d: d.bonus_titles(kind_ids))
    for t in titles or []:
        t.badge = None  # they'd all say "Bonus Borrow" in this folder
    list_titles(titles, TITLES)

def list_history(page):
    entries = dao_call(lambda d: d.history(page=page, page_size=HISTORY_PAGE_SIZE))
    items, shown = [], []
    for h in entries or []:
        if h.title.kind not in VIDEO_KINDS:
            continue
        shown.append(h.title)
        label = f'{h.title.title}: {h.episode_title}' if h.episode_title else h.title.title
        note = f'Borrowed {h.borrowed_date.astimezone():%Y-%m-%d}' if h.borrowed_date else None
        items.append(catalog_entry(h.title, label, note))
    if entries and len(entries) == HISTORY_PAGE_SIZE:
        items.append(next_page(plugin_url('history', page=page + 1)))
    end_directory(items, succeeded=entries is not None, content=content_for(shown), sort=TITLES)

def list_season(season, bingepass_id=None):
    """A season's episodes. bingepass_id: the pass the season was opened through, whose loan plays them."""
    params = {'bingepass': bingepass_id} if bingepass_id else {}
    items = [play_entry(episode_item(season, e), e.borrowed, f'{season.title}: {e.title}',
                        title=season.id, episode=e.id, **params)
             for e in season.episodes] if season else []
    end_directory(items, succeeded=season is not None, content='episodes', sort=EPISODES, cache=False)

def list_episodes(title_id):
    list_season(dao_call(lambda d: d.title(title_id)))

def list_bingepass(page):
    result = dao_call(lambda d: d.bingepass_titles(page=page))
    list_page(result, plugin_url('bingepass', page=page + 1), TITLES, keep=included_passes)

def pass_contents(t):
    """An included BingePass's seasons, then its movies."""
    return by_season(playable(t.included))

def list_pass_season(pass_id, season_id):
    t = dao_call(lambda d: d.title(pass_id))
    contents = pass_contents(t) if t else []
    list_season(next((x for x in contents if x.kind == dao.TELEVISION and x.id == season_id), None), pass_id)

def list_pass(pass_id):
    """An included BingePass's seasons and movies. A pass of just one season opens straight to its episodes."""
    t = dao_call(lambda d: d.title(pass_id))
    if t is None or t.bingepass_type == dao.BINGEPASS_PARTNER:
        end_directory([], succeeded=False)
        return
    included = pass_contents(t)
    if len(included) == 1 and included[0].kind == dao.TELEVISION:
        list_season(included[0], pass_id)
        return
    if not included:
        xbmcgui.Dialog().ok(t.title, 'This BingePass has no movies or TV to play here.')
        end_directory([], succeeded=False)
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
    end_directory(items, content=content_for(included), sort=SEASONS)

def list_genres(kind_id):
    genres = dao_call(lambda d: d.genres(kind_id))
    # A flat list: every genre opens its titles, and a parent genre's titles include its sub-genres'.
    items = [(plugin_url('genre', genre=g.id, kind=kind_id, page=1), folder_item(g.name), True) for g in genres or []]
    end_directory(items, succeeded=genres is not None, sort=NAMES)

def list_genre(genre_id, kind_id, page):
    result = dao_call(lambda d: d.genre_titles(genre_id, page=page, kind_id=kind_id))
    top = [pinned_folder(plugin_url('top', kind=kind_id, genre=genre_id, page=1), 'Top rated')] if page == 1 and kind_id else []
    list_page(result, plugin_url('genre', genre=genre_id, kind=kind_id, page=page + 1), TITLES, first=top)

def list_top_rated(kind_id, genre_id, page):
    """Well-rated titles, most popular first: for a whole kind, or within one genre."""
    result = dao_call(lambda d: d.top_rated(kind_id, genre_id=genre_id, page=page))
    params = dict(kind=kind_id, page=page + 1)
    if genre_id:
        params['genre'] = genre_id
    list_page(result, plugin_url('top', **params), TITLES)

def list_collections(kind_id):
    collections = dao_call(lambda d: d.collections(kind_id))
    items = [(plugin_url('collection', collection=c.id, kind=kind_id, page=1), folder_item(c.name), True)
             for c in collections or []]
    end_directory(items, succeeded=collections is not None, sort=NAMES)

def list_collection(collection_id, kind_id, page):
    # Collections can mix kinds (e.g. "... | All Titles"); only ask for the kind this menu is about.
    result = dao_call(lambda d: d.collection_titles(collection_id, page=page, kind_id=kind_id))
    list_page(result, plugin_url('collection', collection=collection_id, kind=kind_id, page=page + 1), TITLES)

def list_series(series_id, page):
    result = dao_call(lambda d: d.series_titles(series_id, page=page))
    list_page(result, plugin_url('series', series=series_id, page=page + 1), SEASONS,
              keep=lambda titles: by_season(playable(titles)))

def list_related(title_id):
    list_titles(dao_call(lambda d: d.related_titles(title_id)), TITLES)

def search(kind_id, label, query, page):
    if query is None:
        query = xbmcgui.Dialog().input(f'Search {label}')
        if not query:
            end_directory([], succeeded=False)
            return
    result = dao_call(lambda d: d.search(query, kind_id, page=page))
    list_page(result, plugin_url('search', kind=kind_id, label=label, q=query, page=page + 1), TITLES)

def rate(title_id, label, current):
    choices = [f'{"*" * n} ({n})' for n in range(5, 0, -1)]
    preselect = 5 - current if 1 <= current <= 5 else -1
    picked = xbmcgui.Dialog().select(f'Rate "{label}"', choices, preselect=preselect)
    if picked < 0:
        return
    stars = 5 - picked
    log('Rating "{}" ({}) {} of 5', label, title_id, stars)
    if dao_ok(lambda d: d.rate(title_id, stars)):
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

def confirm_borrow(t, name):
    limits = None if t.bonus else dao_call(lambda d: d.borrow_limits())  # a bonus borrow doesn't use them
    lines = [name, borrow_allowance(t, limits), without_downloads(t.lending_message)]
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
    if not confirm_borrow(t, name):
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

def inputstream_ready(manifest_type='mpd', drm=dao.WIDEVINE):
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

def inputstream_major():
    """inputstream.adaptive's major version, or 0 when it isn't installed."""
    try:
        version = xbmcaddon.Addon('inputstream.adaptive').getAddonInfo('version')
    except RuntimeError:
        return 0
    log('inputstream.adaptive {}', version)
    m = re.match(r'\d+', version)
    return int(m.group()) if m else 0

def set_drm_properties(item, stream):
    """inputstream.adaptive's DRM properties changed across Kodi versions:
    <= 21 (Kodi 20/21) takes license_type + license_key, and Kodi 20 still needs manifest_type;
    22+ (Kodi 22/23) takes drm_legacy, and the old properties are deprecated."""
    isa = inputstream_major()
    if isa < 21:
        item.setProperty('inputstream.adaptive.manifest_type', stream.manifest_type)
    if not stream.drm:
        return
    headers = urlencode(stream.license_headers)
    if isa >= 22:
        item.setProperty('inputstream.adaptive.drm_legacy', f'{stream.drm}|{stream.license_url}|{headers}')
    else:
        item.setProperty('inputstream.adaptive.license_type', stream.drm)
        item.setProperty('inputstream.adaptive.license_key', f'{stream.license_url}|{headers}|R{{SSM}}|')

def play(title_id, episode_id=None, bingepass_id=None, label=None, inputstream=None):
    """inputstream: set when borrow_then_play() has already run inputstream_ready()."""
    if not inputstream:
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
    item.setMimeType('application/dash+xml')
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
    if dao_ok(lambda d: d.return_item(item_id)):
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
    elif action == 'bonus':
        list_bonus([k for k in args.get('kinds', '').split(',') if k])
    elif action == 'episodes':
        list_episodes(args['title'])
    elif action == 'bingepass':
        list_bingepass(page)
    elif action == 'kind':
        list_kind(args['kind'], args.get('label', ''), args.get('flex') == '1')
    elif action == 'row':
        list_row(args['row'], args['kind'])
    elif action == 'top':
        list_top_rated(args['kind'], args.get('genre'), page)
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
        if 'season' in args:
            list_pass_season(args['title'], args['season'])
        else:
            list_pass(args['title'])
    elif action == 'return':
        return_item(args['id'], args.get('label', ''))
    elif action == 'unsupported':
        notify('Not supported yet')
    else:
        log('Unknown action in params: {}', args, level=xbmc.LOGERROR)
