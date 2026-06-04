import json
import os
from flask import Flask, render_template, request, jsonify
from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)
app.config['SECRET_KEY'] = 'wyvern_super_secret_key'

basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'crafters.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)


# --- SQL DATABASE MODEL ---
class Crafter(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), nullable=False)
    blueprint_name = db.Column(db.String(250), nullable=False)
    __table_args__ = (db.UniqueConstraint('username', 'blueprint_name', name='_user_bp_uc'),)


with app.app_context():
    db.create_all()


# --- HELPER FUNCTION ---
def load_blueprints():
    file_path = os.path.join(basedir, 'blueprints.json')
    if os.path.exists(file_path):
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []


# --- ROUTES ---
@app.route('/')
def index():
    blueprints = load_blueprints()
    categories = sorted(list(set(bp.get('category', 'Unknown') for bp in blueprints)))
    all_crafters = Crafter.query.all()

    crafters_map = {}
    for c in all_crafters:
        if c.blueprint_name not in crafters_map:
            crafters_map[c.blueprint_name] = []
        crafters_map[c.blueprint_name].append(c.username)

    return render_template('index.html',
                           blueprints=blueprints,
                           categories=categories,
                           crafters_map=crafters_map)


@app.route('/claim', methods=['POST'])
def claim():
    data = request.get_json()
    username = data.get('username')
    blueprint_name = data.get('blueprint_name')

    if username and blueprint_name:
        existing = Crafter.query.filter_by(username=username, blueprint_name=blueprint_name).first()

        if not existing:
            new_claim = Crafter(username=username, blueprint_name=blueprint_name)
            db.session.add(new_claim)
            db.session.commit()
            return jsonify({"status": "success", "message": f"Successfully added {username}!"}), 200
        else:
            return jsonify({"status": "error", "message": f"{username} is already listed for this item."}), 400

    return jsonify({"status": "error", "message": "Invalid request data."}), 400


if __name__ == '__main__':
    app.run(debug=True, port=5000)