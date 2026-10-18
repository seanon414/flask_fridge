import os
import time
from datetime import datetime, timedelta

from flask import Blueprint, current_app, jsonify, request

from app import db
from app.models.temp import MODES, AlertState, Event, Reading, get_settings

api_blueprint = Blueprint('api', __name__, url_prefix='/api')

# The controller logs every 30s; call it dead after four missed cycles.
STALE_AFTER_SECONDS = 120
MAX_HISTORY_POINTS = 300
MAX_HISTORY_EVENTS = 200
MAX_HISTORY_HOURS = 24 * 30

TARGET_RANGE = (20.0, 70.0)
HYSTERESIS_RANGE = (0.5, 10.0)
COAST_RANGE = (0.0, 60.0)    # minutes; 0 turns coast-aware control off

MODE_LABELS = {'auto': 'Auto', 'on': 'Forced on', 'off': 'Forced off'}


_STARTED = int(time.time())


def _asset_version():
    """Changes whenever the server restarts or a page/script/style file changes.

    The page polls this and reloads itself when it differs from the one it
    loaded with, so an always-on wall display picks up updates by itself.
    """
    newest = 0
    for folder in (current_app.static_folder,
                   os.path.join(current_app.root_path, current_app.template_folder or 'templates')):
        try:
            for entry in os.scandir(folder):
                if entry.is_file():
                    newest = max(newest, int(entry.stat().st_mtime))
        except OSError:
            continue
    return '{}.{}'.format(_STARTED, newest)


def _iso(dt):
    return dt.isoformat() + 'Z'  # timestamps are stored as naive UTC


def _settings_json(settings):
    return {
        'target_temp': settings.target_temp,
        'hysteresis': settings.hysteresis,
        'min_cycle_seconds': settings.min_cycle_seconds,
        'coast_minutes': settings.coast_minutes,
        'mode': settings.mode,
    }


@api_blueprint.route('/status')
def status():
    settings = get_settings()
    last = Reading.query.order_by(Reading.timestamp.desc()).first()

    age = None
    if last is not None:
        age = (datetime.utcnow() - last.timestamp).total_seconds()

    active_alerts = AlertState.query.filter_by(active=True).order_by(AlertState.since).all()

    return jsonify({
        'settings': _settings_json(settings),
        'temp_f': last.temp_f if last else None,
        'fridge_on': last.fridge_on if last else None,
        'reading_at': _iso(last.timestamp) if last else None,
        'age_seconds': age,
        'controller_ok': age is not None and age <= STALE_AFTER_SECONDS,
        'sensor_ok': bool(last and last.temp_f is not None),
        'alerts': [{'key': a.key, 'message': a.message,
                    'since': _iso(a.since) if a.since else None} for a in active_alerts],
        'notifications_enabled': bool(os.environ.get('NTFY_TOPIC', '').strip()),
        'version': _asset_version(),
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
    events = Event.query.filter(Event.timestamp >= since) \
        .order_by(Event.timestamp).limit(MAX_HISTORY_EVENTS).all()

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
        'events': [{'t': _iso(e.timestamp), 'kind': e.kind, 'detail': e.detail} for e in events],
        'stats': stats,
    })


def _bad(message):
    return jsonify(error=message), 400


def _log_event(kind, detail):
    db.session.add(Event(kind=kind, detail=detail))


def _fmt(value):
    return '{:.1f}'.format(value)


@api_blueprint.route('/settings', methods=['POST'])
def update_settings():
    data = request.get_json(silent=True) or {}
    try:
        target = float(data['target_temp'])
        hysteresis = float(data['hysteresis'])
    except (KeyError, TypeError, ValueError):
        return _bad('target_temp and hysteresis (numbers) are required')

    if abs(target * 2 - round(target * 2)) > 1e-9:
        return _bad('target_temp must be a multiple of 0.5')
    if not TARGET_RANGE[0] <= target <= TARGET_RANGE[1]:
        return _bad('target_temp must be between {:g} and {:g}'.format(*TARGET_RANGE))
    if not HYSTERESIS_RANGE[0] <= hysteresis <= HYSTERESIS_RANGE[1]:
        return _bad('hysteresis must be between {:g} and {:g}'.format(*HYSTERESIS_RANGE))
    hysteresis = round(hysteresis, 1)

    coast = None    # optional: leaving it out keeps the current value
    if data.get('coast_minutes') is not None:
        try:
            coast = float(data['coast_minutes'])
        except (TypeError, ValueError):
            return _bad('coast_minutes must be a number')
        if not COAST_RANGE[0] <= coast <= COAST_RANGE[1]:
            return _bad('coast_minutes must be between {:g} and {:g}'.format(*COAST_RANGE))
        coast = round(coast, 1)

    settings = get_settings()
    changes = []
    if settings.target_temp != target:
        changes.append('Target {} → {}°F'.format(
            _fmt(settings.target_temp) if settings.target_temp is not None else '--', _fmt(target)))
    if settings.hysteresis != hysteresis:
        changes.append('Swing ±{} → ±{}°F'.format(_fmt(settings.hysteresis), _fmt(hysteresis)))
    if coast is not None and settings.coast_minutes != coast:
        changes.append('Coast {} → {} min'.format(_fmt(settings.coast_minutes or 0), _fmt(coast)))

    settings.target_temp = target
    settings.hysteresis = hysteresis
    if coast is not None:
        settings.coast_minutes = coast
    if changes:
        _log_event('settings', ', '.join(changes))
    db.session.commit()
    return jsonify(_settings_json(settings))


@api_blueprint.route('/mode', methods=['POST'])
def update_mode():
    data = request.get_json(silent=True) or {}
    mode = data.get('mode')
    if mode not in MODES:
        return _bad('mode must be one of: ' + ', '.join(MODES))

    settings = get_settings()
    if settings.mode != mode:
        _log_event('mode', '{} → {}'.format(
            MODE_LABELS.get(settings.mode, settings.mode), MODE_LABELS[mode]))
    settings.mode = mode
    db.session.commit()
    return jsonify(_settings_json(settings))
