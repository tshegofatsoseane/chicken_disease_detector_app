import os
import eventlet
eventlet.monkey_patch()

from flask import Flask, request, jsonify, render_template
from flask_socketio import SocketIO
from werkzeug.middleware.proxy_fix import ProxyFix
import base64

app = Flask(__name__, static_folder="static", template_folder="templates")
app.wsgi_app = ProxyFix(app.wsgi_app)

# SocketIO setup
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")

# === Shared state ===
latest_frame = None
captured_frame = None
pi_start_trigger = {"start": False}


# === API ===
@app.route("/api/frame", methods=["POST"])
def receive_frame():
    """Receive frame from Pi and emit to frontend."""
    global latest_frame
    image_file = request.files.get("image")
    if not image_file:
        return jsonify({"status": "error", "message": "No image received"}), 400

    latest_frame = base64.b64encode(image_file.read()).decode("utf-8")
    socketio.emit("new_frame", {"frame": latest_frame})
    return jsonify({"status": "ok"}), 200


@app.route("/trigger-pi", methods=["POST"])
def trigger_pi():
    """Trigger Pi to start sending frames."""
    pi_start_trigger["start"] = True
    print("🚀 Pi client START triggered")
    return jsonify({"status": "ok", "message": "Pi feed started"}), 200


@app.route("/reset-pi", methods=["POST"])
def reset_pi():
    """Stop Pi frame feed."""
    pi_start_trigger["start"] = False
    print("🛑 Pi client STOP triggered")
    socketio.emit("pi_stopped")
    return jsonify({"status": "ok", "message": "Pi feed stopped"}), 200


@app.route("/check-start", methods=["GET"])
def check_start():
    """Check if Pi should be running."""
    return jsonify({"start": pi_start_trigger["start"]}), 200


@app.route("/capture-frame", methods=["POST"])
def capture_frame():
    """Capture current frame for analysis."""
    global captured_frame, latest_frame
    if latest_frame:
        captured_frame = latest_frame
        socketio.emit("frame_captured", {"frame": captured_frame})
        print("📸 Frame captured for analysis")
        return jsonify({"status": "ok", "message": "Frame captured"}), 200
    return jsonify({"status": "error", "message": "No live frame to capture"}), 400


@app.route("/analyze-frame", methods=["POST"])
def analyze_frame():
    """Analyze the captured frame and send results to frontend."""
    global captured_frame
    if not captured_frame:
        return jsonify({"status": "error", "message": "No captured frame"}), 400

    # === Placeholder prediction ===
    disease_id = 0         # Example: 0 = avian influenza
    confidence = 0.95      # Example confidence

    # Emit structured data to frontend modal
    socketio.emit("frame_analyzed", {
        "disease_id": disease_id,
        "confidence": confidence
    })

    return jsonify({
        "status": "ok",
        "disease_id": disease_id,
        "confidence": confidence
    }), 200


# === Frontend ===
@app.route("/")
def index():
    return render_template("index.html")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"🚀 Starting server on 0.0.0.0:{port}")
    socketio.run(app, host="0.0.0.0", port=port)
