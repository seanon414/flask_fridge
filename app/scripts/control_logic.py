"""Pure thermostat decision logic: no Flask, DB or hardware, so it is easy to test."""


def decide(temp_f, target_f, hysteresis_f, is_on, seconds_since_change, min_cycle_seconds):
    """Return the desired fridge state (True=on, False=off).

    The fridge turns on above target + hysteresis and off below
    target - hysteresis; inside that band it keeps its current state.
    A state change is held back until min_cycle_seconds have passed since the
    last one, to protect the compressor from short-cycling.

    is_on may be None when the state is unknown (e.g. first start); then a
    plain thermostat decision is made against the target.
    """
    if temp_f is None:
        # No valid reading: hold whatever we're doing.
        return is_on

    if is_on is None:
        return temp_f >= target_f

    if is_on:
        want_on = not (temp_f <= target_f - hysteresis_f)
    else:
        want_on = temp_f >= target_f + hysteresis_f

    if want_on != is_on and seconds_since_change is not None \
            and seconds_since_change < min_cycle_seconds:
        return is_on
    return want_on
