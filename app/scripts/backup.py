"""Consistent backup of the SQLite database.

    python -m app.scripts.backup

Uses SQLite's own backup API, so it is safe while the controller and web app
are running (a plain `cp` can catch a write halfway). Each copy is checked, the
newest 14 are kept, and an empty or missing database is refused rather than
backed up. Run nightly by keezer-backup.timer; on failure it sends a push
notification if alerts are configured.

Settings (optional): KEEZER_BACKUP_DIR (default ~/keezer-backups),
KEEZER_BACKUP_KEEP (default 14).
"""
import glob
import logging
import os
import sqlite3
import sys
from datetime import datetime

logger = logging.getLogger(__name__)

PREFIX = 'app.db.'


class BackupError(Exception):
    pass


def db_path_from_uri(uri):
    """sqlite:////home/pi/x.db -> /home/pi/x.db. Anything else is not backed up."""
    prefix = 'sqlite:///'
    if uri.startswith(prefix) and len(uri) > len(prefix) and ':memory:' not in uri:
        return uri[len(prefix):]
    raise BackupError('Not a file-based SQLite database: {}'.format(uri))


def _check_source(path):
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        raise BackupError('Database is missing or empty: {}'.format(path))
    conn = sqlite3.connect(path)
    try:
        tables = conn.execute("select count(*) from sqlite_master where type='table'").fetchone()[0]
    finally:
        conn.close()
    if tables == 0:
        raise BackupError('Database has no tables, refusing to back it up: {}'.format(path))


def backup(db_path, dest_dir, keep=14, now=None):
    """Copy db_path into dest_dir as app.db.<timestamp>; returns the new path."""
    _check_source(db_path)
    now = now or datetime.now()
    os.makedirs(dest_dir, exist_ok=True)
    target = os.path.join(dest_dir, PREFIX + now.strftime('%Y-%m-%d-%H%M%S'))

    source = sqlite3.connect(db_path)
    copy = sqlite3.connect(target)
    try:
        source.backup(copy)
    finally:
        copy.close()
        source.close()

    check = sqlite3.connect(target)
    try:
        result = check.execute('pragma integrity_check').fetchone()[0]
    finally:
        check.close()
    if result != 'ok':
        os.remove(target)
        raise BackupError('Backup failed its integrity check: {}'.format(result))

    for old in sorted(glob.glob(os.path.join(dest_dir, PREFIX + '*')))[:-keep]:
        os.remove(old)
    return target


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    from app import app
    from app.scripts.alerts import Notifier

    dest = os.environ.get('KEEZER_BACKUP_DIR') or os.path.join(os.path.expanduser('~'), 'keezer-backups')
    try:
        keep = int(os.environ.get('KEEZER_BACKUP_KEEP', 14))
    except ValueError:
        keep = 14
    try:
        target = backup(db_path_from_uri(app.config['SQLALCHEMY_DATABASE_URI']), dest, keep)
    except Exception as exc:
        logger.error('Backup failed: %s', exc)
        Notifier().send('Keezer backup failed', str(exc), 'high', 'warning')
        return 1
    logger.info('Backed up to %s', target)
    return 0


if __name__ == '__main__':
    sys.exit(main())
