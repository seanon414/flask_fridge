from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request

from app import db
from app.models.temp import MODES, Reading, get_settings

api_blueprint = Blueprint('api', __name__, url_prefix='/api')

# The controller logs every 30s; call it dead after four missed cycles.
STALE_AFTER_SECONDS = 120
MAX_HISTORY_POINTS = 300
MAX_HISTORY_HOURS = 24 * 30

TARGET_RANGE = (20, 70)
HYSTERESIS_RANGE = (0.5, 10.0)


def _iso(dt):
    return dt.isoformat() + 'Z'  # timestamps are stored as naive UTC


def _settings_json(settings):
    return {
        'target_temp': settings.target_temp,
        'hysteresis': settings.hysteresis,
        'min_cycle_seconds': settings.min_cycle_seconds,
        'mode': settings.mode,
    }


@api_blueprint.route('/status')
def status():
    settings = get_settings()
    last = Reading.query.order_by(Reading.timestamp.desc()).first()

    age = None
    if last is not None:
        age = (datetime.utcnow() - last.timestamp).total_seconds()

    return jsonify({
        'settings': _settings_json(settings),
        'temp_f': last.temp_f if last else None,
        'fridge_on': last.fridge_on if last else None,
        'reading_at': _iso(last.timestamp) if last else None,
        'age_seconds': age,
        'controller_ok': age is not None and age <= STALE_AFTER_SECONDS,
        'sensor_ok': bool(last and last.temp_f is not None),
    })


def _downsample(rows, max_points):
    """Average rows into at most max_points buckets, oldest first."""
    if not rows:
        return []
    stride = -(-len(rows) // max_points)  # ceil
    points = []
    for i in range(0, len(rows), stride):
        bucket = rows[i:i + stride]
        temps = [r.temp_f for r in bucket if r.temp_f is not None]
        on_flags = [r.fridge_on for r in bucket if r.fridge_on is not None]
        points.append({
            't': _iso(bucket[0].timestamp),
            'temp': round(sum(temps) / len(temps), 2) if temps else None,
            'target': bucket[-1].target_f,
            # a bucket counts as "on" if the fridge ran for most of it
            'on': (sum(1 for f in on_flags if f) * 2 >= len(on_flags)) if on_flags else None,
        })
    return points


@api_blueprint.route('/history')
def history():
    try:
        hours = float(request.args.get('hours', 24))
    except ValueError:
        return jsonify(error='hours must be a number'), 400
    hours = min(max(hours, 0.5), MAX_HISTORY_HOURS)

    since = datetime.utcnow() - timedelta(hours=hours)
    rows = Reading.query.filter(Reading.timestamp >= since) \
        .order_by(Reading.timestamp).all()

    temps = [r.temp_f for r in rows if r.temp_f is not None]
    known = [r for r in rows if r.fridge_on is not None]
    stats = {
        'min': min(temps) if temps else None,
        'max': max(temps) if temps else None,
        'avg': round(sum(temps) / len(temps), 2) if temps else None,
        'duty_percent': round(100.0 * sum(1 for r in known if r.fridge_on) / len(known), 1)
        if known else None,
        'samples': len(rows),
    }
    return jsonify({
        'hours': hours,
        'points': _downsample(rows, MAX_HISTORY_POINTS),
        'stats': stats,
    })


def _bad(message):
    return jsonify(error=message), 400


@api_blueprint.route('/settings', methods=['POST'])
def update_settings():
    data = request.get_json(silent=True) or {}
    try:
        target = int(data['target_temp'])
        hysteresis = float(data['hysteresis'])
    except (KeyError, TypeError, ValueError):
        return _bad('target_temp (integer) and hysteresis (number) are required')

    if not TARGET_RANGE[0] <= target <= TARGET_RANGE[1]:
        return _bad('target_temp must be between {} and {}'.format(*TARGET_RANGE))
    if not HYSTERESIS_RANGE[0] <= hysteresis <= HYSTERESIS_RANGE[1]:
        return _bad('hysteresis must be between {} and {}'.format(*HYSTERESIS_RANGE))

    settings = get_settings()
    settings.target_temp = target
    settings.hysteresis = round(hysteresis, 1)
    db.session.commit()
    return jsonify(_settings_json(settings))


@api_blueprint.route('/mode', methods=['POST'])
def update_mode():
    data = request.get_json(silent=True) or {}
    mode = data.get('mode')
    if mode not in MODES:
        return _bad('mode must be one of: ' + ', '.join(MODES))

    settings = get_settings()
    settings.mode = mode
    db.session.commit()
    return jsonify(_settings_json(settings))
