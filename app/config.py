import logging
import os

basedir = os.path.abspath(os.path.dirname(__file__))

logger = logging.getLogger(__name__)


def database_uri(environ=None, home=None, legacy=None):
    """Where the SQLite database lives.

    1. DATABASE_URL, if set (tests use sqlite://).
    2. ~/keezer-data/app.db (or $KEEZER_DATA_DIR/app.db). This is outside the git
       repo on purpose: git can silently overwrite or delete files inside it
       during a checkout or pull.
    3. Existing installs: if the old in-repo app/app.db has data and the new
       location doesn't exist yet, keep using it until you move it. That way
       upgrading never starts the app on a fresh empty database.
    """
    environ = os.environ if environ is None else environ
    if environ.get('DATABASE_URL'):
        return environ['DATABASE_URL']

    home = home or os.path.expanduser('~')
    legacy = legacy or os.path.join(basedir, 'app.db')
    data_dir = environ.get('KEEZER_DATA_DIR') or os.path.join(home, 'keezer-data')
    path = os.path.join(data_dir, 'app.db')

    if not os.path.exists(path) and os.path.exists(legacy) and os.path.getsize(legacy) > 0:
        logger.warning('Using the old database at %s. Move it to %s (see README, "Data and backups").',
                       legacy, path)
        return 'sqlite:///' + legacy

    os.makedirs(data_dir, exist_ok=True)
    return 'sqlite:///' + path


class Config(object):
    SQLALCHEMY_DATABASE_URI = database_uri()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
