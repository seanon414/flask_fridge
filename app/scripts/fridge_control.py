import os

def turn_off_fridge():
    """Turns off fridge"""
    # logging.info('Sending code to turn off fridge')
    print('Sending code to turn off fridge')
    os.system('/home/pi/rfoutlet/codesend 1054012')

def turn_on_fridge():
    """Turns on fridge"""
    # logging.info('Sending code to turn on fridge')
    print('Sending code to turn on fridge')
    os.system('/home/pi/rfoutlet/codesend 1054003')
