from flask_wtf import Form
from wtforms import IntegerField
from wtforms import validators


class IndexForm(Form):
    target_temp = IntegerField('Target Temp', validators=[validators.NumberRange(min=0, max=99), validators.InputRequired()])
    current_temp = IntegerField('Current Temp', validators=[validators.Optional()], render_kw={'readonly': True})
