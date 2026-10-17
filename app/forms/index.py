from flask_wtf import Form
from wtforms import FloatField, IntegerField, TextAreaField, validators


class IndexForm(Form):
    target_temp = IntegerField('Target Temp (F)', validators=[validators.NumberRange(min=20, max=70), validators.InputRequired()])
    hysteresis = FloatField('Hysteresis +/- (F)', validators=[validators.NumberRange(min=0.5, max=10), validators.InputRequired()])
    current_temp = TextAreaField('Current Temp', validators=[validators.Optional()], render_kw={'readonly': True})
