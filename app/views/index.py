from flask import Blueprint, render_template, request
from ..scripts.lager import turn_off_fridge, turn_on_fridge


index_blueprint = Blueprint('index', __name__, static_folder='static')


@index_blueprint.route("/", methods=['GET', 'POST'])
def index():
    current_temp = 60
    target_temp = 55
    message = ''
    if request.method == 'POST':
        if 'update_target' in request.form:
            # Todo: Add ability to update target temp
            message = 'Taget temp set to {}'.format(target_temp)
        elif 'turn_off_fridge' in request.form:
            turn_off_fridge
            message = 'The frige has been turned off.'
        elif 'turn_on_fridge' in request.form:
            turn_on_fridge
            message = 'The frige has been turned on.'

    return render_template('index.html', current_temp=current_temp, target_temp=target_temp, message=message)
