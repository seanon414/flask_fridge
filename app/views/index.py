from flask import Blueprint, render_template, request, flash
from ..scripts.fridge_control import turn_off_fridge, turn_on_fridge
from ..forms.index import IndexForm
from app.models.temp import Temp
from app import db
from app.scripts.thermometer import read_temp

index_blueprint = Blueprint('index', __name__, static_folder='static')


@index_blueprint.route("/", methods=['GET', 'POST'])
def index():
    current_temp_c, current_temp_f = read_temp()
    temperature = Temp.query.get(1)
    temperature.current_temp = current_temp_f
    form = IndexForm(obj=temperature)

    if request.method == 'POST':
        if 'update_target' in request.form and form.validate():
            # Todo: Add ability to update target temp
            target_temp = form.data['target_temp']
            temperature.target_temp = target_temp
            db.session.commit()
            flash('Taget temp set to {}'.format(target_temp), 'info')
        elif 'turn_off_fridge' in request.form:
            turn_off_fridge()
            flash('The frige has been turned off.', 'info')
        elif 'turn_on_fridge' in request.form:
            turn_on_fridge()
            flash('The frige has been turned on.', 'info')

    return render_template('index.html', form=form, current_temp=current_temp_f, target_temp=temperature.target_temp)
