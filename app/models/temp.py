from app import db

class Temp(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    target_temp = db.Column(db.Integer)
    current_temp = db.Column(db.Integer)
