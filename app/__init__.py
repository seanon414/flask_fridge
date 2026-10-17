import os
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_wtf.csrf import CSRFProtect
from .config import Config

basedir = os.path.abspath(os.path.dirname(__file__))
SECRET_KEY = os.environ.get('SECRET_KEY') or os.urandom(32)

app = Flask(__name__)

app.config.from_object(Config)
app.config['SECRET_KEY'] = SECRET_KEY
db = SQLAlchemy(app)
migrate = Migrate(app, db)
csrf = CSRFProtect(app)

from .views.index import index_blueprint
from .views.api import api_blueprint

app.register_blueprint(index_blueprint)
app.register_blueprint(api_blueprint)
