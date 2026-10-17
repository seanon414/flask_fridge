from datetime import datetime

from app import db

MODES = ('auto', 'on', 'off')


class Temp(db.Model):
    """Single-row settings table shared by the web app and the controller."""
    id = db.Column(db.Integer, primary_key=True)
    target_temp = db.Column(db.Integer)
    current_temp = db.Column(db.Integer)
    hysteresis = db.Column(db.Float, nullable=False, default=1.0, server_default='1.0')
    min_cycle_seconds = db.Column(db.Integer, nullable=False, default=300, server_default='300')
    mode = db.Column(db.String(8), nullable=False, default='auto', server_default='auto')


class Reading(db.Model):
    """One temperature sample logged by the controller."""
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    temp_f = db.Column(db.Float)  # NULL when the sensor could not be read
    target_f = db.Column(db.Float)
    fridge_on = db.Column(db.Boolean)


def get_settings():
    """Return the settings row, creating it with defaults if it doesn't exist."""
    settings = Temp.query.get(1)
    if settings is None:
        settings = Temp(id=1, target_temp=45, hysteresis=1.0,
                        min_cycle_seconds=300, mode='auto')
        db.session.add(settings)
        db.session.commit()
    return settings
