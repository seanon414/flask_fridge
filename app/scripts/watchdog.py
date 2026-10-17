"""Tells you when the controller itself has stopped.

The controller can't report its own death, so this runs separately, on a
systemd timer every couple of minutes (see deploy/keezer-watchdog.*):

    python -m app.scripts.watchdog
"""
import logging
from datetime import datetime, timedelta

from app import app
from app.models.temp import Reading
from app.scripts.alerts import AlertManager, Notifier, _env_float

logger = logging.getLogger(__name__)


def check(manager=None, now=None, minutes=None):
    """Update the 'controller' alert. Returns True if the controller looks dead."""
    manager = manager or AlertManager(Notifier())
    now = now or datetime.utcnow()
    minutes = minutes if minutes is not None else _env_float('ALERT_CONTROLLER_MINUTES', 5)

    last = Reading.query.order_by(Reading.timestamp.desc()).first()
    if last is None:
        dead, message = True, 'No readings have ever been logged. The controller is not running.'
    else:
        silence = now - last.timestamp
        dead = silence > timedelta(minutes=minutes)
        message = 'The controller has not logged a reading for {:.0f} min. The freezer is not being regulated.'.format(
            silence.total_seconds() / 60.0)

    manager.update('controller', dead, message, now)
    return dead


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    with app.app_context():
        print('controller down' if check() else 'controller ok')
