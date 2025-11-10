# ==============================
# app.py
# ==============================
import eventlet
eventlet.monkey_patch()  # Must be the first import for Eventlet

import os
import base64
from functools import wraps
from dotenv import load_dotenv

from flask import Flask, request, jsonify, render_template, session, redirect, url_for
from flask_socketio import SocketIO
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import generate_password_hash, check_password_hash
from pymongo import MongoClient

# ==============================
# Load environment variables
# ==============================
load_dotenv()

SECRET_KEY = os.environ.get("SECRET_KEY", "supersecretkey")
MONGO_URI = os.environ.get("MONGO_URI")

# ==============================
# Flask setup
# ==============================
app = Flask(__name__, static_folder="static", template_folder="templates")
app.wsgi_app = ProxyFix(app.wsgi_app)
app.secret_key = SECRET_KEY

# ==============================
# SocketIO setup
# ==============================
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")

# ==============================
# MongoDB setup
# ==============================
client = MongoClient(
    MONGO_URI,
    tls=True,
    tlsAllowInvalidCertificates=True
)
db = client.kgosibiodrone
users_col = db.users
results_col = db.results

# ==============================
# Shared state
# ==============================
latest_frame = None
captured_frame = None
pi_start_trigger = {"start": False}

# ==============================
# Authentication decorator
# ==============================
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

# ==============================
# Routes
# ==============================
@app.route("/")
@login_required
def index():
    return render_template("index.html")


# --- Authentication ---
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        user = users_col.find_one({"username": username})
        if user and check_password_hash(user["password"], password):
            session["user"] = username
            return redirect(url_for("index"))
        return render_template("login.html", error="Invalid credentials")

    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        if users_col.find_one({"username": username}):
            return render_template("register.html", error="User already exists")

        users_col.insert_one({
            "username": username,
            "password": generate_password_hash(password)
        })

        session["user"] = username
        return redirect(url_for("index"))

    return render_template("register.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# --- Pi Endpoints ---
@app.route("/api/frame", methods=["POST"])
def receive_frame():
    global latest_frame
    image_file = request.files.get("image")
    if not image_file:
        return jsonify({"status": "error", "message": "No image received"}), 400

    latest_frame = base64.b64encode(image_file.read()).decode("utf-8")
    socketio.emit("new_frame", {"frame": latest_frame})
    return jsonify({"status": "ok"}), 200


@app.route("/trigger-pi", methods=["POST"])
def trigger_pi():
    pi_start_trigger["start"] = True
    print("🚀 Pi feed started")
    return jsonify({"status": "ok"}), 200


@app.route("/reset-pi", methods=["POST"])
def reset_pi():
    pi_start_trigger["start"] = False
    socketio.emit("pi_stopped")
    print("🛑 Pi feed stopped")
    return jsonify({"status": "ok"}), 200


@app.route("/check-start", methods=["GET"])
def check_start():
    return jsonify({"start": pi_start_trigger["start"]}), 200


@app.route("/capture-frame", methods=["POST"])
def capture_frame():
    global captured_frame, latest_frame
    if latest_frame:
        captured_frame = latest_frame
        socketio.emit("frame_captured", {"frame": captured_frame})
        print("📸 Frame captured")
        return jsonify({"status": "ok"}), 200
    return jsonify({"status": "error", "message": "No live frame"}), 400


@app.route("/analyze-frame", methods=["POST"])
def analyze_frame():
    global captured_frame
    if not captured_frame:
        return jsonify({"status": "error", "message": "No captured frame"}), 400

    # Placeholder analysis
    disease_id = 0
    confidence = 0.95

    socketio.emit("frame_analyzed", {
        "disease_id": disease_id,
        "confidence": confidence
    })

    return jsonify({"status": "ok", "disease_id": disease_id, "confidence": confidence}), 200


# --- Save analysis results ---
@app.route("/save-result", methods=["POST"])
@login_required
def save_result():
    data = request.json
    if not data:
        return jsonify({"status": "error", "message": "No data"}), 400

    data["username"] = session["user"]
    results_col.insert_one(data)
    return jsonify({"status": "ok"}), 200


@app.route("/get-history", methods=["GET"])
@login_required
def get_history():
    username = session["user"]
    results = list(results_col.find({"username": username}).sort("_id", -1))
    for r in results:
        r["_id"] = str(r["_id"])
    return jsonify(results)


# ==============================
# Run server
# ==============================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"🚀 Starting server on 0.0.0.0:{port}")
    socketio.run(app, host="0.0.0.0", port=port)