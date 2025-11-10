import time
import cv2
import numpy as np
import requests
import threading
from picamera2 import Picamera2
from tflite_runtime.interpreter import Interpreter
import signal
import sys

# ==== CONFIG ====
BACKEND_URL = "http://192.168.100.74:8000/api/frame"
CHECK_START_URL = "http://192.168.100.74:8000/check-start"
MODEL_PATH = "chicken_disease_classifier_vgg16.tflite"
CAPTURE_INTERVAL = 0.3
NETWORK_RETRY = 5
CAMERA_RETRY = 2
CAMERA_INIT_RETRIES = 5

# ==== DISEASE MAPPING ====
DISEASE_MAP = {
    0: 0, 1: 7, 2: 5, 3: 3, 4: 2, 5: 1, 6: 4, 7: 6, 8: 8
}

# ==== LOAD MODEL ====
interpreter = Interpreter(model_path=MODEL_PATH)
interpreter.allocate_tensors()
input_details = interpreter.get_input_details()
output_details = interpreter.get_output_details()
IMG_SIZE = (input_details[0]['shape'][2], input_details[0]['shape'][1])
print(f"🧪 Model expects input size: {IMG_SIZE}")

# ==== GLOBAL STATE ====
picam2 = None
camera_lock = threading.Lock()
should_stop = False

# ==== HELPER FUNCTIONS ====
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
    return DISEASE_MAP.get(pred_class, 0), confidence

def send_to_backend(image, disease_id, confidence, retries=3):
    """Send frame and prediction to backend."""
    h, w, _ = image.shape
    cv2.rectangle(image, (10, 10), (w-10, h-10), (0, 255, 0), 2)
    _, img_encoded = cv2.imencode('.jpg', image)
    files = {'image': ('frame.jpg', img_encoded.tobytes(), 'image/jpeg')}
    data = {'disease_id': str(disease_id), 'confidence': confidence}

    for attempt in range(1, retries + 1):
        try:
            r = requests.post(BACKEND_URL, files=files, data=data, timeout=10)
            if r.status_code == 200:
                print(f"✅ Sent: Disease ID {disease_id} ({confidence:.2f})")
                return True
            else:
                print(f"⚠️ Backend error: {r.status_code}")
        except Exception as e:
            print(f"⚠️ Attempt {attempt} failed: {e}")
        time.sleep(2 ** attempt)
    return False

def release_camera():
    """Safely release camera with proper cleanup."""
    global picam2
    with camera_lock:
        if picam2 is not None:
            try:
                print("🔒 Acquiring camera lock for cleanup...")
                picam2.stop()
                picam2.close()  # ← CRITICAL: Must call close()!
                time.sleep(1)
            except Exception as e:
                print(f"⚠️ Error releasing camera: {e}")
            finally:
                picam2 = None
                print("✓ Camera safely released")

def initialize_camera():
    """Initialize camera with robust error handling."""
    global picam2
    with camera_lock:
        # Clean up any existing instance first
        if picam2 is not None:
            try:
                picam2.stop()
                picam2.close()
                time.sleep(1)
            except:
                pass
            picam2 = None

        for attempt in range(CAMERA_INIT_RETRIES):
            try:
                print(f"📷 Initializing camera (attempt {attempt + 1}/{CAMERA_INIT_RETRIES})...")
                picam2 = Picamera2()
                config = picam2.create_preview_configuration(main={"size": (640, 480)})
                picam2.configure(config)
                picam2.start()
                time.sleep(2)  # Allow time for camera to stabilize
                print("📸 Camera initialized and ready!")
                return True
            except Exception as e:
                print(f"⚠️ Camera init failed: {e}")
                if picam2 is not None:
                    try:
                        picam2.close()
                    except:
                        pass
                    picam2 = None
                time.sleep(CAMERA_RETRY)
        
        print("❌ Failed to initialize camera after multiple attempts")
        return False

# ==== SIGNAL HANDLING ====
def signal_handler(sig, frame):
    global should_stop
    print("\n🛑 Shutting down gracefully...")
    should_stop = True
    release_camera()
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)

# ==== MAIN LOOP ====
try:
    while not should_stop:
        # Check start/stop signal from backend
        feed_on = False
        try:
            r = requests.get(CHECK_START_URL, timeout=5)
            feed_on = r.json().get("start", False)
        except Exception as e:
            print(f"⚠️ Error checking start signal: {e}")
            time.sleep(NETWORK_RETRY)
            continue

        if feed_on:
            # Initialize camera if not already running
            if picam2 is None:
                if not initialize_camera():
                    time.sleep(NETWORK_RETRY)
                    continue

            # Capture frame and predict
            try:
                with camera_lock:
                    if picam2 is not None:
                        frame = picam2.capture_array()
                disease_id, confidence = predict_disease(frame)
                print(f"🖼 Prediction: Disease ID {disease_id}, Confidence: {confidence:.2f}")
                send_to_backend(frame, disease_id, confidence)
            except Exception as e:
                print(f"⚠️ Error capturing frame: {e}")
                release_camera()
            time.sleep(CAPTURE_INTERVAL)

        else:
            # Stop camera if feed turned off
            if picam2 is not None:
                release_camera()
                print("⏸ Camera stopped. Waiting for start signal...")
            time.sleep(1)

except Exception as e:
    print(f"❌ Unexpected error: {e}")
    release_camera()
finally:
    release_camera()
    print("📷 Client shutdown complete")