import os
import tempfile
import unittest

from accounts import Account, AccountStore, migrate_legacy


class AccountStoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, 'profile', 'accounts.json')

    def tearDown(self):
        self.dir.cleanup()

    def reload(self):
        return AccountStore(self.path)

    def test_empty(self):
        s = self.reload()
        self.assertFalse(s.existed)
        self.assertIsNone(s.current())
        self.assertEqual(s.others(), [])

    def test_add_select_and_persist(self):
        s = self.reload()
        s.add(Account('a@example.com', 'pa', name='Ann'))
        s.add(Account('b@example.com', 'pb', name='Bob'))
        self.assertEqual(s.current().username, 'b@example.com')  # the newest user is current
        s.select('a@example.com')
        s = self.reload()
        self.assertTrue(s.existed)
        self.assertEqual(s.current().name, 'Ann')
        self.assertEqual([a.name for a in s.others()], ['Bob'])

    def test_add_existing_replaces_password_and_keeps_name(self):
        s = self.reload()
        s.add(Account('a@example.com', 'old', name='Ann', token='t'))
        s.add(Account('a@example.com', 'new', token='t2'))
        s = self.reload()
        self.assertEqual(len(s.accounts), 1)
        self.assertEqual((s.current().password, s.current().name, s.current().token), ('new', 'Ann', 't2'))

    def test_remove_current_falls_back(self):
        s = self.reload()
        s.add(Account('a', 'p', name='Ann'))
        s.add(Account('b', 'p', name='Bob'))
        s.remove('b')
        self.assertEqual(self.reload().current().username, 'a')
        s.remove('a')
        self.assertIsNone(self.reload().current())

    def test_token_and_name(self):
        s = self.reload()
        s.add(Account('a', 'p'))
        self.assertEqual(s.current().label, 'a')  # no name yet: the username
        s.set_token('a', 'tok')
        s.set_name('a', 'Ann')
        s = self.reload()
        self.assertEqual((s.current().token, s.current().label), ('tok', 'Ann'))

    def test_unreadable_file_starts_empty(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, 'w') as f:
            f.write('not json')
        self.assertIsNone(self.reload().current())

    def test_migrate_legacy(self):
        s = self.reload()
        self.assertTrue(migrate_legacy(s, 'a@example.com', 'p', 'tok', 'a'))
        s = self.reload()
        self.assertEqual((s.current().username, s.current().token, s.current().label), ('a@example.com', 'tok', 'a'))
        self.assertFalse(migrate_legacy(s, 'x', 'y'))  # only once: the store now exists

    def test_migrate_without_login(self):
        s = self.reload()
        self.assertFalse(migrate_legacy(s, '', ''))
        self.assertFalse(os.path.exists(self.path))


if __name__ == '__main__':
    unittest.main()
