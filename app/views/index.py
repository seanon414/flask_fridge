from flask import Blueprint, render_template, request, flash

from ..forms.index import IndexForm
from app import db
from app.models.temp import Reading, get_settings
from app.scripts.thermometer import read_temp

index_blueprint = Blueprint('index', __name__, static_folder='static')

APPLY_NOTE = 'The controller applies it within about 30 seconds.'


@index_blueprint.route("/", methods=['GET', 'POST'])
def index():
    settings = get_settings()
    form = IndexForm(obj=settings)

    if request.method == 'POST':
        if 'update_target' in request.form:
            if form.validate():
                settings.target_temp = form.target_temp.data
                settings.hysteresis = form.hysteresis.data
                db.session.commit()
                flash('Target set to {}F (+/-{}F). {}'.format(
                    settings.target_temp, settings.hysteresis, APPLY_NOTE), 'info')
        elif 'refresh_current_temp' in request.form:
            flash('Retrieved latest temp reading.', 'info')
        elif 'turn_off_fridge' in request.form:
            settings.mode = 'off'
            db.session.commit()
            flash('Fridge forced off. ' + APPLY_NOTE, 'info')
        elif 'turn_on_fridge' in request.form:
            settings.mode = 'on'
            db.session.commit()
            flash('Fridge forced on. ' + APPLY_NOTE, 'info')
        elif 'auto_mode' in request.form:
            settings.mode = 'auto'
            db.session.commit()
            flash('Automatic control resumed. ' + APPLY_NOTE, 'info')

    _, current_temp_f = read_temp()
    if current_temp_f is None:
        form.current_temp.data = 'Sensor unavailable'
    else:
        form.current_temp.data = '{:.1f}'.format(current_temp_f)

    last = Reading.query.order_by(Reading.timestamp.desc()).first()
    return render_template('index.html', form=form, mode=settings.mode,
                           fridge_on=last.fridge_on if last else None,
                           last_reading=last)
