import os
import time
from thermometer import read_temp
from fridge_control import turn_off_fridge, turn_on_fridge
from datetime import datetime

# logging.basicConfig(filename='/home/pi/Documents/lager/lager_fridge/lager_history.log', level=logging.DEBUG, format='%(asctime)s %(message)s', datefmt='%m/%d/%Y %I:%M:%S %p')

target_temp = 45


def should_turn_off_fridge():
    """Checks if fridge temp is below target temp"""
    temp_data = read_temp()[1]
    # logging.info('Current temp is: {}, target temp is: {}'.format(temp_data, target_temp))
    print('{} Current temp is: {}, target temp is: {}'.format(datetime.now().strftime("%Y-%m-%d %H:%M:%S"), temp_data, target_temp))
    return temp_data < target_temp


if __name__ == "__main__":
    if should_turn_off_fridge():
        turn_off_fridge()
    else:
        turn_on_fridge()
