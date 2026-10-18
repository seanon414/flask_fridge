from datetime import datetime

from app import db

MODES = ('auto', 'on', 'off')


class Temp(db.Model):
    """Single-row settings table shared by the web app and the controller."""
    id = db.Column(db.Integer, primary_key=True)
    target_temp = db.Column(db.Float)
    current_temp = db.Column(db.Integer)
    hysteresis = db.Column(db.Float, nullable=False, default=1.0, server_default='1.0')
    min_cycle_seconds = db.Column(db.Integer, nullable=False, default=300, server_default='300')
    mode = db.Column(db.String(8), nullable=False, default='auto', server_default='auto')
    # How long the temperature keeps falling after the compressor stops, in minutes.
    # 0 = plain thermostat; see control_logic.decide and app.scripts.coast.
    coast_minutes = db.Column(db.Float, nullable=False, default=0.0, server_default='0')


class Reading(db.Model):
    """One temperature sample logged by the controller."""
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    temp_f = db.Column(db.Float)  # NULL when the sensor could not be read
    target_f = db.Column(db.Float)
    fridge_on = db.Column(db.Boolean)


class Event(db.Model):
    """A setting change made from the web UI, shown as a marker on the chart."""
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    kind = db.Column(db.String(16), nullable=False)    # 'settings' or 'mode'
    detail = db.Column(db.String(120), nullable=False)


class AlertState(db.Model):
    """Whether each alert condition is currently active, and when we last told you."""
    key = db.Column(db.String(32), primary_key=True)
    active = db.Column(db.Boolean, nullable=False, default=False)
    since = db.Column(db.DateTime)
    last_notified = db.Column(db.DateTime)
    message = db.Column(db.String(200), nullable=False, default='')


def get_settings():
    """Return the settings row, creating it with defaults if it doesn't exist."""
    settings = db.session.get(Temp, 1)
    if settings is None:
        settings = Temp(id=1, target_temp=45.0, hysteresis=1.0,
                        min_cycle_seconds=300, mode='auto')
        db.session.add(settings)
        db.session.commit()
    return settings
