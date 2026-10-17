import glob
import logging
import os
import subprocess
import time

logger = logging.getLogger(__name__)

BASE_DIR = os.environ.get('W1_BASE_DIR', '/sys/bus/w1/devices/')
MAX_RETRIES = 5

_modules_loaded = False


def _load_modules():
    """Load the 1-wire kernel modules once, the first time we read."""
    global _modules_loaded
    if _modules_loaded:
        return
    for module in ('w1-gpio', 'w1-therm'):
        try:
            subprocess.run(['modprobe', module], check=False, timeout=10)
        except (OSError, subprocess.SubprocessError):
            logger.warning('Could not modprobe %s', module)
    _modules_loaded = True


def read_temp_raw():
    device_folder = glob.glob(BASE_DIR + '28*')[0]
    with open(device_folder + '/w1_slave', 'r') as f:
        return f.readlines()


def read_temp():
    """Return (temp_c, temp_f), or (None, None) if the sensor can't be read.

    Callers must treat None as "unknown" and never as a temperature.
    """
    _load_modules()
    try:
        for _ in range(MAX_RETRIES):
            lines = read_temp_raw()
            if lines[0].strip().endswith('YES'):
                break
            time.sleep(0.2)
        else:
            logger.error('Sensor CRC check failed %d times', MAX_RETRIES)
            return None, None

        equals_pos = lines[1].find('t=')
        if equals_pos == -1:
            logger.error('Unexpected sensor output: %r', lines)
            return None, None
        temp_c = float(lines[1][equals_pos + 2:]) / 1000.0
    except Exception:
        logger.exception('Failed to read temperature sensor')
        return None, None

    # 85.0C is the DS18B20 power-on default, i.e. no real conversion happened.
    if temp_c == 85.0:
        logger.error('Sensor returned power-on default reading (85C)')
        return None, None

    return temp_c, temp_c * 9.0 / 5.0 + 32.0
