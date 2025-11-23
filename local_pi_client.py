#!/usr/bin/env python3
"""
Integrated Autonomous Chicken Farm Monitoring System
Combines disease detection with Pixhawk autonomous navigation
"""

import time
import cv2
import numpy as np
import requests
import threading
from datetime import datetime
from picamera2 import Picamera2
from tflite_runtime.interpreter import Interpreter
from dronekit import connect, VehicleMode, LocationGlobalRelative
import signal
import sys

# ==== CONFIGURATION ====
# Backend URLs
BACKEND_URL = "https://chicken-disease-detector-app-1.onrender.com/api/frame"
CHECK_START_URL = "https://chicken-disease-detector-app-1.onrender.com/check-start"
ALERT_URL = "https://chicken-disease-detector-app-1.onrender.com/api/alert"

# Pixhawk connection
PIXHAWK_CONNECTION = '/dev/serial0'
PIXHAWK_BAUD = 57600

# ML Model
MODEL_PATH = "chicken_disease_classifier_vgg16.tflite"
DISEASE_MAP = {
    0: 0, 1: 7, 2: 5, 3: 3, 4: 2, 5: 1, 6: 4, 7: 6, 8: 8
}

# Critical diseases that require immediate attention
CRITICAL_DISEASES = {0, 2, 3, 4, 8}

# Camera settings
CAPTURE_INTERVAL = 0.3
CAMERA_INIT_RETRIES = 5

# Simple flight parameters
TAKEOFF_ALTITUDE = 1.0  # Just 1 meter up
SCAN_DURATION = 10  # Take pictures for 10 seconds at 1m altitude

# ==== GLOBAL STATE ====
picam2 = None
vehicle = None
camera_lock = threading.Lock()
should_stop = False
autonomous_mode = False

# ML Model setup
interpreter = Interpreter(model_path=MODEL_PATH)
interpreter.allocate_tensors()
input_details = interpreter.get_input_details()
output_details = interpreter.get_output_details()
IMG_SIZE = (input_details[0]['shape'][2], input_details[0]['shape'][1])

# ==== HELPER FUNCTIONS ====

def preprocess_frame(frame):
    """Preprocess frame for ML model."""
    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGRA2RGB)
    frame_resized = cv2.resize(frame_rgb, IMG_SIZE)
    frame_normalized = frame_resized.astype(np.float32) / 255.0
    return np.expand_dims(frame_normalized, axis=0)

def predict_disease(frame):
    """Run disease prediction on frame."""
    input_data = preprocess_frame(frame)
    interpreter.set_tensor(input_details[0]['index'], input_data)
    interpreter.invoke()
    output_data = interpreter.get_tensor(output_details[0]['index'])
    pred_class = int(np.argmax(output_data))
    confidence = float(np.max(output_data))
    return DISEASE_MAP.get(pred_class, 0), confidence

def send_to_backend(image, disease_id, confidence, gps_location=None):
    """Send frame, prediction, and GPS location to backend."""
    h, w, _ = image.shape
    cv2.rectangle(image, (10, 10), (w-10, h-10), (0, 255, 0), 2)
    _, img_encoded = cv2.imencode('.jpg', image)
    
    files = {'image': ('frame.jpg', img_encoded.tobytes(), 'image/jpeg')}
    data = {
        'disease_id': str(disease_id),
        'confidence': confidence
    }
    
    # Add GPS data if available
    if gps_location:
        data['latitude'] = gps_location['lat']
        data['longitude'] = gps_location['lon']
        data['altitude'] = gps_location['alt']
    
    try:
        r = requests.post(BACKEND_URL, files=files, data=data, timeout=10)
        if r.status_code == 200:
            print(f"✅ Sent: Disease {disease_id} ({confidence:.2f}) @ GPS: {gps_location}")
            return True
    except Exception as e:
        print(f"⚠️ Backend error: {e}")
    return False

def send_critical_alert(disease_id, confidence, gps_location):
    """Send critical disease alert to backend."""
    try:
        alert_data = {
            'disease_id': disease_id,
            'confidence': confidence,
            'latitude': gps_location['lat'],
            'longitude': gps_location['lon'],
            'altitude': gps_location['alt'],
            'timestamp': datetime.utcnow().isoformat(),
            'alert_type': 'critical'
        }
        requests.post(ALERT_URL, json=alert_data, timeout=5)
        print(f"🚨 CRITICAL ALERT SENT - Disease {disease_id} @ {gps_location}")
    except Exception as e:
        print(f"⚠️ Alert send failed: {e}")

def initialize_camera():
    """Initialize Picamera2."""
    global picam2
    with camera_lock:
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
                print(f"📷 Initializing camera (attempt {attempt + 1})...")
                picam2 = Picamera2()
                config = picam2.create_preview_configuration(main={"size": (640, 480)})
                picam2.configure(config)
                picam2.start()
                time.sleep(2)
                print("✅ Camera ready!")
                return True
            except Exception as e:
                print(f"⚠️ Camera init failed: {e}")
                time.sleep(2)
        
        print("❌ Camera initialization failed")
        return False

def release_camera():
    """Release camera resources."""
    global picam2
    with camera_lock:
        if picam2 is not None:
            try:
                picam2.stop()
                picam2.close()
                time.sleep(1)
            except:
                pass
            picam2 = None
            print("✓ Camera released")

def connect_pixhawk():
    """Connect to Pixhawk flight controller."""
    global vehicle
    try:
        print(f"🔌 Connecting to Pixhawk on {PIXHAWK_CONNECTION}...")
        vehicle = connect(PIXHAWK_CONNECTION, baud=PIXHAWK_BAUD, wait_ready=True, timeout=30)
        print("✅ Pixhawk connected!")
        print(f"   Mode: {vehicle.mode.name}")
        print(f"   GPS: {vehicle.gps_0.fix_type} ({vehicle.gps_0.satellites_visible} sats)")
        return True
    except Exception as e:
        print(f"❌ Pixhawk connection failed: {e}")
        return False

def get_gps_location():
    """Get current GPS coordinates from Pixhawk."""
    if vehicle and vehicle.location.global_relative_frame.lat:
        return {
            'lat': vehicle.location.global_relative_frame.lat,
            'lon': vehicle.location.global_relative_frame.lon,
            'alt': vehicle.location.global_relative_frame.alt
        }
    return None

def simple_takeoff_and_scan():
    """Simple mission: Take off 1m, scan for 10 seconds, land."""
    global vehicle, picam2
    
    if not vehicle:
        print("⚠️ No Pixhawk - scanning from ground")
        return
    
    try:
        print("\n🚁 Starting simple scan mission...")
        
        # Take off to 1 meter
        print(f"   Taking off to {TAKEOFF_ALTITUDE}m...")
        vehicle.simple_takeoff(TAKEOFF_ALTITUDE)
        
        # Wait until reached target altitude
        while True:
            current_alt = vehicle.location.global_relative_frame.alt
            print(f"   Altitude: {current_alt:.1f}m / {TAKEOFF_ALTITUDE}m")
            
            if current_alt >= TAKEOFF_ALTITUDE * 0.95:
                print("✅ Target altitude reached!")
                break
            
            time.sleep(1)
        
        # Scan at this altitude
        print(f"📸 Scanning for {SCAN_DURATION} seconds...")
        scan_start = time.time()
        frame_count = 0
        
        while time.time() - scan_start < SCAN_DURATION:
            capture_and_analyze()
            frame_count += 1
            time.sleep(CAPTURE_INTERVAL)
        
        print(f"✅ Scan complete - {frame_count} frames captured")
        
        # Land
        print("🛬 Landing...")
        vehicle.mode = VehicleMode("LAND")
        
        # Wait until landed
        while vehicle.armed:
            current_alt = vehicle.location.global_relative_frame.alt
            print(f"   Descending... {current_alt:.1f}m")
            time.sleep(1)
        
        print("✅ Landed safely!")
        
    except Exception as e:
        print(f"⚠️ Mission error: {e}")
        # Emergency land
        try:
            vehicle.mode = VehicleMode("LAND")
        except:
            pass

def capture_and_analyze():
    """Capture frame, analyze, and send with GPS."""
    global picam2, vehicle
    
    if picam2 is None:
        return
    
    try:
        with camera_lock:
            frame = picam2.capture_array()
        
        # Predict disease
        disease_id, confidence = predict_disease(frame)
        
        # Get GPS location
        gps_location = get_gps_location()
        
        # Send to backend
        send_to_backend(frame, disease_id, confidence, gps_location)
        
        # Check for critical diseases
        if disease_id in CRITICAL_DISEASES and confidence > 0.75:
            print(f"🚨 CRITICAL: Disease {disease_id} detected with {confidence:.2f} confidence!")
            send_critical_alert(disease_id, confidence, gps_location)
            
    except Exception as e:
        print(f"⚠️ Capture error: {e}")

# ==== SIGNAL HANDLING ====
def signal_handler(sig, frame):
    global should_stop, vehicle
    print("\n🛑 Emergency shutdown initiated...")
    should_stop = True
    
    # Disarm drone first (critical safety)
    if vehicle and vehicle.armed:
        print("🔓 Emergency disarm...")
        try:
            vehicle.armed = False
            time.sleep(2)
        except:
            pass
    
    # Release camera
    release_camera()
    
    # Close vehicle connection
    if vehicle:
        try:
            vehicle.close()
        except:
            pass
    
    print("✅ Emergency shutdown complete")
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# ==== MAIN LOOP ====
def arm_drone():
    """Arm the drone when feed starts."""
    global vehicle
    
    if not vehicle:
        print("⚠️ No Pixhawk connection - skipping arm")
        return False
    
    try:
        print("\n🔐 Arming sequence initiated...")
        
        # Wait for vehicle to be armable
        print("   Checking pre-arm conditions...")
        timeout = 30
        start_time = time.time()
        while not vehicle.is_armable:
            if time.time() - start_time > timeout:
                print("⚠️ Timeout waiting for armable state")
                return False
            print(f"   Waiting for armable... GPS: {vehicle.gps_0.fix_type} ({vehicle.gps_0.satellites_visible} sats)")
            time.sleep(1)
        
        print("✓ Pre-arm checks passed")
        
        # Change to GUIDED mode
        print("   Changing to GUIDED mode...")
        vehicle.mode = VehicleMode("GUIDED")
        while vehicle.mode.name != "GUIDED":
            time.sleep(0.5)
        print("✓ Mode: GUIDED")
        
        # Arm motors
        print("   Arming motors...")
        vehicle.armed = True
        
        timeout = 10
        start_time = time.time()
        while not vehicle.armed:
            if time.time() - start_time > timeout:
                print("⚠️ Arming timeout")
                return False
            time.sleep(0.5)
        
        print("✅ DRONE ARMED - Motors ready!")
        return True
        
    except Exception as e:
        print(f"❌ Arming failed: {e}")
        return False

def disarm_drone():
    """Disarm the drone when feed stops."""
    global vehicle
    
    if not vehicle or not vehicle.armed:
        return
    
    try:
        print("\n🔓 Disarming sequence...")
        
        # If in autonomous mode, return to launch first
        if vehicle.mode.name == "GUIDED":
            print("   Returning to safe position...")
            vehicle.mode = VehicleMode("RTL")  # Return to Launch
            time.sleep(3)  # Give it time to start RTL
        
        # Disarm
        print("   Disarming motors...")
        vehicle.armed = False
        
        timeout = 10
        start_time = time.time()
        while vehicle.armed:
            if time.time() - start_time > timeout:
                print("⚠️ Disarm timeout - forcing")
                break
            time.sleep(0.5)
        
        print("✅ DRONE DISARMED - Motors stopped")
        
    except Exception as e:
        print(f"⚠️ Disarm error: {e}")

def main():
    global autonomous_mode
    
    print("=" * 60)
    print("🚁 Kgosi BioDrone - Simple Aerial Scanning")
    print("=" * 60)
    print("Mission: Take off 1m → Scan 10s → Land")
    print("=" * 60)
    
    # Connect to Pixhawk
    pixhawk_connected = connect_pixhawk()
    
    # Track if we're currently armed
    is_armed = False
    mission_completed = False
    
    # Main operation loop
    try:
        while not should_stop:
            # Check backend for commands
            try:
                r = requests.get(CHECK_START_URL, timeout=5)
                feed_on = r.json().get("start", False)
                auto_mode = r.json().get("autonomous", False)
            except:
                feed_on = False
                auto_mode = False
                time.sleep(5)
                continue
            
            autonomous_mode = auto_mode and pixhawk_connected
            
            if feed_on:
                # ARM DRONE when feed starts (first time only)
                if pixhawk_connected and not is_armed:
                    if arm_drone():
                        is_armed = True
                        mission_completed = False
                    else:
                        print("⚠️ Failed to arm - continuing with camera only")
                
                # Initialize camera if needed
                if picam2 is None:
                    if not initialize_camera():
                        time.sleep(5)
                        continue
                
                if autonomous_mode and not mission_completed:
                    # Execute simple takeoff, scan, land mission (ONCE)
                    print("\n🤖 AUTONOMOUS MISSION STARTING")
                    simple_takeoff_and_scan()
                    mission_completed = True
                    print("✅ Mission complete - returning to stationary mode")
                    # Continue with ground scanning after landing
                
                # Regular ground-based monitoring (or after mission)
                capture_and_analyze()
                time.sleep(CAPTURE_INTERVAL)
                    
            else:
                # Feed off - disarm and release resources
                if is_armed:
                    disarm_drone()
                    is_armed = False
                    mission_completed = False
                
                if picam2 is not None:
                    release_camera()
                    print("⏸ Monitoring paused")
                time.sleep(1)
                
    except Exception as e:
        print(f"❌ Fatal error: {e}")
    finally:
        release_camera()
        if vehicle:
            vehicle.close()
        print("✅ Shutdown complete")

if __name__ == "__main__":
    main()