import logging
import os
import subprocess
import threading

logger = logging.getLogger(__name__)

CODESEND = os.environ.get('CODESEND_PATH', '/home/pi/rfoutlet/codesend')
CODE_ON = os.environ.get('FRIDGE_CODE_ON', '1054003')
CODE_OFF = os.environ.get('FRIDGE_CODE_OFF', '1054012')

# Only one process-local RF transmission at a time.
_send_lock = threading.Lock()


def _send(code):
    with _send_lock:
        try:
            result = subprocess.run([CODESEND, code], timeout=10)
        except (OSError, subprocess.SubprocessError):
            logger.exception('codesend failed for code %s', code)
            return False
    return result.returncode == 0


def turn_off_fridge():
    """Turns off fridge. Returns True if the RF command was sent."""
    logger.info('Sending code to turn off fridge')
    return _send(CODE_OFF)


def turn_on_fridge():
    """Turns on fridge. Returns True if the RF command was sent."""
    logger.info('Sending code to turn on fridge')
    return _send(CODE_ON)


def set_fridge(on):
    return turn_on_fridge() if on else turn_off_fridge()
