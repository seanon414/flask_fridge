from flask_wtf import Form
from wtforms import IntegerField
from wtforms.validators import InputRequired, NumberRange


class IndexForm(Form):
    target_temp = IntegerField('Target Temp', validators=[NumberRange(min=0, max=99), InputRequired()])
