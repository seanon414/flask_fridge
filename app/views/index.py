from flask import Blueprint, render_template

index_blueprint = Blueprint('index', __name__, static_folder='static')


@index_blueprint.route("/")
def index():
    # The page is a shell; app.js fills it in from /api/status and /api/history.
    return render_template('index.html')
