## app.py

import eventlet
eventlet.monkey_patch()  # Must be the first import for Eventlet

import os
import base64
import signal
import atexit
import requests
import time
from functools import wraps
from dotenv import load_dotenv
from datetime import datetime, timedelta

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
PAYSTACK_SECRET_KEY = os.environ.get("PAYSTACK_SECRET_KEY")
PAYSTACK_PUBLIC_KEY = os.environ.get("PAYSTACK_PUBLIC_KEY")

# ==============================
# Payment Configuration
# ==============================
DOWNLOAD_COST_ZAR = 50  # 50 ZAR per PDF download
SUBSCRIPTION_COST_ZAR = 500  # 500 ZAR per month
PAYSTACK_CURRENCY = "ZAR"  # South African Rand
FREE_TIER_SCANS_PER_DAY = 5
FREE_TIER_DOWNLOADS_PER_DAY = 5

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
transactions_col = db.transactions
subscriptions_col = db.subscriptions

# ==============================
# Shared state
# ==============================
latest_frame = None
latest_prediction = None
captured_frame = None
captured_prediction = None
pi_start_trigger = {"start": False}

# ==============================
# Cleanup handlers
# ==============================
def cleanup_resources():
    """Clean up resources on shutdown."""
    global latest_frame, latest_prediction, captured_frame, captured_prediction, pi_start_trigger
    try:
        print("🛑 Backend shutting down - cleaning up resources...")
        pi_start_trigger["start"] = False
        latest_frame = None
        latest_prediction = None
        captured_frame = None
        captured_prediction = None
        if client:
            client.close()
            print("✓ MongoDB connection closed")
    except Exception as e:
        print(f"⚠️ Error during cleanup: {e}")

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
# Subscription helper functions
# ==============================
def check_user_subscription(username):
    """Check if user has active premium subscription."""
    subscription = subscriptions_col.find_one({
        "username": username,
        "status": "active",
        "end_date": {"$gt": datetime.utcnow()}
    })
    return subscription is not None

def get_user_subscription_status(username):
    """Get detailed subscription status for user."""
    subscription = subscriptions_col.find_one(
        {"username": username},
        sort=[("end_date", -1)]
    )
    
    if not subscription:
        return {"is_premium": False, "status": "none"}
    
    is_active = (
        subscription["status"] == "active" and 
        subscription["end_date"] > datetime.utcnow()
    )
    
    return {
        "is_premium": is_active,
        "status": subscription["status"],
        "start_date": subscription.get("start_date"),
        "end_date": subscription.get("end_date"),
        "days_remaining": (subscription["end_date"] - datetime.utcnow()).days if is_active else 0
    }

# ==============================
# Usage stats helper functions
# ==============================
def get_today_start_end():
    """Get start and end of today in UTC."""
    today = datetime.utcnow().date()
    start = datetime.combine(today, datetime.min.time())
    end = datetime.combine(today, datetime.max.time())
    return start, end

def get_user_usage_stats(username):
    """Get user's daily usage statistics."""
    try:
        is_premium = check_user_subscription(username)
        start, end = get_today_start_end()
        
        # Count today's scans (analysis results created today)
        scans_today = results_col.count_documents({
            "username": username,
            "created_at": {"$gte": start, "$lte": end}
        })
        
        # Count today's completed downloads (paid)
        downloads_today = transactions_col.count_documents({
            "username": username,
            "type": "download",
            "status": "completed",
            "verified_at": {"$gte": start, "$lte": end}
        })
        
        if is_premium:
            scans_limit = None
            downloads_limit = None
        else:
            scans_limit = FREE_TIER_SCANS_PER_DAY
            downloads_limit = None  # No download limit, but all paid
        
        return {
            "scans_used": scans_today,
            "scans_limit": scans_limit,
            "scans_remaining": scans_limit - scans_today if scans_limit else None,
            "downloads_used": downloads_today,
            "downloads_limit": downloads_limit,
            "downloads_remaining": None,  # No limit on downloads
            "is_premium": is_premium
        }
    except Exception as e:
        print(f"⚠️ Error getting usage stats: {e}")
        return {
            "scans_used": 0,
            "scans_limit": FREE_TIER_SCANS_PER_DAY,
            "scans_remaining": FREE_TIER_SCANS_PER_DAY,
            "downloads_used": 0,
            "downloads_limit": None,
            "downloads_remaining": None,
            "is_premium": False
        }

# ==============================
# Paystack helper functions
# ==============================
def initialize_paystack_payment(email, amount, metadata, callback_url=None):
    """Initialize a Paystack payment - Let Paystack generate the reference."""
    url = "https://api.paystack.co/transaction/initialize"
    headers = {
        "Authorization": f"Bearer {PAYSTACK_SECRET_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "email": email,
        "amount": amount,
        "currency": PAYSTACK_CURRENCY,
        "metadata": metadata
    }
    
    if callback_url:
        payload["callback_url"] = callback_url
    
    try:
        print(f"🔍 Paystack Request - Email: {email}, Amount: {amount} ({PAYSTACK_CURRENCY}) [AUTO-REFERENCE]")
        if callback_url:
            print(f"🔗 Callback URL: {callback_url}")
        response = requests.post(url, json=payload, headers=headers)
        print(f"📊 Paystack Response Status: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            if data.get('status') and data.get('data'):
                assigned_ref = data['data'].get('reference', 'N/A')
                print(f"✅ Paystack assigned reference: {assigned_ref}")
            return data
        else:
            print(f"⚠️ Paystack error response: {response.text}")
            response.raise_for_status()
            
    except requests.exceptions.RequestException as e:
        print(f"⚠️ Paystack initialization error: {e}")
        if hasattr(e, 'response') and hasattr(e.response, 'text'):
            print(f"⚠️ Response body: {e.response.text}")
        return None

def verify_paystack_payment(reference):
    """Verify a Paystack payment using reference."""
    url = f"https://api.paystack.co/transaction/verify/{reference}"
    headers = {
        "Authorization": f"Bearer {PAYSTACK_SECRET_KEY}"
    }
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"⚠️ Paystack verification error: {e}")
        return None

# ==============================
# Routes
# ==============================
@app.route("/")
@login_required
def index():
    username = session["user"]
    subscription_status = get_user_subscription_status(username)
    
    return render_template("index.html", 
                         paystack_public_key=PAYSTACK_PUBLIC_KEY,
                         download_cost_zar=DOWNLOAD_COST_ZAR,
                         subscription_cost_zar=SUBSCRIPTION_COST_ZAR,
                         paystack_currency=PAYSTACK_CURRENCY,
                         is_premium=subscription_status["is_premium"],
                         subscription_status=subscription_status)


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
        email = request.form.get("email")
        password = request.form.get("password")
        confirm_password = request.form.get("confirm_password")

        if not username or not email or not password:
            return render_template("register.html", error="All fields are required")
        
        if password != confirm_password:
            return render_template("register.html", error="Passwords do not match")
        
        if "@" not in email or "." not in email:
            return render_template("register.html", error="Invalid email address")

        if users_col.find_one({"username": username}):
            return render_template("register.html", error="Username already exists")
        
        if users_col.find_one({"email": email}):
            return render_template("register.html", error="Email already registered")

        users_col.insert_one({
            "username": username,
            "email": email,
            "password": generate_password_hash(password),
            "verified": False,
            "created_at": datetime.utcnow()
        })

        session["user"] = username
        print(f"✅ New user registered: {username} ({email})")
        return redirect(url_for("index"))

    return render_template("register.html")

@app.route("/logout")
def logout():
    user = session.get("user", "Unknown")
    session.clear()
    print(f"👋 User logged out: {user}")
    return redirect(url_for("login"))

# --- Subscription Endpoints ---
@app.route("/api/subscription-status", methods=["GET"])
@login_required
def subscription_status():
    """Get user's subscription status."""
    try:
        username = session["user"]
        status = get_user_subscription_status(username)
        return jsonify(status), 200
    except Exception as e:
        print(f"⚠️ Error getting subscription status: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/usage-stats", methods=["GET"])
@login_required
def usage_stats():
    """Get user's usage statistics for the current day."""
    try:
        username = session["user"]
        
        # Debug: Log detailed stats
        start, end = get_today_start_end()
        print(f"\n📊 Usage Stats for {username}")
        print(f"   Time range: {start} to {end}")
        
        # Count scans
        scans_today = results_col.count_documents({
            "username": username,
            "created_at": {"$gte": start, "$lte": end}
        })
        print(f"   Scans today: {scans_today}")
        
        # Count completed downloads (both paid and free)
        downloads_today = transactions_col.count_documents({
            "username": username,
            "type": "download",
            "status": "completed",
            "verified_at": {"$gte": start, "$lte": end}
        })
        print(f"   Downloads completed (with verified_at): {downloads_today}")
        
        # Also check for free downloads that might not have verified_at
        all_transactions = list(transactions_col.find({
            "username": username,
            "type": "download",
            "status": "completed"
        }))
        print(f"   Total download transactions: {len(all_transactions)}")
        for t in all_transactions:
            print(f"      - {t.get('reference')} at {t.get('verified_at', 'NO VERIFIED_AT')}")
        
        stats = get_user_usage_stats(username)
        print(f"   Final stats: {stats}\n")
        return jsonify(stats), 200
    except Exception as e:
        print(f"⚠️ Error getting usage stats: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/initialize-subscription", methods=["POST"])
@login_required
def initialize_subscription():
    """Initialize subscription payment."""
    try:
        username = session["user"]
        
        # Check if user already has active subscription
        if check_user_subscription(username):
            return jsonify({
                "status": "error",
                "message": "You already have an active subscription"
            }), 400
        
        user = users_col.find_one({"username": username})
        if not user:
            return jsonify({"status": "error", "message": "User not found"}), 400
        
        email = user.get("email")
        if not email or "@" not in email:
            return jsonify({
                "status": "error",
                "message": "User email not set. Please update your profile."
            }), 400
        
        # Clean up old pending subscription transactions (older than 10 minutes)
        ten_min_ago = datetime.utcnow() - timedelta(minutes=10)
        transactions_col.delete_many({
            "email": email,
            "type": "subscription",
            "status": "pending",
            "timestamp": {"$lt": ten_min_ago}
        })
        
        amount_cents = int(SUBSCRIPTION_COST_ZAR * 100)
        
        metadata = {
            "username": username,
            "email": email,
            "action": "subscription",
            "subscription_type": "premium_monthly",
            "currency": PAYSTACK_CURRENCY
        }
        
        print(f"💎 Initializing subscription - Email: {email}, Amount: R{SUBSCRIPTION_COST_ZAR}.00")
        
        # Generate callback URL for Paystack to redirect back
        callback_url = request.url_root.rstrip('/') + '/payment-callback'
        
        # Let Paystack auto-generate the reference
        response = initialize_paystack_payment(email, amount_cents, metadata, callback_url)
        
        if not response:
            return jsonify({"status": "error", "message": "Payment service unavailable"}), 500
        
        if response.get("status"):
            paystack_reference = response["data"]["reference"]
            
            transaction_data = {
                "username": username,
                "email": email,
                "reference": paystack_reference,
                "amount_zar": SUBSCRIPTION_COST_ZAR,
                "amount_cents": amount_cents,
                "type": "subscription",
                "status": "pending",
                "currency": PAYSTACK_CURRENCY,
                "timestamp": datetime.utcnow()
            }
            transactions_col.insert_one(transaction_data)
            
            print(f"✅ Subscription payment initialized - Reference: {paystack_reference}")
            
            return jsonify({
                "status": "ok",
                "payment_url": response["data"]["authorization_url"],
                "reference": paystack_reference
            }), 200
        else:
            error_msg = response.get("message", "Unknown error")
            print(f"⚠️ Paystack error: {error_msg}")
            return jsonify({"status": "error", "message": error_msg}), 400
    except Exception as e:
        print(f"⚠️ Error initializing subscription: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/verify-subscription", methods=["POST"])
@login_required
def verify_subscription():
    """Verify subscription payment and activate premium features."""
    try:
        data = request.json
        reference = data.get("reference")
        username = session["user"]
        
        print(f"🔍 Verifying subscription - Username: {username}, Reference: {reference}")
        
        if not reference:
            print("⚠️ No reference provided")
            return jsonify({"status": "error", "message": "No reference provided"}), 400
        
        response = verify_paystack_payment(reference)
        print(f"📊 Paystack verification response: {response}")
        
        if response and response.get("status") and response["data"]["status"] == "success":
            print(f"✅ Payment verified successfully")
            
            # Update transaction status
            transactions_col.update_one(
                {"reference": reference},
                {"$set": {"status": "completed", "verified_at": datetime.utcnow()}}
            )
            
            # Create or update subscription
            start_date = datetime.utcnow()
            end_date = start_date + timedelta(days=30)
            
            subscriptions_col.update_one(
                {"username": username},
                {
                    "$set": {
                        "username": username,
                        "status": "active",
                        "start_date": start_date,
                        "end_date": end_date,
                        "payment_reference": reference,
                        "amount_paid": SUBSCRIPTION_COST_ZAR,
                        "updated_at": datetime.utcnow()
                    }
                },
                upsert=True
            )
            
            print(f"✅ Subscription activated for {username}: {reference}")
            return jsonify({
                "status": "ok",
                "message": "Subscription activated successfully",
                "end_date": end_date.isoformat()
            }), 200
        else:
            error_msg = "Payment not successful"
            if response:
                error_msg = response.get("message", error_msg)
                print(f"⚠️ Paystack verification failed: {error_msg}")
            return jsonify({
                "status": "error",
                "message": error_msg
            }), 400
    except Exception as e:
        print(f"⚠️ Error verifying subscription: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


# --- Pi Endpoints ---
@app.route("/api/frame", methods=["POST"])
def receive_frame():
    """Receive frame + ML prediction from Pi client and broadcast to connected clients."""
    global latest_frame, latest_prediction
    try:
        image_file = request.files.get("image")
        if not image_file:
            return jsonify({"status": "error", "message": "No image received"}), 400

        disease_id = request.form.get("disease_id")
        confidence = request.form.get("confidence")

        # Store frame and Pi's prediction
        latest_frame = base64.b64encode(image_file.read()).decode("utf-8")
        latest_prediction = {
            "disease_id": int(disease_id) if disease_id else 0,
            "confidence": float(confidence) if confidence else 0.0
        }
        
        print(f"🎯 Frame received from Pi - Disease ID: {latest_prediction['disease_id']}, Confidence: {latest_prediction['confidence']:.2f}")
        
        socketio.emit("new_frame", {
            "frame": latest_frame,
            "disease_id": latest_prediction["disease_id"],
            "confidence": latest_prediction["confidence"]
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
    """Capture the current live frame + prediction for analysis."""
    global captured_frame, captured_prediction, latest_frame, latest_prediction
    try:
        if latest_frame and latest_prediction:
            captured_frame = latest_frame
            captured_prediction = latest_prediction.copy()
            
            socketio.emit("frame_captured", {
                "frame": captured_frame,
                "disease_id": captured_prediction["disease_id"],
                "confidence": captured_prediction["confidence"]
            })
            
            print(f"📸 Frame captured - Disease ID: {captured_prediction['disease_id']}, Confidence: {captured_prediction['confidence']:.2f}")
            return jsonify({"status": "ok"}), 200
        
        return jsonify({"status": "error", "message": "No live frame available"}), 400
    except Exception as e:
        print(f"⚠️ Error capturing frame: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/analyze-frame", methods=["POST"])
@login_required
def analyze_frame():
    """Analyze the captured frame using Pi's ML prediction."""
    global captured_frame, captured_prediction
    try:
        username = session["user"]
        is_premium = check_user_subscription(username)
        
        # Check usage limits for free users
        if not is_premium:
            start, end = get_today_start_end()
            scans_today = results_col.count_documents({
                "username": username,
                "created_at": {"$gte": start, "$lte": end}
            })
            
            if scans_today >= FREE_TIER_SCANS_PER_DAY:
                print(f"⚠️ User {username} reached daily scan limit ({FREE_TIER_SCANS_PER_DAY})")
                socketio.emit("frame_analyzed", {
                    "status": "limit_reached",
                    "disease_id": 0,
                    "confidence": 0,
                    "message": "Daily scan limit reached. Upgrade to Premium for unlimited scans.",
                    "limit_reached": True
                })
                return jsonify({
                    "status": "error",
                    "message": "Daily scan limit reached",
                    "limit_reached": True
                }), 429
        
        if not captured_frame or not captured_prediction:
            return jsonify({"status": "error", "message": "No captured frame or prediction available"}), 400

        # Use Pi's ML prediction
        disease_id = captured_prediction["disease_id"]
        confidence = captured_prediction["confidence"]
        
        print(f"🔍 Analyzing frame - Using Pi prediction: Disease ID {disease_id}, Confidence {confidence:.2f}")
        
        # Save result with timestamp
        result_data = {
            "username": username,
            "disease_id": disease_id,
            "confidence": confidence,
            "frame": captured_frame,
            "created_at": datetime.utcnow()
        }
        results_col.insert_one(result_data)
        
        print(f"✅ Result saved - Disease ID: {disease_id}, Confidence: {confidence:.2f}")
        
        socketio.emit("frame_analyzed", {
            "disease_id": disease_id,
            "confidence": confidence
        })
        
        return jsonify({
            "status": "ok",
            "disease_id": disease_id,
            "confidence": confidence
        }), 200
    except Exception as e:
        print(f"⚠️ Error analyzing frame: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/save-result", methods=["POST"])
@login_required
def save_result():
    """Save analysis result to database."""
    try:
        data = request.json
        if not data:
            return jsonify({"status": "error", "message": "No data"}), 400

        data["username"] = session["user"]
        data["created_at"] = datetime.utcnow()
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
            if "created_at" in r:
                r["timestamp"] = r["created_at"].isoformat()
        print(f"📋 Retrieved {len(results)} history records for {username}")
        return jsonify(results)
    except Exception as e:
        print(f"⚠️ Error retrieving history: {e}")
        return jsonify([]), 500


# --- Payment Endpoints ---
@app.route("/api/initialize-payment", methods=["POST"])
@login_required
def initialize_payment():
    """Initialize Paystack payment for PDF download in ZAR."""
    try:
        username = session["user"]
        data = request.json
        amount_zar = data.get("amount", DOWNLOAD_COST_ZAR)
        
        print(f"💳 Initialize payment request - User: {username}, Amount: R{amount_zar}")
        
        # Check if user has premium subscription (free downloads for premium users)
        if check_user_subscription(username):
            print(f"💎 Premium user detected - free download allowed")
            return jsonify({
                "status": "ok",
                "is_premium": True,
                "message": "Premium user - download is free"
            }), 200
        
        # Free tier users must pay for downloads
        print(f"💰 Free tier user - proceeding to payment for R{amount_zar}")
        
        amount_cents = int(amount_zar * 100)
        disease_name = data.get("disease_name", "Report")
        
        user = users_col.find_one({"username": username})
        if not user:
            print(f"❌ User not found: {username}")
            return jsonify({"status": "error", "message": "User not found"}), 400
        
        email = user.get("email")
        if not email or "@" not in email:
            print(f"❌ Invalid email for {username}: {email}")
            return jsonify({"status": "error", "message": "User email not set. Please update your profile."}), 400
        
        # Clean up old pending transactions (older than 10 minutes)
        ten_min_ago = datetime.utcnow() - timedelta(minutes=10)
        old_count = transactions_col.delete_many({
            "email": email,
            "type": "download",
            "status": "pending",
            "timestamp": {"$lt": ten_min_ago}
        }).deleted_count
        if old_count > 0:
            print(f"🗑️ Cleaned up {old_count} old pending transactions")
        
        metadata = {
            "username": username,
            "disease": disease_name,
            "action": "pdf_download",
            "email": email,
            "currency": PAYSTACK_CURRENCY
        }
        
        print(f"💳 Initializing PAID download - User: {username}, Email: {email}, Amount: R{amount_zar}.00 ({amount_cents} cents)")
        callback_url = request.url_root.rstrip('/') + '/payment-callback'
        response = initialize_paystack_payment(email, amount_cents, metadata, callback_url)
        
        if not response:
            print(f"❌ Paystack service unavailable")
            return jsonify({"status": "error", "message": "Payment service unavailable"}), 500
        
        if response.get("status"):
            paystack_reference = response["data"]["reference"]
            transaction_data = {
                "username": username,
                "email": email,
                "reference": paystack_reference,
                "amount_zar": amount_zar,
                "amount_cents": amount_cents,
                "disease": disease_name,
                "type": "download",
                "status": "pending",
                "currency": PAYSTACK_CURRENCY,
                "timestamp": datetime.utcnow()
            }
            transactions_col.insert_one(transaction_data)
            print(f"✅ Payment initialized - Reference: {paystack_reference}, URL: {response['data']['authorization_url']}")
            
            return jsonify({
                "status": "ok",
                "payment_url": response["data"]["authorization_url"],
                "reference": paystack_reference
            }), 200
        else:
            error_msg = response.get("message", "Unknown error")
            print(f"⚠️ Paystack error: {error_msg}")
            return jsonify({"status": "error", "message": error_msg}), 400
    except Exception as e:
        print(f"⚠️ Error initializing payment: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/verify-payment", methods=["POST"])
@login_required
def verify_payment():
    """Verify Paystack payment and allow PDF download."""
    try:
        data = request.json
        reference = data.get("reference")
        username = session["user"]
        
        if not reference:
            return jsonify({"status": "error", "message": "No reference provided"}), 400
        
        print(f"🔍 Verifying payment - Username: {username}, Reference: {reference}")
        response = verify_paystack_payment(reference)
        
        if response and response.get("status") and response["data"]["status"] == "success":
            # Update transaction status
            transactions_col.update_one(
                {"reference": reference},
                {"$set": {"status": "completed", "verified_at": datetime.utcnow()}}
            )
            print(f"✅ Payment verified for {username}: {reference}")
            return jsonify({
                "status": "ok",
                "message": "Payment verified successfully",
                "can_download": True
            }), 200
        
        return jsonify({
            "status": "error",
            "message": "Payment verification failed"
        }), 400
    except Exception as e:
        print(f"⚠️ Error verifying payment: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/transaction-history", methods=["GET"])
@login_required
def transaction_history():
    """Get user's transaction history."""
    try:
        username = session["user"]
        transactions = list(transactions_col.find(
            {"username": username}
        ).sort("timestamp", -1))
        
        for t in transactions:
            t["_id"] = str(t["_id"])
            t["timestamp"] = t["timestamp"].isoformat()
        
        return jsonify(transactions), 200
    except Exception as e:
        print(f"⚠️ Error retrieving transactions: {e}")
        return jsonify([]), 500


@app.route("/api/payment-config", methods=["GET"])
def payment_config():
    """Return payment configuration to frontend."""
    return jsonify({
        "download_cost_zar": DOWNLOAD_COST_ZAR,
        "subscription_cost_zar": SUBSCRIPTION_COST_ZAR,
        "currency": PAYSTACK_CURRENCY,
        "paystack_public_key": PAYSTACK_PUBLIC_KEY
    }), 200


@app.route("/payment-callback", methods=["GET"])
def payment_callback():
    """Handle Paystack payment callback."""
    try:
        reference = request.args.get("reference")
        print(f"\n🔗 Payment callback received - Reference: {reference}")
        
        if not reference:
            print("⚠️ No reference in callback URL")
            return redirect(url_for("index"))
        
        # Check if user is logged in
        if "user" not in session:
            print(f"⚠️ User not logged in on callback")
            session["pending_payment_ref"] = reference
            return redirect(url_for("login"))
        
        username = session["user"]
        print(f"✅ Verifying payment for {username} - Reference: {reference}")
        
        # Verify the payment
        response = verify_paystack_payment(reference)
        
        if response and response.get("status") and response["data"]["status"] == "success":
            print(f"✅ Payment successful - marking as completed")
            
            # Find the transaction to determine type (subscription or download)
            transaction = transactions_col.find_one({"reference": reference})
            
            if transaction:
                transaction_type = transaction.get("type")
                print(f"📋 Transaction type: {transaction_type}")
                
                # Update transaction status
                transactions_col.update_one(
                    {"reference": reference},
                    {"$set": {"status": "completed", "verified_at": datetime.utcnow()}}
                )
                
                # If subscription, activate premium
                if transaction_type == "subscription":
                    print(f"💎 Activating premium subscription for {username}")
                    start_date = datetime.utcnow()
                    end_date = start_date + timedelta(days=30)
                    
                    subscriptions_col.update_one(
                        {"username": username},
                        {
                            "$set": {
                                "username": username,
                                "status": "active",
                                "start_date": start_date,
                                "end_date": end_date,
                                "payment_reference": reference,
                                "amount_paid": SUBSCRIPTION_COST_ZAR,
                                "updated_at": datetime.utcnow()
                            }
                        },
                        upsert=True
                    )
                    print(f"✅ Premium subscription activated until {end_date}")
                else:
                    print(f"💰 Download payment verified")
            else:
                print(f"⚠️ Transaction not found in database for reference: {reference}")
            
            # Redirect back to home with success message in URL
            return redirect(url_for("index") + f"?payment_success=true&ref={reference}")
        else:
            print(f"⚠️ Payment verification failed")
            return redirect(url_for("index") + f"?payment_failed=true&ref={reference}")
            
    except Exception as e:
        print(f"⚠️ Error handling payment callback: {e}")
        import traceback
        traceback.print_exc()
        return redirect(url_for("index"))


@app.route("/health", methods=["GET"])
def health_check():
    """Health check endpoint for monitoring."""
    try:
        db.command("ping")
        return jsonify({
            "status": "healthy",
            "pi_feed": pi_start_trigger["start"],
            "has_frame": latest_frame is not None
        }), 200
    except Exception as e:
        print(f"⚠️ Health check failed: {e}")
        return jsonify({"status": "unhealthy", "error": str(e)}), 500

# ==============================
# Statistics & Monitoring Endpoints
# ==============================

@app.route("/api/session-stats", methods=["GET"])
@login_required
def session_stats():
    """Get real-time session statistics for current user."""
    try:
        username = session["user"]
        start, end = get_today_start_end()
        
        # Count total detections today
        total_detections = results_col.count_documents({
            "username": username,
            "created_at": {"$gte": start, "$lte": end}
        })
        
        # Get all results for today to calculate stats
        results_today = list(results_col.find({
            "username": username,
            "created_at": {"$gte": start, "$lte": end}
        }).sort("created_at", -1))
        
        # Calculate average confidence
        avg_confidence = 0
        if results_today:
            confidences = [r.get("confidence", 0) for r in results_today]
            avg_confidence = sum(confidences) / len(confidences) if confidences else 0
        
        # Count alerts (critical issues - high confidence predictions for severe diseases)
        alerts = sum(1 for r in results_today if r.get("confidence", 0) > 0.8 and r.get("disease_id", 0) in [0, 3, 4])
        
        # Categorize health status based on disease severity
        # 0=Coccidiosis, 1=Healthy, 2=Newcastle, 3=Salmonella, 4=Fowl Pox, 5=Aspergillosis, 6=Infectious Bursal, 7=Gumboro, 8=Marek's
        SEVERE_DISEASES = {0, 2, 3, 4, 8}  # Critical conditions
        WARNING_DISEASES = {5, 6, 7}  # Warning conditions
        HEALTHY_DISEASES = {1}  # Healthy
        
        healthy_count = sum(1 for r in results_today if r.get("disease_id", 1) in HEALTHY_DISEASES)
        warning_count = sum(1 for r in results_today if r.get("disease_id") in WARNING_DISEASES)
        critical_count = sum(1 for r in results_today if r.get("disease_id") in SEVERE_DISEASES)
        
        # Calculate analysis rate (% of frames that resulted in analysis)
        # This is based on total results vs potential frames
        analysis_rate = (total_detections / max(1, len(results_today))) * 100 if results_today else 0
        
        stats = {
            "detections": total_detections,
            "avg_confidence": round(avg_confidence * 100, 1),  # Convert to percentage
            "alerts": alerts,
            "healthy_birds": healthy_count,
            "warnings": warning_count,
            "critical_issues": critical_count,
            "analysis_rate": round(analysis_rate, 1)
        }
        
        print(f"📊 Session stats for {username}: {stats}")
        return jsonify(stats), 200
        
    except Exception as e:
        print(f"⚠️ Error getting session stats: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            "detections": 0,
            "avg_confidence": 0,
            "alerts": 0,
            "healthy_birds": 0,
            "warnings": 0,
            "critical_issues": 0,
            "analysis_rate": 0
        }), 500


@app.route("/api/disease-summary", methods=["GET"])
@login_required
def disease_summary():
    """Get summary of all diseases detected today."""
    try:
        username = session["user"]
        start, end = get_today_start_end()
        
        # Disease mapping
        DISEASE_NAMES = {
            0: "Coccidiosis",
            1: "Healthy",
            2: "Newcastle Disease",
            3: "Salmonella",
            4: "Fowl Pox",
            5: "Aspergillosis",
            6: "Infectious Bursal",
            7: "Gumboro",
            8: "Marek's Disease"
        }
        
        results_today = results_col.find({
            "username": username,
            "created_at": {"$gte": start, "$lte": end}
        })
        
        disease_counts = {}
        for result in results_today:
            disease_id = result.get("disease_id", 1)
            disease_counts[disease_id] = disease_counts.get(disease_id, 0) + 1
        
        # Format response
        disease_summary = [
            {
                "disease_id": disease_id,
                "name": DISEASE_NAMES.get(disease_id, "Unknown"),
                "count": count
            }
            for disease_id, count in sorted(disease_counts.items())
        ]
        
        print(f"🦠 Disease summary for {username}: {disease_summary}")
        return jsonify(disease_summary), 200
        
    except Exception as e:
        print(f"⚠️ Error getting disease summary: {e}")
        return jsonify([]), 500


@app.route("/api/session-duration", methods=["GET"])
@login_required
def session_duration():
    """Get current session duration based on active detection period."""
    try:
        username = session["user"]
        start, end = get_today_start_end()
        
        # Get first and last detection times today
        first_result = results_col.find_one(
            {"username": username, "created_at": {"$gte": start, "$lte": end}},
            sort=[("created_at", 1)]
        )
        last_result = results_col.find_one(
            {"username": username, "created_at": {"$gte": start, "$lte": end}},
            sort=[("created_at", -1)]
        )
        
        if first_result and last_result:
            duration = last_result["created_at"] - first_result["created_at"]
            minutes = duration.total_seconds() / 60
            seconds = int(duration.total_seconds() % 60)
            hours = int(minutes // 60)
            minutes = int(minutes % 60)
            
            duration_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}" if hours > 0 else f"{minutes:02d}:{seconds:02d}"
            return jsonify({"duration": duration_str, "seconds": duration.total_seconds()}), 200
        
        return jsonify({"duration": "00:00", "seconds": 0}), 200
        
    except Exception as e:
        print(f"⚠️ Error getting session duration: {e}")
        return jsonify({"duration": "00:00", "seconds": 0}), 500


@app.route("/api/detailed-results", methods=["GET"])
@login_required
def detailed_results():
    """Get detailed results with health classification."""
    try:
        username = session["user"]
        start, end = get_today_start_end()
        limit = request.args.get("limit", 50, type=int)
        
        DISEASE_NAMES = {
            0: "Coccidiosis",
            1: "Healthy",
            2: "Newcastle Disease",
            3: "Salmonella",
            4: "Fowl Pox",
            5: "Aspergillosis",
            6: "Infectious Bursal",
            7: "Gumboro",
            8: "Marek's Disease"
        }
        
        SEVERITY = {
            0: "critical", 1: "healthy", 2: "critical", 3: "critical",
            4: "critical", 5: "warning", 6: "warning", 7: "warning", 8: "critical"
        }
        
        results = list(results_col.find(
            {"username": username, "created_at": {"$gte": start, "$lte": end}}
        ).sort("created_at", -1).limit(limit))
        
        detailed = []
        for r in results:
            disease_id = r.get("disease_id", 1)
            detailed.append({
                "id": str(r["_id"]),
                "disease_id": disease_id,
                "disease_name": DISEASE_NAMES.get(disease_id, "Unknown"),
                "confidence": round(r.get("confidence", 0) * 100, 1),
                "severity": SEVERITY.get(disease_id, "unknown"),
                "timestamp": r.get("created_at").isoformat(),
                "frame": r.get("frame", "")[:50] + "..." if r.get("frame") else ""  # Truncate for size
            })
        
        return jsonify(detailed), 200
        
    except Exception as e:
        print(f"⚠️ Error getting detailed results: {e}")
        return jsonify([]), 500


@app.route("/api/alerts-today", methods=["GET"])
@login_required
def alerts_today():
    """Get all high-confidence detections (alerts) for today."""
    try:
        username = session["user"]
        start, end = get_today_start_end()
        confidence_threshold = request.args.get("threshold", 0.75, type=float)
        
        DISEASE_NAMES = {
            0: "Coccidiosis", 1: "Healthy", 2: "Newcastle Disease",
            3: "Salmonella", 4: "Fowl Pox", 5: "Aspergillosis",
            6: "Infectious Bursal", 7: "Gumboro", 8: "Marek's Disease"
        }
        
        # Get high-confidence results
        alerts = list(results_col.find({
            "username": username,
            "created_at": {"$gte": start, "$lte": end},
            "confidence": {"$gte": confidence_threshold}
        }).sort("created_at", -1))
        
        formatted_alerts = []
        for alert in alerts:
            formatted_alerts.append({
                "disease": DISEASE_NAMES.get(alert.get("disease_id", 1), "Unknown"),
                "confidence": round(alert.get("confidence", 0) * 100, 1),
                "timestamp": alert.get("created_at").isoformat(),
                "severity": "high" if alert.get("confidence", 0) > 0.85 else "medium"
            })
        
        print(f"🚨 {len(formatted_alerts)} alerts for {username} (threshold: {confidence_threshold})")
        return jsonify(formatted_alerts), 200
        
    except Exception as e:
        print(f"⚠️ Error getting alerts: {e}")
        return jsonify([]), 500


@app.route("/api/dashboard-overview", methods=["GET"])
@login_required
def dashboard_overview():
    """Get complete dashboard overview in one call."""
    try:
        username = session["user"]
        start, end = get_today_start_end()
        
        DISEASE_NAMES = {
            0: "Coccidiosis", 1: "Healthy", 2: "Newcastle Disease",
            3: "Salmonella", 4: "Fowl Pox", 5: "Aspergillosis",
            6: "Infectious Bursal", 7: "Gumboro", 8: "Marek's Disease"
        }
        
        SEVERE_DISEASES = {0, 2, 3, 4, 8}
        WARNING_DISEASES = {5, 6, 7}
        HEALTHY_DISEASES = {1}
        
        # Get all results for today
        results_today = list(results_col.find({
            "username": username,
            "created_at": {"$gte": start, "$lte": end}
        }).sort("created_at", -1))
        
        # Calculate stats
        total_detections = len(results_today)
        avg_confidence = sum(r.get("confidence", 0) for r in results_today) / max(1, total_detections)
        
        healthy_count = sum(1 for r in results_today if r.get("disease_id", 1) in HEALTHY_DISEASES)
        warning_count = sum(1 for r in results_today if r.get("disease_id") in WARNING_DISEASES)
        critical_count = sum(1 for r in results_today if r.get("disease_id") in SEVERE_DISEASES)
        alerts = sum(1 for r in results_today if r.get("confidence", 0) > 0.8 and r.get("disease_id", 0) in SEVERE_DISEASES)
        
        # Disease breakdown
        disease_counts = {}
        for r in results_today:
            did = r.get("disease_id", 1)
            disease_counts[did] = disease_counts.get(did, 0) + 1
        
        session_duration_obj = (results_today[-1]["created_at"] - results_today[0]["created_at"]) if len(results_today) > 1 else None
        duration_str = "00:00"
        if session_duration_obj:
            minutes = int(session_duration_obj.total_seconds() / 60)
            seconds = int(session_duration_obj.total_seconds() % 60)
            duration_str = f"{minutes:02d}:{seconds:02d}"
        
        overview = {
            "summary": {
                "detections": total_detections,
                "avg_confidence": round(avg_confidence * 100, 1),
                "session_duration": duration_str,
                "alerts": alerts,
                "healthy_birds": healthy_count,
                "warnings": warning_count,
                "critical_issues": critical_count,
                "analysis_rate": round((total_detections / max(1, total_detections)) * 100, 1)
            },
            "disease_breakdown": [
                {"disease_id": did, "name": DISEASE_NAMES.get(did, "Unknown"), "count": count}
                for did, count in sorted(disease_counts.items())
            ],
            "recent_detections": [
                {
                    "disease": DISEASE_NAMES.get(r.get("disease_id", 1), "Unknown"),
                    "confidence": round(r.get("confidence", 0) * 100, 1),
                    "timestamp": r.get("created_at").isoformat()
                }
                for r in results_today[:10]
            ]
        }
        
        return jsonify(overview), 200
        
    except Exception as e:
        print(f"⚠️ Error getting dashboard overview: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            "summary": {
                "detections": 0,
                "avg_confidence": 0,
                "session_duration": "00:00",
                "alerts": 0,
                "healthy_birds": 0,
                "warnings": 0,
                "critical_issues": 0,
                "analysis_rate": 0
            },
            "disease_breakdown": [],
            "recent_detections": []
        }), 500

@app.errorhandler(404)
def not_found(error):
    """Handle 404 errors."""
    return jsonify({"status": "error", "message": "Endpoint not found"}), 404


@app.errorhandler(500)
def internal_error(error):
    """Handle 500 errors."""
    print(f"❌ Internal server error: {error}")
    return jsonify({"status": "error", "message": "Internal server error"}), 500


@socketio.on("connect")
def handle_connect():
    """Handle new client connection."""
    print("🔌 Client connected")
    socketio.emit("connection_response", {"data": "Connected to BioDrone server"})


@socketio.on("disconnect")
def handle_disconnect():
    """Handle client disconnection."""
    print("🔌 Client disconnected")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"🚀 Starting Kgosi BioDrone server on 0.0.0.0:{port}")
    print(f"💳 Payment Configuration:")
    print(f"   - Download: R{DOWNLOAD_COST_ZAR}.00 ZAR per PDF")
    print(f"   - Subscription: R{SUBSCRIPTION_COST_ZAR}.00 ZAR per month")
    print(f"   - Free Tier: {FREE_TIER_SCANS_PER_DAY} scans & {FREE_TIER_DOWNLOADS_PER_DAY} downloads/day")
    print(f"   - References: AUTO-GENERATED by Paystack")
    try:
        socketio.run(app, host="0.0.0.0", port=port, debug=False)
    except KeyboardInterrupt:
        print("\n🛑 Server interrupted by user")
        cleanup_resources()
    except Exception as e:
        print(f"❌ Server error: {e}")
        cleanup_resources()
        raise