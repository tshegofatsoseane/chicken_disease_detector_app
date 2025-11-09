from flask import Flask, request, jsonify, render_template_string
from flask_socketio import SocketIO, emit

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

latest_frame = None
latest_label = None

@app.route("/api/frame", methods=["POST"])
def receive_frame():
    global latest_frame, latest_label
    data = request.get_json()
    latest_label = data.get("label")
    latest_frame = data.get("frame")
    socketio.emit("new_frame", {"label": latest_label, "frame": latest_frame})
    return jsonify({"status": "ok"}), 200

@app.route("/")
def index():
    return render_template_string("""
    <html>
    <head><title>Live Detection</title></head>
    <body style="text-align:center;background:#111;color:#eee;">
        <h2>🐔 Chicken Disease Monitor</h2>
        <img id="video" style="width:80%;border-radius:12px;box-shadow:0 0 10px #fff;">
        <h3 id="label">Waiting for data...</h3>
        <script src="https://cdn.socket.io/4.3.2/socket.io.min.js"></script>
        <script>
            const socket = io();
            socket.on("new_frame", data => {
                document.getElementById("video").src = "data:image/jpeg;base64," + data.frame;
                document.getElementById("label").innerText = "Detected: " + data.label;
            });
        </script>
    </body>
    </html>
    """)

if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=8000)
