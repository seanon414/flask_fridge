"""Pure thermostat decision logic: no Flask, DB or hardware, so it is easy to test."""

# However fast the temperature is falling, never stop cooling more than this
# many degrees early. A bad rate reading must not be able to switch the
# compressor off far from where it should.
MAX_ANTICIPATION_F = 2.0


def cooling_rate(samples, window_seconds=360, min_span_seconds=150, min_samples=4):
    """How fast the temperature is falling, in degrees F per minute.

    samples is a list of (seconds, temp_f) from oldest to newest, valid
    readings only. The slope is a least-squares fit over the last
    window_seconds, which smooths out the sensor's coarse steps (about
    0.11F). Positive means cooling. Returns None when there is not enough
    recent data to say, e.g. just after a restart.
    """
    if not samples:
        return None
    newest = samples[-1][0]
    recent = [s for s in samples if s[0] >= newest - window_seconds]
    if len(recent) < min_samples or recent[-1][0] - recent[0][0] < min_span_seconds:
        return None

    mean_t = sum(t for t, _ in recent) / len(recent)
    mean_f = sum(f for _, f in recent) / len(recent)
    var = sum((t - mean_t) ** 2 for t, _ in recent)
    if var == 0:
        return None
    slope_per_second = sum((t - mean_t) * (f - mean_f) for t, f in recent) / var
    return -slope_per_second * 60.0


def decide(temp_f, target_f, hysteresis_f, is_on, seconds_since_change, min_cycle_seconds,
           coast_minutes=0.0, cooling_rate_f_per_min=None):
    """Return the desired fridge state (True=on, False=off).

    The fridge turns on above target + hysteresis and off below
    target - hysteresis; inside that band it keeps its current state.
    A state change is held back until min_cycle_seconds have passed since the
    last one, to protect the compressor from short-cycling.

    Coast awareness: a chest freezer keeps cooling for a while after the
    compressor stops, so waiting for the low threshold overshoots it. When
    coast_minutes > 0 and the temperature is falling, the fridge turns off
    early, once temp - rate * coast_minutes reaches the low threshold (the
    amount is capped at MAX_ANTICIPATION_F). With coast_minutes = 0 this is a
    plain thermostat.

    is_on may be None when the state is unknown (e.g. first start); then a
    plain thermostat decision is made against the target.
    """
    if temp_f is None:
        # No valid reading: hold whatever we're doing.
        return is_on

    if is_on is None:
        return temp_f >= target_f

    if is_on:
        anticipation = 0.0
        if coast_minutes and coast_minutes > 0 and cooling_rate_f_per_min and cooling_rate_f_per_min > 0:
            anticipation = min(cooling_rate_f_per_min * coast_minutes, MAX_ANTICIPATION_F)
        want_on = not (temp_f <= target_f - hysteresis_f + anticipation)
    else:
        want_on = temp_f >= target_f + hysteresis_f

    if want_on != is_on and seconds_since_change is not None \
            and seconds_since_change < min_cycle_seconds:
        return is_on
    return want_on
