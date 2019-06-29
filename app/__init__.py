import os
from flask import Flask
from config import Config
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_bootstrap import Bootstrap
from flask_nav import Nav
from flask_nav.elements import *

nav = Nav()

# registers the "top" menubar
nav.register_element(
    'top',
    Navbar(
        View('Home', 'index.index')))

basedir = os.path.abspath(os.path.dirname(__file__))
SECRET_KEY = os.urandom(32)

app = Flask(__name__)

app.config.from_object(Config)
app.config['SECRET_KEY'] = SECRET_KEY
db = SQLAlchemy(app)
db.init_app(app)
migrate = Migrate(app, db)
bootstrap = Bootstrap(app)
nav.init_app(app)

from .views.index import index_blueprint

app.register_blueprint(index_blueprint)
