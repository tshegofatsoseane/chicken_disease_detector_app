import eventlet
eventlet.monkey_patch()

from flask import Flask, request, jsonify, render_template_string
from flask_socketio import SocketIO
from werkzeug.middleware.proxy_fix import ProxyFix

# === Flask setup ===
app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app)

# === SocketIO setup ===
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")

# === Shared data ===
latest_frame = None
latest_label = None
pi_start_trigger = {"start": False}

# === API: Receive frames from Pi ===
@app.route("/api/frame", methods=["POST"])
def receive_frame():
    global latest_frame, latest_label
    label = request.form.get("label")
    confidence = request.form.get("confidence")
    image_file = request.files.get("image")
    if image_file:
        import base64
        latest_frame = base64.b64encode(image_file.read()).decode("utf-8")
        latest_label = f"{label} ({float(confidence):.2f})"
        socketio.emit("new_frame", {"label": latest_label, "frame": latest_frame})
        return jsonify({"status": "ok"}), 200
    return jsonify({"status": "error", "message": "No image received"}), 400

# === API: Pi client checks if it should start ===
@app.route("/check-start", methods=["GET"])
def check_start():
    return jsonify(pi_start_trigger), 200

# === API: Trigger start from frontend ===
@app.route("/trigger-pi", methods=["POST"])
def trigger_pi():
    pi_start_trigger["start"] = True
    print("🚀 Pi client START triggered")
    return jsonify({"status": "ok", "message": "Pi client started"}), 200

# === API: Stop trigger ===
@app.route("/reset-pi", methods=["POST"])
def reset_pi():
    pi_start_trigger["start"] = False
    print("🛑 Pi client STOP triggered")
    return jsonify({"status": "ok", "message": "Pi client stopped"}), 200

# === Frontend ===
@app.route("/")
def index():
    return render_template_string("""
    <html>
    <head>
        <title>🐔 Chicken Disease Monitor</title>
        <style>
            body { text-align:center; background:#111; color:#eee; font-family:sans-serif; }
            img { width:80%; border-radius:12px; box-shadow:0 0 10px #fff; margin-top:20px; }
            button { padding:10px 20px; margin:10px; font-size:16px; cursor:pointer; border-radius:6px; border:none; }
            #start { background:#4CAF50; color:white; }
            #start:hover { background:#45a049; }
            #stop { background:#f44336; color:white; }
            #stop:hover { background:#e53935; }
        </style>
    </head>
    <body>
        <h2>🐔 Chicken Disease Monitor</h2>
        <img id="video" src="" alt="Live feed">
        <h3 id="label">Waiting for data...</h3>
        <div>
            <button id="start" onclick="triggerPi()">Start Pi Client</button>
            <button id="stop" onclick="stopPi()">Stop Pi Client</button>
        </div>

        <script src="https://cdn.socket.io/4.3.2/socket.io.min.js"></script>
        <script>
            const socket = io({ transports: ["websocket"] });
            socket.on("new_frame", data => {
                document.getElementById("video").src = "data:image/jpeg;base64," + data.frame;
                document.getElementById("label").innerText = "Detected: " + data.label;
            });

            function triggerPi() {
                fetch("/trigger-pi", { method: "POST" })
                    .then(res => res.json())
                    .then(data => alert(data.message))
                    .catch(err => alert("Error triggering Pi client"));
            }

            function stopPi() {
                fetch("/reset-pi", { method: "POST" })
                    .then(res => res.json())
                    .then(data => alert(data.message))
                    .catch(err => alert("Error stopping Pi client"));
            }
        </script>
    </body>
    </html>
    """)

if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=8000, debug=True)
