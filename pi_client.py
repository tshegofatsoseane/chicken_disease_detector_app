import time
import cv2
import numpy as np
import requests
from picamera2 import Picamera2
from tflite_runtime.interpreter import Interpreter

# ==== CONFIG ====
BACKEND_URL = "https://chicken-disease-detector-app.onrender.com/api/frame"
CHECK_START_URL = "https://chicken-disease-detector-app.onrender.com/check-start"
MODEL_PATH = "chicken_disease_classifier_vgg16.tflite"
CAPTURE_INTERVAL = 5  # seconds between captures
PING_INTERVAL = 2     # seconds between start checks

# ==== WAIT FOR START SIGNAL ====
print("⏳ Waiting for start trigger from server...")
while True:
    try:
        r = requests.get(CHECK_START_URL, timeout=5)
        if r.status_code == 200 and r.json().get("start") == True:
            print("✅ Start signal received! Initializing camera...")
            break
    except Exception as e:
        print("⚠️ Error checking start:", e)
    time.sleep(PING_INTERVAL)

# ==== INITIALIZE CAMERA ====
picam2 = Picamera2()
config = picam2.create_preview_configuration(main={"size": (640, 480)})
picam2.configure(config)
picam2.start()
time.sleep(2)
print("📸 Camera ready, starting detection loop...")

# ==== LOAD MODEL ====
interpreter = Interpreter(model_path=MODEL_PATH)
interpreter.allocate_tensors()
input_details = interpreter.get_input_details()
output_details = interpreter.get_output_details()
IMG_SIZE = (input_details[0]['shape'][2], input_details[0]['shape'][1])
print(f"🧪 Model expects input size: {IMG_SIZE}")

def preprocess_frame(frame):
    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGRA2RGB)
    frame_resized = cv2.resize(frame_rgb, IMG_SIZE)
    frame_normalized = frame_resized.astype(np.float32) / 255.0
    return np.expand_dims(frame_normalized, axis=0)

def predict_disease(frame):
    input_data = preprocess_frame(frame)
    interpreter.set_tensor(input_details[0]['index'], input_data)
    interpreter.invoke()
    output_data = interpreter.get_tensor(output_details[0]['index'])
    pred_class = int(np.argmax(output_data))
    confidence = float(np.max(output_data))
    return pred_class, confidence

def send_to_backend(image, label, confidence):
    _, img_encoded = cv2.imencode('.jpg', image)
    files = {'image': ('frame.jpg', img_encoded.tobytes(), 'image/jpeg')}
    data = {'label': str(label), 'confidence': confidence}
    try:
        r = requests.post(BACKEND_URL, files=files, data=data, timeout=10)
        if r.status_code == 200:
            print(f"✅ Sent result: {label} ({confidence:.2f})")
        else:
            print(f"❌ Backend error: {r.status_code} {r.text}")
    except Exception as e:
        print("⚠️ Failed to send data:", e)

# ==== MAIN DETECTION LOOP ====
try:
    while True:
        frame = picam2.capture_array()
        label, confidence = predict_disease(frame)
        print(f"🖼 Prediction: {label} ({confidence:.2f})")
        send_to_backend(frame, label, confidence)
        time.sleep(CAPTURE_INTERVAL)

except KeyboardInterrupt:
    print("\n🛑 Stopped by user.")
    picam2.stop()
