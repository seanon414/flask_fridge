"""Measure and set the freezer's coast time. Run on the Pi:

    python -m app.scripts.coast              # study the last 3 days, suggest a value
    python -m app.scripts.coast --days 7
    python -m app.scripts.coast set 18       # coast-aware control on (minutes)
    python -m app.scripts.coast set 0        # back to a plain thermostat

After the compressor stops, a chest freezer keeps cooling for a while. The
controller can allow for that by stopping early (see control_logic.decide).
How early depends on one number, the coast time in minutes: the temperature
keeps falling by about (cooling rate x coast time) after a stop. This tool
measures it from the logged readings. The controller reads the setting every
cycle, so `set` takes effect within half a minute without a restart.
"""
import argparse
import statistics
import sys
from datetime import datetime, timedelta, timezone

from app.scripts.control_logic import cooling_rate

MAX_GAP_SECONDS = 90          # a longer silence means a restart or lost data: skip that cycle
RATE_WINDOW_SECONDS = 360
MIN_RATE = 0.02               # F/min; a slower fall is too noisy to divide by
MIN_CYCLES = 3                # fewer than this is a hint, not a measurement


def find_stops(rows):
    """Every completed compressor stop in rows, with how far it coasted.

    rows: (seconds, temp_f, fridge_on) oldest first (temp_f may be None).
    A stop counts only if the readings are unbroken from a few minutes before
    it until the compressor started again, so the bottom is a real bottom.
    """
    stops = []
    for i in range(1, len(rows)):
        prev, cur = rows[i - 1], rows[i]
        if not (prev[2] is True and cur[2] is False and cur[1] is not None):
            continue

        window = [(t, f) for t, f, _ in rows[:i + 1] if f is not None and t >= cur[0] - RATE_WINDOW_SECONDS]
        if any(b[0] - a[0] > MAX_GAP_SECONDS for a, b in zip(window, window[1:])):
            continue
        rate = cooling_rate(window, window_seconds=RATE_WINDOW_SECONDS)

        j = i + 1
        while j < len(rows) and not (rows[j][2] is True):
            j += 1
        if j >= len(rows):
            continue                                  # still coasting when the data ends
        between = rows[i:j + 1]
        if any(b[0] - a[0] > MAX_GAP_SECONDS for a, b in zip(between, between[1:])):
            continue
        temps = [f for _, f, _ in rows[i:j] if f is not None]
        if not temps:
            continue

        undershoot = max(0.0, cur[1] - min(temps))
        usable = rate is not None and rate >= MIN_RATE and undershoot > 0
        stops.append({'at': cur[0], 'stop_temp': cur[1], 'rate': rate, 'bottom': min(temps),
                      'undershoot': undershoot, 'coast_minutes': undershoot / rate if usable else None})
    return stops


def suggest(stops):
    """Median coast time over the stops that could be measured, or None."""
    values = [s['coast_minutes'] for s in stops if s['coast_minutes'] is not None]
    return round(statistics.median(values), 1) if values else None


def _load_rows(days):
    from app.models.temp import Reading
    since = datetime.utcnow() - timedelta(days=days)
    epoch = datetime(1970, 1, 1)
    query = Reading.query.filter(Reading.timestamp >= since).order_by(Reading.timestamp)
    return [((r.timestamp - epoch).total_seconds(), r.temp_f, r.fridge_on) for r in query]


def _local(seconds):
    return datetime.fromtimestamp(seconds, tz=timezone.utc).astimezone().strftime('%a %H:%M')


def analyze(days, out=print):
    from app.models.temp import get_settings
    settings = get_settings()
    rows = _load_rows(days)
    stops = find_stops(rows)
    out('Coast study, last {:g} day(s): {} readings, {} complete compressor stops'.format(
        days, len(rows), len(stops)))
    out('Current setting: coast_minutes = {:g}{}'.format(
        settings.coast_minutes or 0, '  (off: plain thermostat)' if not settings.coast_minutes else ''))
    if stops:
        out('')
        out('  stopped at   temp    falling   bottom   coasted   coast time')
        for s in stops:
            out('  {:<11}  {:5.2f}  {:>7}   {:6.2f}   {:5.2f}F   {}'.format(
                _local(s['at']), s['stop_temp'],
                'n/a' if s['rate'] is None else '{:.3f}/m'.format(s['rate']), s['bottom'],
                s['undershoot'],
                'n/a' if s['coast_minutes'] is None else '{:.1f} min'.format(s['coast_minutes'])))
    value = suggest(stops)
    measured = sum(1 for s in stops if s['coast_minutes'] is not None)
    out('')
    if value is None:
        out('Not enough complete cycles to suggest a value yet. Let it run for a day or two.')
    else:
        confidence = '' if measured >= MIN_CYCLES else '  (only {} cycle(s): treat as a rough guess)'.format(measured)
        out('Suggested: python -m app.scripts.coast set {:g}{}'.format(value, confidence))
    return value


def set_coast(minutes, out=print):
    from app import db
    from app.models.temp import Event, get_settings
    if not 0 <= minutes <= 60:
        raise ValueError('minutes must be between 0 and 60')
    minutes = round(minutes, 1)
    settings = get_settings()
    old = settings.coast_minutes or 0
    if old != minutes:
        db.session.add(Event(kind='settings', detail='Coast {:.1f} → {:.1f} min'.format(old, minutes)))
    settings.coast_minutes = minutes
    db.session.commit()
    out('coast_minutes: {:g} -> {:g}{}'.format(old, minutes, '' if minutes else '  (plain thermostat)'))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--days', type=float, default=3, help='how much history to study (default 3)')
    sub = parser.add_subparsers(dest='command')
    setter = sub.add_parser('set', help='turn coast-aware control on (minutes) or off (0)')
    setter.add_argument('minutes', type=float)
    args = parser.parse_args(argv)

    from app import app
    with app.app_context():
        if args.command == 'set':
            try:
                set_coast(args.minutes)
            except ValueError as exc:
                print('error: {}'.format(exc))
                return 2
        else:
            analyze(args.days)
    return 0


if __name__ == '__main__':
    sys.exit(main())
