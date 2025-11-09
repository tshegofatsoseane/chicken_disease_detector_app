import eventlet
eventlet.monkey_patch()  # Must be at top

from flask import Flask, request, jsonify, render_template_string
from flask_socketio import SocketIO
from werkzeug.middleware.proxy_fix import ProxyFix
from PIL import Image
from io import BytesIO
from base64 import b64encode

# === Flask app setup ===
app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app)  # Needed if behind a proxy (like Render)

# === SocketIO setup ===
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")

# === Global storage for latest frame/label ===
latest_frame = None
latest_label = None

# === API endpoint to receive frames ===
@app.route("/api/frame", methods=["POST"])
def receive_frame():
    global latest_frame, latest_label
    label = request.form.get("label")
    confidence = request.form.get("confidence")
    file = request.files.get("image")

    if file:
        img = Image.open(file.stream)
        buf = BytesIO()
        img.save(buf, format="JPEG")
        latest_frame = b64encode(buf.getvalue()).decode("utf-8")
        latest_label = f"{label} ({float(confidence):.2f})"
        # Emit to all connected clients
        socketio.emit("new_frame", {"label": latest_label, "frame": latest_frame})
        return jsonify({"status": "ok"}), 200

    return jsonify({"status": "no image"}), 400

# === Frontend page ===
@app.route("/")
def index():
    return render_template_string("""
    <html>
    <head>
        <title>Live Chicken Disease Monitor</title>
    </head>
    <body style="text-align:center;background:#111;color:#eee;font-family:sans-serif;">
        <h2>🐔 Chicken Disease Monitor</h2>
        <img id="video" style="width:80%;border-radius:12px;box-shadow:0 0 10px #fff;">
        <h3 id="label">Waiting for data...</h3>
        <script src="https://cdn.socket.io/4.3.2/socket.io.min.js"></script>
        <script>
            const socket = io({ transports: ["websocket"] });
            socket.on("new_frame", data => {
                document.getElementById("video").src = "data:image/jpeg;base64," + data.frame;
                document.getElementById("label").innerText = "Detected: " + data.label;
            });
        </script>
    </body>
    </html>
    """)

# === Run server with Eventlet ===
if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=8000, debug=True)
