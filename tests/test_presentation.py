import unittest
from datetime import datetime, timedelta, timezone

import dao
from presentation import (BONUS_TEXT, borrow_allowance, by_season, content_for, included_passes, loan_due,
                          paragraphs, playable, time_left, without_downloads)

NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)


def title(kind=dao.MOVIE, **kw):
    return dao.Title(id='1', title='T', kind=kind, **kw)


class BorrowAllowanceTest(unittest.TestCase):
    LIMITS = dao.BorrowLimits(message='all', instant_message='You have 5 Instant Borrows remaining this month.',
                              flex_message='flex')

    def test_bonus(self):
        self.assertEqual(borrow_allowance(title(bonus=True), self.LIMITS), BONUS_TEXT)
        self.assertEqual(borrow_allowance(title(bonus=True), None), BONUS_TEXT)

    def test_instant_and_flex(self):
        self.assertEqual(borrow_allowance(title(borrow_type=dao.INSTANT), self.LIMITS), self.LIMITS.instant_message)
        self.assertEqual(borrow_allowance(title(borrow_type=dao.FLEX), self.LIMITS), 'flex')
        self.assertEqual(borrow_allowance(title(), None), '')


class TimeLeftTest(unittest.TestCase):
    def test_time_left(self):
        cases = [(timedelta(hours=-1), 'due now'), (timedelta(minutes=20), '1 hour left'),
                 (timedelta(hours=5, minutes=30), '5 hours left'), (timedelta(days=1), '1 day left'),
                 (timedelta(days=2, hours=23), '3 days left')]
        for delta, text in cases:
            self.assertEqual(time_left(NOW + delta, now=NOW), text, delta)

    def test_loan_due_of_season_is_soonest_borrowed_episode(self):
        soon, later = NOW + timedelta(days=1), NOW + timedelta(days=2)
        t = title(dao.TELEVISION, episodes=[
            dao.Episode(id='1', number=1, title='', borrowed=True, due=later),
            dao.Episode(id='2', number=2, title='', borrowed=True, due=soon),
            dao.Episode(id='3', number=3, title='', borrowed=False, due=NOW),
        ])
        self.assertEqual(loan_due(t), soon)
        self.assertEqual(loan_due(title(due=later)), later)
        self.assertIsNone(loan_due(title()))


class ListingTest(unittest.TestCase):
    def test_content_for(self):
        self.assertEqual(content_for([title(), title()]), 'movies')
        self.assertEqual(content_for([title(dao.TELEVISION)]), 'tvshows')
        self.assertEqual(content_for([title(), title(dao.TELEVISION)]), 'videos')
        self.assertEqual(content_for([]), 'videos')

    def test_by_season(self):
        def season(name, number):
            return dao.Title(id=name, title=name, kind=dao.TELEVISION, season=number)
        titles = [season('Show - Season 10', 10), dao.Title(id='A Movie', title='A Movie', kind=dao.MOVIE),
                  season('Show - Season 2', 2), season('Show', None)]
        self.assertEqual([t.id for t in by_season(titles)],
                         ['Show', 'Show - Season 2', 'Show - Season 10', 'A Movie'])

    def test_playable(self):
        titles = [title(), title(dao.TELEVISION), title(dao.BINGEPASS), title('EBOOK')]
        self.assertEqual([t.kind for t in playable(titles)], [dao.MOVIE, dao.TELEVISION])
        self.assertEqual(playable(None), [])

    def test_included_passes(self):
        video = title(dao.BINGEPASS, bingepass_type=dao.BINGEPASS_INCLUDED, content_kinds=[dao.TELEVISION])
        unknown = title(dao.BINGEPASS, bingepass_type=dao.BINGEPASS_INCLUDED)
        books = title(dao.BINGEPASS, bingepass_type=dao.BINGEPASS_INCLUDED, content_kinds=['EBOOK'])
        partner = title(dao.BINGEPASS, bingepass_type=dao.BINGEPASS_PARTNER)
        self.assertEqual(included_passes([video, unknown, books, partner, title()]), [video, unknown])


class TextTest(unittest.TestCase):
    def test_paragraphs(self):
        self.assertEqual(paragraphs('a', '', None, 'b'), 'a\n\nb')
        self.assertEqual(paragraphs(), '')

    def test_without_downloads(self):
        text = 'Borrow for 3 days. Download it to your phone! Enjoy.'
        self.assertEqual(without_downloads(text), 'Borrow for 3 days. Enjoy.')
        self.assertEqual(without_downloads(None), '')


if __name__ == '__main__':
    unittest.main()
