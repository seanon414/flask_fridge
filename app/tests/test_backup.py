"""Nightly backup tests. Real SQLite files in temporary folders; nothing else is touched."""
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime

from app.scripts.backup import BackupError, backup, db_path_from_uri


def make_db(path, rows=3):
    conn = sqlite3.connect(path)
    conn.execute('create table reading (id integer primary key, temp_f real)')
    conn.executemany('insert into reading (temp_f) values (?)', [(37.0 + i,) for i in range(rows)])
    conn.commit()
    conn.close()


def read_temps(path):
    conn = sqlite3.connect(path)
    try:
        return [r[0] for r in conn.execute('select temp_f from reading order by id')]
    finally:
        conn.close()


class BackupTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.db = os.path.join(self.dir, 'live.db')
        self.dest = os.path.join(self.dir, 'backups')

    def stamps(self):
        return sorted(os.listdir(self.dest))

    def test_copies_all_the_data(self):
        make_db(self.db, rows=5)
        target = backup(self.db, self.dest, now=datetime(2026, 10, 18, 3, 30, 0))
        self.assertEqual(os.path.basename(target), 'app.db.2026-10-18-033000')
        self.assertEqual(read_temps(target), read_temps(self.db))
        self.assertEqual(len(read_temps(target)), 5)

    def test_the_backup_is_independent_of_later_changes(self):
        make_db(self.db, rows=2)
        target = backup(self.db, self.dest)
        conn = sqlite3.connect(self.db)
        conn.execute('insert into reading (temp_f) values (99.0)')
        conn.commit()
        conn.close()
        self.assertEqual(len(read_temps(target)), 2)

    def test_keeps_only_the_newest_copies(self):
        make_db(self.db)
        for day in range(1, 6):
            backup(self.db, self.dest, keep=3, now=datetime(2026, 10, day, 3, 30, 0))
        self.assertEqual(self.stamps(), ['app.db.2026-10-03-033000', 'app.db.2026-10-04-033000',
                                         'app.db.2026-10-05-033000'])

    def test_keeps_everything_while_under_the_limit(self):
        make_db(self.db)
        for day in range(1, 4):
            backup(self.db, self.dest, keep=14, now=datetime(2026, 10, day, 3, 30, 0))
        self.assertEqual(len(self.stamps()), 3)

    def test_does_not_delete_unrelated_files_in_the_folder(self):
        make_db(self.db)
        os.makedirs(self.dest)
        notes = os.path.join(self.dest, 'notes.txt')
        with open(notes, 'w') as handle:
            handle.write('keep me')
        for day in range(1, 5):
            backup(self.db, self.dest, keep=1, now=datetime(2026, 10, day, 3, 30, 0))
        self.assertTrue(os.path.exists(notes))

    def test_refuses_a_missing_database_and_does_not_create_one(self):
        with self.assertRaises(BackupError):
            backup(self.db, self.dest)
        self.assertFalse(os.path.exists(self.db))

    def test_refuses_an_empty_file(self):
        open(self.db, 'wb').close()                  # what the clobbered database looked like
        with self.assertRaises(BackupError):
            backup(self.db, self.dest)

    def test_refuses_a_database_with_no_tables(self):
        conn = sqlite3.connect(self.db)
        conn.execute('create table t (x)')
        conn.execute('drop table t')
        conn.commit()
        conn.close()
        with self.assertRaises(BackupError):
            backup(self.db, self.dest)

    def test_a_refused_backup_never_pushes_out_good_ones(self):
        make_db(self.db)
        backup(self.db, self.dest, keep=1, now=datetime(2026, 10, 1, 3, 30, 0))
        open(self.db, 'wb').close()
        with self.assertRaises(BackupError):
            backup(self.db, self.dest, keep=1, now=datetime(2026, 10, 2, 3, 30, 0))
        self.assertEqual(self.stamps(), ['app.db.2026-10-01-033000'])


class DbPathTests(unittest.TestCase):
    def test_absolute_path(self):
        self.assertEqual(db_path_from_uri('sqlite:////home/pi/keezer-data/app.db'), '/home/pi/keezer-data/app.db')

    def test_relative_path(self):
        self.assertEqual(db_path_from_uri('sqlite:///x.db'), 'x.db')

    def test_in_memory_and_other_databases_are_not_backed_up(self):
        for uri in ('sqlite://', 'sqlite:///:memory:', 'postgresql://u@h/db', 'sqlite:///'):
            with self.assertRaises(BackupError, msg=uri):
                db_path_from_uri(uri)


if __name__ == '__main__':
    unittest.main()
