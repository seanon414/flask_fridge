"""Where the database lives. Uses temporary folders only; never touches a real database."""
import os
import tempfile
import unittest

from app.config import database_uri


class DatabaseUriTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = os.path.join(tmp.name, 'home')
        self.repo = os.path.join(tmp.name, 'repo')
        os.makedirs(self.home)
        os.makedirs(self.repo)
        self.legacy = os.path.join(self.repo, 'app.db')
        self.new = os.path.join(self.home, 'keezer-data', 'app.db')

    def uri(self, **environ):
        return database_uri(environ=environ, home=self.home, legacy=self.legacy)

    def write(self, path, data=b'sqlite-ish bytes'):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as handle:
            handle.write(data)

    def test_explicit_database_url_always_wins(self):
        self.write(self.legacy)
        self.write(self.new)
        self.assertEqual(self.uri(DATABASE_URL='sqlite://'), 'sqlite://')

    def test_fresh_install_uses_the_data_dir_and_creates_it(self):
        self.assertEqual(self.uri(), 'sqlite:///' + self.new)
        self.assertTrue(os.path.isdir(os.path.dirname(self.new)))

    def test_data_dir_can_be_overridden(self):
        custom = os.path.join(self.home, 'elsewhere')
        self.assertEqual(self.uri(KEEZER_DATA_DIR=custom), 'sqlite:///' + os.path.join(custom, 'app.db'))
        self.assertTrue(os.path.isdir(custom))

    def test_existing_install_keeps_using_the_old_file_until_moved(self):
        self.write(self.legacy)
        with self.assertLogs('app.config', level='WARNING') as logs:
            self.assertEqual(self.uri(), 'sqlite:///' + self.legacy)
        self.assertIn('Move it', logs.output[0])
        self.assertFalse(os.path.exists(self.new))          # and it did not invent an empty one

    def test_the_new_location_wins_once_it_exists(self):
        self.write(self.legacy)
        self.write(self.new)
        self.assertEqual(self.uri(), 'sqlite:///' + self.new)

    def test_an_empty_old_file_is_ignored(self):
        self.write(self.legacy, b'')                         # the 0-byte file git left behind once
        self.assertEqual(self.uri(), 'sqlite:///' + self.new)

    def test_after_moving_the_old_file_away_it_uses_the_new_one(self):
        self.write(self.new)
        self.assertEqual(self.uri(), 'sqlite:///' + self.new)


if __name__ == '__main__':
    unittest.main()
