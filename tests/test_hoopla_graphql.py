import base64
import json
import os
import unittest
from collections import namedtuple
from datetime import datetime, timezone

import dao
from hoopla_graphql import DASH_URL, GATEWAY, GRAPHQL_URL, TOKENS_URL, WIDEVINE, HooplaGraphQLDAO

FIXTURES = os.path.join(os.path.dirname(__file__), 'fixtures')
USER_ID = '00000000-0000-4000-8000-000000000001'


def fixture(name):
    with open(os.path.join(FIXTURES, name + '.json'), encoding='utf-8') as f:
        return json.load(f)


def rest_fixture(name):
    with open(os.path.join(FIXTURES, 'rest', name), encoding='utf-8') as f:
        return f.read()


class FakeResponse:
    def __init__(self, body=None, status_code=200, reason='OK', text=None):
        self.body = body
        self.status_code = status_code
        self.reason = reason
        self.text = text if text is not None else json.dumps(body)

    def json(self):
        if self.body is None:
            raise ValueError('no JSON')
        return self.body


# REST calls are served by (method, URL fragment); the first match wins.
REST_FIXTURES = [
    ('GET', '/upfront-auth-tokens/', lambda: FakeResponse(text=rest_fixture('upfront_auth_token.txt'))),
    ('POST', '/borrowed-titles/', lambda: FakeResponse(json.loads(rest_fixture('borrow.json')))),
    ('DELETE', '/borrowed-titles/', lambda: FakeResponse(json.loads(rest_fixture('return.json')))),
    ('POST', '/patron-ratings', lambda: FakeResponse(status_code=204, reason='No Content', text='')),
]


Request = namedtuple('Request', 'method url headers json data')


class FakeSession:
    """GraphQL posts are served from tests/fixtures/<operationName>.json, REST calls from REST_FIXTURES.

    `responses` overrides either, keyed by operationName, URL, or 'METHOD url-fragment'.
    Every request is recorded in `sent`.
    """

    def __init__(self, responses=None):
        self.headers = {}
        self.responses = responses or {}
        self.sent = []

    @property
    def operations(self):
        """The GraphQL payloads sent, in order."""
        return [r.json for r in self.sent if r.url == GRAPHQL_URL]

    @property
    def rest(self):
        """The requests sent other than GraphQL and login, in order."""
        return [r for r in self.sent if r.url not in (GRAPHQL_URL, TOKENS_URL)]

    def request(self, method, url, headers=None, json=None, data=None, timeout=None):
        self.sent.append(Request(method, url, headers or {}, json, data))
        if url in (GRAPHQL_URL, TOKENS_URL):
            key = json['operationName'] if url == GRAPHQL_URL else url
            return self.responses.get(key) or FakeResponse(fixture(key))
        for key, resp in self.responses.items():
            m, _, fragment = key.partition(' ')
            if m == method and fragment and fragment in url:
                return resp
        return next(make() for m, fragment, make in REST_FIXTURES if m == method and fragment in url)


def make_dao(responses=None, token=None, device_id='device-1'):
    sess = FakeSession(responses)
    return HooplaGraphQLDAO(session_token=token, device_id=device_id, sess=sess), sess


class QueryShapeTest(unittest.TestCase):
    def test_every_request_is_a_named_graphql_operation(self):
        d, sess = make_dao()
        d.kinds()
        d.genres('9')
        d.borrowed()
        d.borrow_limits()
        d.bingepass_titles(page=2, page_size=3)
        d.genre_titles('320711872', page=2, page_size=3)
        d.title('2000001')
        d.featured_titles('7')
        d.recent_titles('7')
        d.collections('7')
        d.related_titles('2000001')
        d.history(page=2, page_size=5)

        self.assertEqual({r.url for r in sess.sent}, {GRAPHQL_URL})
        for payload in sess.operations:
            self.assertIn(f"query {payload['operationName']}", payload['query'])
        variables = {p['operationName']: p['variables'] for p in sess.operations}
        self.assertEqual(variables['GetGenresListQuery'], {'kindId': '9'})
        self.assertEqual(variables['GetBorrowedTitlesQuery'], {'criteria': {}})
        self.assertEqual(variables['GetBingePassTitlesQuery'],
                         {'pagination': {'page': 2, 'pageSize': 3}, 'sort': 'A_Z'})
        self.assertEqual(variables['GetFilterSearchQuery'],
                         {'criteria': {'genreId': '320711872', 'pagination': {'page': 2, 'pageSize': 3}},
                          'sort': 'A_Z'})
        self.assertEqual(variables['GetFetchTitleDetailQuery'], {'id': '2000001', 'includeDeleted': False})
        self.assertEqual(variables['GetFeaturedQuery'], {'kindId': '7', 'audience': 'ANY'})
        self.assertEqual(variables['GetRecentQuery'], {'kindId': '7', 'availability': 'AVAILABLE_NOW', 'audience': 'ANY'})
        self.assertEqual(variables['GetCollectionsListQuery'], {'kindId': '7', 'audience': 'ANY'})
        self.assertEqual(variables['GetRelatedTitlesForTitle'],
                         {'titleId': '2000001', 'hooplaDeviceId': 'device-1', 'audience': 'ANY'})
        self.assertEqual(variables['GetPatronHistoryQuery'], {'historyAudience': 'ANY', 'page': 2, 'pageSize': 5})

    def test_popular_queries_by_borrow_type(self):
        d, sess = make_dao()
        d.popular_titles('7')
        d.popular_titles('7', dao.INSTANT)
        d.popular_titles('7', dao.FLEX)
        ops = [(p['operationName'], p['query']) for p in sess.operations]
        self.assertEqual([o for o, _ in ops], ['GetPopularTitlesQuery', 'GetPopularInstantQuery', 'GetPopularFlexQuery'])
        self.assertNotIn('borrowType', ops[0][1])
        self.assertIn('borrowType: PPU', ops[1][1])
        self.assertIn('borrowType: EST', ops[2][1])
        self.assertTrue(all(p['variables'] == {'kindId': '7', 'audience': 'ANY'} for p in sess.operations))

    def test_search_criteria(self):
        d, sess = make_dao()
        d.search('oppenheimer', '7', page=2, page_size=3)
        d.collection_titles('16279', page=1, page_size=3)
        d.series_titles('4000001')
        d.collection_titles('30266', page=1, page_size=3, kind_id='9')
        d.genre_titles('320711872', page=1, page_size=3, kind_id='7')
        criteria = [(p['variables']['criteria'], p['variables']['sort']) for p in sess.operations]
        self.assertEqual(criteria, [
            ({'q': 'oppenheimer', 'kindId': '7', 'audience': 'ANY', 'pagination': {'page': 2, 'pageSize': 3}}, 'RELEVANCE'),
            ({'collectionId': '16279', 'audience': 'ANY', 'pagination': {'page': 1, 'pageSize': 3}}, 'A_Z'),
            ({'seriesId': '4000001', 'audience': 'ANY', 'pagination': {'page': 1, 'pageSize': 150}}, 'A_Z'),
            ({'collectionId': '30266', 'audience': 'ANY', 'kindId': '9', 'pagination': {'page': 1, 'pageSize': 3}},
             'A_Z'),
            ({'genreId': '320711872', 'kindId': '7', 'pagination': {'page': 1, 'pageSize': 3}}, 'A_Z'),
        ])


class MappingTest(unittest.TestCase):
    def test_kinds(self):
        d, _ = make_dao()
        kinds = {k.name: (k.id, k.label) for k in d.kinds()}
        self.assertEqual(len(kinds), 7)
        self.assertEqual(kinds[dao.MOVIE], ('7', 'Movies'))
        self.assertEqual(kinds[dao.TELEVISION], ('9', 'Television'))
        self.assertEqual(kinds[dao.BINGEPASS], ('11', 'BingePasses'))

    def test_kinds_skip_disabled(self):
        body = fixture('GetKindsQuery')
        body['data']['kinds'][0]['enabled'] = False
        d, _ = make_dao({'GetKindsQuery': FakeResponse(body)})
        self.assertNotIn(body['data']['kinds'][0]['name'], [k.name for k in d.kinds()])

    def test_genres(self):
        d, _ = make_dao()
        genres = d.genres('7')
        self.assertEqual([(g.id, g.name) for g in genres], [('100', 'Action'), ('101', 'Documentary')])

    def test_borrowed(self):
        d, _ = make_dao()
        [t] = d.borrowed()
        self.assertEqual(t.id, '1000001')
        self.assertEqual(t.title, 'Sample Show - Season 1')
        self.assertEqual(t.kind, dao.TELEVISION)
        self.assertEqual(t.artist, 'Sample Artist')
        self.assertTrue(t.borrowed, 'a series counts as borrowed when any episode is')
        self.assertIsNone(t.due)
        self.assertIn('smp_sampleep1', t.image_url)

        self.assertEqual([e.number for e in t.episodes], [1, 2, 3])
        self.assertEqual([e.borrowed for e in t.episodes], [True, False, False])
        self.assertEqual(t.episodes[0].due, datetime(2030, 1, 1, tzinfo=timezone.utc))
        self.assertIsNone(t.episodes[1].due)
        self.assertEqual(t.episodes[2].synopsis, 'Synopsis for sample episode 3.')

    def test_borrow_limits(self):
        d, _ = make_dao()
        lim = d.borrow_limits()
        self.assertEqual(lim.message, 'You can borrow 5 more titles this month.')
        self.assertIsNone(lim.flex_remaining)
        self.assertEqual(lim.flex_message, '')

    def test_bingepass_titles(self):
        d, _ = make_dao()
        page = d.bingepass_titles(page=1, page_size=3)
        self.assertEqual(page.total, 115)
        self.assertTrue(page.has_more)
        self.assertEqual(len(page.titles), 3)
        self.assertTrue(all(t.kind == dao.BINGEPASS for t in page.titles))
        self.assertEqual([t.badge for t in page.titles], [None, None, 'New'])
        self.assertFalse(any(t.borrowed for t in page.titles))

    def test_genre_titles(self):
        d, _ = make_dao()
        page = d.genre_titles('320711872', page=1, page_size=3)
        self.assertEqual(page.total, 87)
        self.assertTrue(page.has_more)
        self.assertEqual([t.kind for t in page.titles], [dao.BINGEPASS, dao.MOVIE, dao.MOVIE])
        self.assertTrue(all(t.borrowable and not t.borrowed for t in page.titles))
        movie = page.titles[1]
        self.assertEqual((movie.id, movie.year), ('18660385', 2025))
        self.assertTrue(movie.synopsis)

    def test_title_borrowed_movie(self):
        d, _ = make_dao()
        t = d.title('2000001')
        self.assertEqual((t.id, t.title, t.kind), ('2000001', 'Sample Movie', dao.MOVIE))
        self.assertTrue(t.borrowed)
        self.assertFalse(t.borrowable)
        self.assertEqual(t.due, datetime(2030, 1, 1, tzinfo=timezone.utc))
        self.assertEqual((t.year, t.duration, t.rating), (2023, 5820, None))
        self.assertEqual(t.genres, ['History'])
        self.assertEqual(t.directors, ['Sample Director'])
        self.assertEqual(t.episodes, [])
        self.assertIn('3 days', t.lending_message)

    def test_title_series_episodes(self):
        d, _ = make_dao({'GetFetchTitleDetailQuery': FakeResponse(series_fixture())})
        t = d.title('2000002')
        self.assertEqual(t.kind, dao.TELEVISION)
        self.assertTrue(t.borrowed, 'a series counts as borrowed when any episode is')
        self.assertEqual([(e.id, e.number, e.borrowed, e.borrowable) for e in t.episodes],
                         [('2100001', 1, True, False), ('2100002', 2, False, True)])
        self.assertEqual(t.episodes[0].duration, 1500)

    def test_title_ratings(self):
        d, _ = make_dao()
        t = d.title('2000001')
        self.assertEqual((t.rating_average, t.rating_count, t.user_stars), (3.5, 12, 4))
        self.assertEqual(t.borrow_type, dao.INSTANT)
        self.assertIsNone(t.series_id)

    def test_home_rows(self):
        d, _ = make_dao()
        for titles in (d.featured_titles('7'), d.popular_titles('7'), d.recent_titles('7'),
                       d.popular_titles('7', dao.INSTANT)):
            self.assertEqual(len(titles), 2)
            self.assertTrue(all(t.kind == dao.MOVIE and t.borrow_type == dao.INSTANT and t.borrowable for t in titles))
            self.assertTrue(all(t.title and t.image_url for t in titles))
        self.assertEqual(d.popular_titles('7', dao.FLEX), [], 'no Flex titles in a library without Flex')

    def test_collections_deduplicated(self):
        d, _ = make_dao()
        cols = d.collections('7')
        self.assertEqual([c.id for c in cols], ['34850', '19843', '16279', '75973'])
        self.assertEqual(cols[0].name, 'New Movie Releases | 2026')

    def test_collection_titles_with_series(self):
        d, _ = make_dao({'GetFilterSearchQuery': FakeResponse(fixture('GetFilterSearchQuery_collection'))})
        page = d.collection_titles('16279', page=1, page_size=3)
        self.assertEqual(page.total, 31)
        becky = page.titles[2]
        self.assertEqual((becky.series_id, becky.series_name), ('16847785987', 'Becky'))
        self.assertTrue(all(t.rating_count > 0 for t in page.titles))

    def test_related(self):
        d, _ = make_dao()
        self.assertEqual(len(d.related_titles('2000001')), 2)

    def test_related_needs_device_id(self):
        d, sess = make_dao(device_id=None)
        with self.assertRaises(dao.LibraryError):
            d.related_titles('2000001')
        self.assertEqual(sess.sent, [])

    def test_history(self):
        d, _ = make_dao()
        [h] = d.history()
        self.assertEqual((h.title.id, h.title.kind, h.episode_title), ('2000002', dao.TELEVISION, 'Pilot'))
        self.assertEqual(h.borrowed_date, datetime(2029, 12, 1, 12, tzinfo=timezone.utc))

    def test_season_from_title(self):
        d, _ = make_dao({'GetFetchTitleDetailQuery': FakeResponse(series_fixture())})
        self.assertEqual(d.title('2000002').season, 1)
        cases = {'Wild Kratts - Season 6': 6, 'Wild Kratts': None, 'Some Show: Series 2': 2,
                 'Some Show, Vol. 3': 3, 'Seasonal Specials': None}
        for name, season in cases.items():
            body = series_fixture()
            body['data']['title']['title'] = name
            d, _ = make_dao({'GetFetchTitleDetailQuery': FakeResponse(body)})
            self.assertEqual(d.title('2000002').season, season, name)

    def test_list_metadata(self):
        hit = {'id': '1', 'title': 'Lucy', 'kind': {'name': 'MOVIE'}, 'year': 1958, 'seconds': 2100,
               'releaseDate': '2024-04-04T00:00:00.000Z', 'rating': 'NR', 'genres': [{'id': '9', 'name': 'Comedy'}]}
        body = {'data': {'search': {'found': 1, 'hits': [hit]}}}
        d, _ = make_dao({'GetFilterSearchQuery': FakeResponse(body)})
        [t] = d.search('lucy', '7').titles
        self.assertEqual(t.year, 1958, 'year is the production year, not the Hoopla release date')
        self.assertEqual((t.duration, t.rating, t.genres), (2100, 'NR', ['Comedy']))

    def test_mpaa(self):
        from hoopla_graphql import _mpaa
        self.assertEqual([_mpaa(r) for r in ('PG13', 'NC17', 'TVMA', 'TVY7', 'TV-PG', 'R', 'NR', 'NRA', None)],
                         ['PG-13', 'NC-17', 'TV-MA', 'TV-Y7', 'TV-PG', 'R', 'NR', None, None])

    def test_missing_title(self):
        d, _ = make_dao({'GetFetchTitleDetailQuery': FakeResponse({'data': {'title': None}})})
        with self.assertRaises(dao.LibraryError):
            d.title('1')

    def test_has_more_on_last_page(self):
        self.assertFalse(dao.TitlePage(titles=[], total=115, page=3, page_size=50).has_more)
        self.assertTrue(dao.TitlePage(titles=[], total=115, page=2, page_size=50).has_more)


def series_fixture():
    circ = {'id': '900000002', 'dueDate': '2030-01-01T00:00:00.000Z', 'patron': {'id': '800000001'}}
    episode = {'artKey': 'smp_sampleep', 'synopsis': '', 'seconds': 1500, '__typename': 'Content'}
    return {'data': {'title': {
        'id': '2000002', 'title': 'Sample Show - Season 1', 'artKey': 'smp_sampleshow', 'kind': {'name': 'TELEVISION'},
        'status': 'BORROW', 'mediaKey': None, 'circulation': None, 'episodes': [
            dict(episode, id='2100001', episode=1, title='Pilot', status='BORROWED', mediaKey='smp_sampleep1',
                 circulation=circ),
            dict(episode, id='2100002', episode=2, title='Second', status='BORROW', mediaKey='smp_sampleep2',
                 circulation=None),
        ]}}}


class StreamTest(unittest.TestCase):
    def test_movie_stream(self):
        d, sess = make_dao()
        s = d.stream('2000001')
        self.assertEqual(s.url, DASH_URL.format('smp_samplemovie'))
        self.assertEqual((s.manifest_type, s.drm), ('mpd', WIDEVINE))
        self.assertTrue(s.license_url.startswith('https://'))
        self.assertEqual(s.license_headers['x-dt-auth-token'], rest_fixture('upfront_auth_token.txt'))
        custom = json.loads(base64.b64decode(s.license_headers['x-dt-custom-data']))
        self.assertEqual(custom, {'userId': '800000001', 'sessionId': '900000001', 'merchant': 'hoopla'})
        [req] = sess.rest
        self.assertEqual((req.method, req.url),
                         ('GET', GATEWAY + '/license/castlabs/upfront-auth-tokens/smp_samplemovie/800000001/900000001'))

    def test_episode_stream(self):
        d, sess = make_dao({'GetFetchTitleDetailQuery': FakeResponse(series_fixture())})
        s = d.stream('2000002', '2100001')
        self.assertEqual(s.url, DASH_URL.format('smp_sampleep1'))
        self.assertTrue(sess.rest[0].url.endswith('/smp_sampleep1/800000001/900000002'))

    def test_not_borrowed(self):
        d, sess = make_dao({'GetFetchTitleDetailQuery': FakeResponse(series_fixture())})
        with self.assertRaises(dao.NotBorrowedError):
            d.stream('2000002', '2100002')
        with self.assertRaises(dao.LibraryError):
            d.stream('2000002', '9999999')
        self.assertEqual(sess.rest, [], 'no playback authorization for an unborrowed item')

    def test_upfront_token_401(self):
        d, _ = make_dao({'GET /upfront-auth-tokens/': FakeResponse(status_code=401, reason='Unauthorized')})
        with self.assertRaises(dao.AuthError):
            d.stream('2000001')


class BorrowTest(unittest.TestCase):
    def test_borrow(self):
        d, sess = make_dao()
        self.assertIn('You can now enjoy this title', d.borrow('2000001'))
        [req] = sess.rest
        self.assertEqual((req.method, req.url), (
            'POST', f'{GATEWAY}/core/v2/users/{USER_ID}/patrons/800000001/borrowed-titles/2000001'
                    '?returnBorrowedTitles=true'))
        self.assertEqual((req.headers['patron-id'], req.headers['app']), ('800000001', 'WWW'))

    def test_borrow_refused(self):
        body = {'message': 'You have reached your monthly borrow limit.'}
        d, _ = make_dao({'POST /borrowed-titles/': FakeResponse(body, status_code=400, reason='Bad Request')})
        with self.assertRaises(dao.LibraryError) as cm:
            d.borrow('2000001')
        self.assertNotIsInstance(cm.exception, dao.AuthError)
        self.assertIn('monthly borrow limit', str(cm.exception))

    def test_return(self):
        d, sess = make_dao()
        d.return_item('2100001')
        [req] = sess.rest
        self.assertEqual((req.method, req.url),
                         ('DELETE', f'{GATEWAY}/core/users/{USER_ID}/patrons/800000001/borrowed-titles/2100001'))


def pass_response(name='GetFetchTitleDetailQuery_bingepass'):
    return FakeResponse(fixture(name))


class BingePassTest(unittest.TestCase):
    def test_list_types(self):
        d, _ = make_dao()
        page = d.bingepass_titles()
        self.assertEqual([t.bingepass_type for t in page.titles],
                         [dao.BINGEPASS_PARTNER, dao.BINGEPASS_PARTNER, dao.BINGEPASS_INCLUDED])

    def test_content_kinds(self):
        d, _ = make_dao()
        rogers = d.bingepass_titles().titles[2]
        self.assertEqual(rogers.content_kinds, [dao.TELEVISION])

    def test_included_pass(self):
        d, _ = make_dao({'GetFetchTitleDetailQuery': pass_response()})
        t = d.title('19268061')
        self.assertEqual((t.kind, t.bingepass_type), (dao.BINGEPASS, dao.BINGEPASS_INCLUDED))
        self.assertTrue(t.borrowable and not t.borrowed)
        self.assertEqual([(s.id, s.kind, s.season, len(s.episodes)) for s in t.included],
                         [('19269734', dao.TELEVISION, 26, 2), ('19269741', dao.TELEVISION, 24, 2)])
        ep = t.included[0].episodes[0]
        self.assertEqual((ep.id, ep.number, ep.title, ep.duration), ('19269734', 1, 'All About Hats', 1800))
        self.assertTrue(ep.borrowable and not ep.borrowed, 'episodes share the pass loan state')

    def test_partner_pass(self):
        d, _ = make_dao({'GetFetchTitleDetailQuery': pass_response('GetFetchTitleDetailQuery_partner')})
        t = d.title('15935096')
        self.assertEqual(t.bingepass_type, dao.BINGEPASS_PARTNER)
        self.assertEqual(t.included, [])

    def test_stream_through_borrowed_pass(self):
        d, sess = make_dao({'GetFetchTitleDetailQuery': FakeResponse(series_fixture())})
        s = d.stream('2000002', '2100002', bingepass_id='19268061')  # episode 2 has no loan of its own
        self.assertEqual(s.url, DASH_URL.format('smp_sampleep2'))
        self.assertTrue(sess.rest[0].url.endswith('/smp_sampleep2/800000001/900000009'),
                        'the pass loan authorizes the episode')
        ops = [p['operationName'] for p in sess.operations]
        self.assertEqual(ops, ['GetFetchTitleDetailQuery', 'GetFetchBorrowCirculationQuery'])

    def test_stream_through_unborrowed_pass(self):
        body = {'data': {'title': {'id': '19268061', 'circulation': None}}}
        d, sess = make_dao({'GetFetchTitleDetailQuery': FakeResponse(series_fixture()),
                            'GetFetchBorrowCirculationQuery': FakeResponse(body)})
        with self.assertRaises(dao.NotBorrowedError):
            d.stream('2000002', '2100002', bingepass_id='19268061')
        self.assertEqual(sess.rest, [])


def collections_response(*names):
    group = [{'id': str(30000 + i), 'name': n, '__typename': 'Collection'} for i, n in enumerate(names)]
    return FakeResponse({'data': {'library': [], 'featured': group, 'all': group}})


class BonusTest(unittest.TestCase):
    def test_all_video_collection(self):
        d, sess = make_dao({'GetCollectionsListQuery': collections_response(
            'New to hoopla', 'Bonus Borrows September 2026 | All Titles', 'Bonus Borrows September 2026 | All Video')})
        titles = d.bonus_titles(['7', '9'])
        searches = [p['variables']['criteria'] for p in sess.operations if p['operationName'] == 'GetFilterSearchQuery']
        self.assertEqual(searches, [{'collectionId': '30002', 'audience': 'ANY', 'pagination': {'page': 1, 'pageSize': 150}}])
        self.assertEqual(len(titles), 3)

    def test_bonus_flag(self):
        body = fixture('GetFetchTitleDetailQuery')
        body['data']['title']['overlay'] = {'name': 'Bonus Borrow'}
        d, _ = make_dao({'GetFetchTitleDetailQuery': FakeResponse(body)})
        self.assertTrue(d.title('2000001').bonus)
        d, _ = make_dao()
        self.assertFalse(d.title('2000001').bonus)

    def test_per_kind_fallback(self):
        d, sess = make_dao({'GetCollectionsListQuery': collections_response(
            'Bonus Borrows October 2026 | All Movies', 'Bonus Borrows October 2026 | All Titles', 'Action-Packed Movies')})
        titles = d.bonus_titles(['7'])
        searches = [p['variables']['criteria'] for p in sess.operations if p['operationName'] == 'GetFilterSearchQuery']
        self.assertEqual(searches, [{'collectionId': '30000', 'audience': 'ANY', 'kindId': '7',
                                     'pagination': {'page': 1, 'pageSize': 150}}])
        self.assertEqual(len(titles), 3)

    def test_none_this_month(self):
        d, sess = make_dao({'GetCollectionsListQuery': collections_response('New to hoopla')})
        self.assertEqual(d.bonus_titles(['7', '9']), [])
        self.assertFalse(any(p['operationName'] == 'GetFilterSearchQuery' for p in sess.operations))


class PageSizeTest(unittest.TestCase):
    def test_page_size_capped(self):
        d, sess = make_dao()
        page = d.genre_titles('1', page=2, page_size=500)
        self.assertEqual(sess.operations[-1]['variables']['criteria']['pagination'], {'page': 2, 'pageSize': 150})
        self.assertEqual(page.page_size, 150, 'has_more must use the size actually served')
        d.bingepass_titles(page=1, page_size=500)
        self.assertEqual(sess.operations[-1]['variables']['pagination'], {'page': 1, 'pageSize': 150})

    def test_default_page_size_is_the_largest(self):
        d, sess = make_dao()
        self.assertEqual(d.genre_titles('1').page_size, 150)
        self.assertEqual(sess.operations[-1]['variables']['criteria']['pagination'], {'page': 1, 'pageSize': 150})


class RateTest(unittest.TestCase):
    def test_rate(self):
        d, sess = make_dao()
        d.rate('2000001', 4)
        [req] = sess.rest
        self.assertEqual((req.method, req.url), ('POST', f'{GATEWAY}/core/titles/2000001/patron-ratings'))
        self.assertEqual(req.data, {'stars': 4})
        self.assertEqual((req.headers['patron-id'], req.headers['Content-Type']),
                         ('800000001', 'application/x-www-form-urlencoded'))

    def test_rate_range(self):
        d, sess = make_dao()
        for stars in (0, 6):
            with self.assertRaises(ValueError):
                d.rate('2000001', stars)
        self.assertEqual(sess.sent, [])


class ErrorTest(unittest.TestCase):
    def test_graphql_errors(self):
        d, _ = make_dao({'GetKindsQuery': FakeResponse({'errors': [{'message': 'boom'}], 'data': None})})
        with self.assertRaises(dao.LibraryError) as cm:
            d.kinds()
        self.assertNotIsInstance(cm.exception, dao.AuthError)
        self.assertIn('boom', str(cm.exception))

    def test_graphql_unauthenticated(self):
        body = {'errors': [{'message': 'nope', 'extensions': {'code': 'UNAUTHENTICATED'}}]}
        d, _ = make_dao({'GetBorrowedTitlesQuery': FakeResponse(body)})
        with self.assertRaises(dao.AuthError):
            d.borrowed()

    def test_http_401(self):
        d, _ = make_dao({'GetRemainingBorrowsQuery': FakeResponse(status_code=401, reason='Unauthorized')})
        with self.assertRaises(dao.AuthError):
            d.borrow_limits()

    def test_http_500(self):
        d, _ = make_dao({'GetKindsQuery': FakeResponse(status_code=500, reason='Server Error')})
        with self.assertRaises(dao.LibraryError) as cm:
            d.kinds()
        self.assertNotIsInstance(cm.exception, dao.AuthError)

    def test_non_json(self):
        d, _ = make_dao({'GetKindsQuery': FakeResponse(None)})
        with self.assertRaises(dao.LibraryError):
            d.kinds()


class AuthTest(unittest.TestCase):
    def test_login_sets_bearer(self):
        d, sess = make_dao({TOKENS_URL: FakeResponse({'tokenStatus': 'SUCCESS', 'token': 'abc'})})
        self.assertEqual(d.login('user', 'pw'), 'abc')
        self.assertEqual(sess.headers['Authorization'], 'Bearer abc')
        self.assertEqual(d.session_token, 'abc')
        login = sess.sent[-1]
        self.assertEqual((login.method, login.url, login.data), ('POST', TOKENS_URL, {'username': 'user', 'password': 'pw'}))
        self.assertIsNone(login.json, 'login must be form-encoded; a JSON body gets a 500')

    def test_login_failure_status(self):
        d, sess = make_dao({TOKENS_URL: FakeResponse({'tokenStatus': 'INVALID_CREDENTIALS'})})
        with self.assertRaises(dao.AuthError):
            d.login('user', 'bad')
        self.assertNotIn('Authorization', sess.headers)

    def test_login_http_401(self):
        d, _ = make_dao({TOKENS_URL: FakeResponse(status_code=401, reason='Unauthorized')})
        with self.assertRaises(dao.AuthError):
            d.login('user', 'bad')

    def test_initial_token_applied(self):
        _, sess = make_dao(token='saved')
        self.assertEqual(sess.headers['Authorization'], 'Bearer saved')

    def test_instances_do_not_share_headers(self):
        a = HooplaGraphQLDAO(session_token='one')
        b = HooplaGraphQLDAO()
        self.assertEqual(a.sess.headers['Authorization'], 'Bearer one')
        self.assertNotIn('Authorization', b.sess.headers)


if __name__ == '__main__':
    unittest.main()
