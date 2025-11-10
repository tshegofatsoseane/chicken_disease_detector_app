# ==============================
# app.py
# ==============================
import eventlet
eventlet.monkey_patch()  # Must be the first import for Eventlet

import os
import base64
import signal
import atexit
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
# Cleanup handlers
# ==============================
def cleanup_resources():
    """Clean up resources on shutdown."""
    global latest_frame, captured_frame, pi_start_trigger
    try:
        print("🛑 Backend shutting down - cleaning up resources...")
        # Stop Pi feed
        pi_start_trigger["start"] = False
        # Clear frame buffers
        latest_frame = None
        captured_frame = None
        # Close MongoDB connection
        if client:
            client.close()
            print("✓ MongoDB connection closed")
    except Exception as e:
        print(f"⚠️ Error during cleanup: {e}")

# Register cleanup handlers
atexit.register(cleanup_resources)

def signal_handler(sig, frame):
    """Graceful shutdown on SIGTERM/SIGINT."""
    print(f"\n🛑 Received signal {sig}, shutting down gracefully...")
    cleanup_resources()
    exit(0)

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

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
            print(f"✅ User logged in: {username}")
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
        print(f"✅ New user registered: {username}")
        return redirect(url_for("index"))

    return render_template("register.html")


@app.route("/logout")
def logout():
    user = session.get("user", "Unknown")
    session.clear()
    print(f"👋 User logged out: {user}")
    return redirect(url_for("login"))

# --- Pi Endpoints ---
@app.route("/api/frame", methods=["POST"])
def receive_frame():
    """Receive frame from Pi client and broadcast to connected clients."""
    global latest_frame
    try:
        image_file = request.files.get("image")
        if not image_file:
            return jsonify({"status": "error", "message": "No image received"}), 400

        disease_id = request.form.get("disease_id")
        confidence = request.form.get("confidence")

        latest_frame = base64.b64encode(image_file.read()).decode("utf-8")
        
        # Emit to all connected WebSocket clients
        socketio.emit("new_frame", {
            "frame": latest_frame,
            "disease_id": disease_id,
            "confidence": confidence
        })
        
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        print(f"⚠️ Error receiving frame: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/trigger-pi", methods=["POST"])
def trigger_pi():
    """Signal Pi to start camera feed."""
    try:
        pi_start_trigger["start"] = True
        print("🚀 Pi feed started")
        socketio.emit("pi_started")
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        print(f"⚠️ Error triggering Pi: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/reset-pi", methods=["POST"])
def reset_pi():
    """Signal Pi to stop camera feed."""
    try:
        pi_start_trigger["start"] = False
        socketio.emit("pi_stopped")
        print("🛑 Pi feed stopped")
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        print(f"⚠️ Error resetting Pi: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/check-start", methods=["GET"])
def check_start():
    """Pi client polls this to check if it should start/stop recording."""
    try:
        return jsonify({"start": pi_start_trigger["start"]}), 200
    except Exception as e:
        print(f"⚠️ Error checking start signal: {e}")
        return jsonify({"start": False}), 500


@app.route("/capture-frame", methods=["POST"])
def capture_frame():
    """Capture the current live frame for analysis."""
    global captured_frame, latest_frame
    try:
        if latest_frame:
            captured_frame = latest_frame
            socketio.emit("frame_captured", {"frame": captured_frame})
            print("📸 Frame captured")
            return jsonify({"status": "ok"}), 200
        return jsonify({"status": "error", "message": "No live frame"}), 400
    except Exception as e:
        print(f"⚠️ Error capturing frame: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/analyze-frame", methods=["POST"])
def analyze_frame():
    """Analyze the captured frame (currently a placeholder)."""
    global captured_frame
    try:
        if not captured_frame:
            return jsonify({"status": "error", "message": "No captured frame"}), 400

        # Placeholder analysis - in production, this would use ML model
        disease_id = 0
        confidence = 0.95

        socketio.emit("frame_analyzed", {
            "disease_id": disease_id,
            "confidence": confidence
        })

        print(f"🔍 Frame analyzed - Disease ID: {disease_id}, Confidence: {confidence}")
        return jsonify({"status": "ok", "disease_id": disease_id, "confidence": confidence}), 200
    except Exception as e:
        print(f"⚠️ Error analyzing frame: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


# --- Save analysis results ---
@app.route("/save-result", methods=["POST"])
@login_required
def save_result():
    """Save analysis result to database."""
    try:
        data = request.json
        if not data:
            return jsonify({"status": "error", "message": "No data"}), 400

        data["username"] = session["user"]
        results_col.insert_one(data)
        print(f"💾 Result saved for user: {session['user']}")
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        print(f"⚠️ Error saving result: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/get-history", methods=["GET"])
@login_required
def get_history():
    """Retrieve user's analysis history."""
    try:
        username = session["user"]
        results = list(results_col.find({"username": username}).sort("_id", -1))
        for r in results:
            r["_id"] = str(r["_id"])
        print(f"📋 Retrieved {len(results)} history records for {username}")
        return jsonify(results)
    except Exception as e:
        print(f"⚠️ Error retrieving history: {e}")
        return jsonify([]), 500


# --- Health check endpoint ---
@app.route("/health", methods=["GET"])
def health_check():
    """Health check endpoint for monitoring."""
    try:
        # Test MongoDB connection
        db.command("ping")
        return jsonify({
            "status": "healthy",
            "pi_feed": pi_start_trigger["start"],
            "has_frame": latest_frame is not None
        }), 200
    except Exception as e:
        print(f"⚠️ Health check failed: {e}")
        return jsonify({"status": "unhealthy", "error": str(e)}), 500


# --- Error handlers ---
@app.errorhandler(404)
def not_found(error):
    """Handle 404 errors."""
    return jsonify({"status": "error", "message": "Endpoint not found"}), 404


@app.errorhandler(500)
def internal_error(error):
    """Handle 500 errors."""
    print(f"❌ Internal server error: {error}")
    return jsonify({"status": "error", "message": "Internal server error"}), 500


# ==============================
# SocketIO Events
# ==============================
@socketio.on("connect")
def handle_connect():
    """Handle new client connection."""
    print("🔌 Client connected")
    socketio.emit("connection_response", {"data": "Connected to BioDrone server"})


@socketio.on("disconnect")
def handle_disconnect():
    """Handle client disconnection."""
    print("🔌 Client disconnected")


# ==============================
# Run server
# ==============================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"🚀 Starting Kgosi BioDrone server on 0.0.0.0:{port}")
    try:
        socketio.run(app, host="0.0.0.0", port=port, debug=False)
    except KeyboardInterrupt:
        print("\n🛑 Server interrupted by user")
        cleanup_resources()
    except Exception as e:
        print(f"❌ Server error: {e}")
        cleanup_resources()
        raise