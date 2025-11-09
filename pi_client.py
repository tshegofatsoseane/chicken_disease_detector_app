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
CAPTURE_INTERVAL = 1.0  # seconds between frames
NETWORK_RETRY = 5       # seconds between network retries
CAMERA_RETRY = 2        # seconds between camera retries

# ==== LOAD MODEL ====
interpreter = Interpreter(model_path=MODEL_PATH)
interpreter.allocate_tensors()
input_details = interpreter.get_input_details()
output_details = interpreter.get_output_details()
IMG_SIZE = (input_details[0]['shape'][2], input_details[0]['shape'][1])
print(f"🧪 Model expects input size: {IMG_SIZE}")

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
    return pred_class, confidence

def send_to_backend(image, label, confidence, retries=3):
    h, w, _ = image.shape
    cv2.rectangle(image, (10, 10), (w-10, h-10), (0, 255, 0), 2)
    _, img_encoded = cv2.imencode('.jpg', image)
    files = {'image': ('frame.jpg', img_encoded.tobytes(), 'image/jpeg')}
    data = {'label': str(label), 'confidence': confidence}

    for attempt in range(1, retries + 1):
        try:
            r = requests.post(BACKEND_URL, files=files, data=data, timeout=10)
            if r.status_code == 200:
                print(f"✅ Sent: {label} ({confidence:.2f})")
                return True
            else:
                print(f"⚠️ Backend error: {r.status_code}")
        except Exception as e:
            print(f"⚠️ Attempt {attempt} failed to send data:", e)
        time.sleep(2 ** attempt)  # exponential backoff
    return False

# ==== MAIN LOOP ====
picam2 = None

try:
    while True:
        # Check start/stop signal
        feed_on = False
        try:
            r = requests.get(CHECK_START_URL, timeout=5)
            feed_on = r.json().get("start", False)
        except Exception as e:
            print("⚠️ Error checking start signal:", e)
            time.sleep(NETWORK_RETRY)
            continue

        if feed_on:
            # Initialize camera if not already running
            if picam2 is None:
                try:
                    picam2 = Picamera2()
                    config = picam2.create_preview_configuration(main={"size": (640, 480)})
                    picam2.configure(config)
                    picam2.start()
                    time.sleep(2)
                    print("📸 Camera initialized and ready!")
                except Exception as e:
                    print("⚠️ Camera busy, retrying...", e)
                    if picam2 is not None:
                        try: picam2.stop()
                        except: pass
                        picam2 = None
                    time.sleep(CAMERA_RETRY)
                    continue

            # Capture frame and predict
            try:
                frame = picam2.capture_array()
                label, confidence = predict_disease(frame)
                print(f"🖼 Prediction: {label} ({confidence:.2f})")
                send_to_backend(frame, label, confidence)
            except Exception as e:
                print("⚠️ Error capturing or sending frame:", e)
                if picam2 is not None:
                    try: picam2.stop()
                    except: pass
                    picam2 = None
            time.sleep(CAPTURE_INTERVAL)

        else:
            # Stop camera if feed turned off
            if picam2 is not None:
                try:
                    picam2.stop()
                    picam2 = None
                    print("⏸ Camera stopped. Waiting for start signal...")
                except Exception as e:
                    print("⚠️ Error stopping camera:", e)
            time.sleep(1)

except KeyboardInterrupt:
    print("\n🛑 Stopped by user")

finally:
    if picam2 is not None:
        try:
            picam2.stop()
            print("📷 Camera released.")
        except:
            pass
