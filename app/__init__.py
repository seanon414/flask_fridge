import os
from flask import Flask
from config import Config
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate

basedir = os.path.abspath(os.path.dirname(__file__))
SECRET_KEY = os.urandom(32)

app = Flask(__name__)

app.config.from_object(Config)
app.config['SECRET_KEY'] = SECRET_KEY
db = SQLAlchemy(app)
db.init_app(app)
migrate = Migrate(app, db)

from .views.index import index_blueprint

app.register_blueprint(index_blueprint)
