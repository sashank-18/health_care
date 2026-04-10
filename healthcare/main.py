"""
Healthcare Patient Monitoring System - Backend (Flask)
IAM Role-based (No credentials in code)
"""

import os
import uuid
import boto3
import logging
from datetime import datetime
from flask import Flask, request, jsonify, render_template
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Key, Attr

# ─── Logging ─────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── Flask ───────────────────────────────────────────
app = Flask(__name__, template_folder='.')

# ─── ENV ─────────────────────────────────────────────
SNS_TOPIC_ARN = os.getenv("SNS_TOPIC_ARN")

HEALTH_LOGS_TABLE = "patient_health_logs"
ALERTS_TABLE = "alerts"

# ─── AWS Clients (IAM role auto) ─────────────────────
def get_dynamodb():
    return boto3.resource("dynamodb")

def get_sns():
    return boto3.client("sns")

# ─── Business Logic ──────────────────────────────────
def evaluate_vitals(hr, o2):
    return "critical" if hr > 110 or o2 < 90 else "normal"

def send_sns_alert(patient_id, hr, o2):
    if not SNS_TOPIC_ARN:
        return

    message = f"""
🚨 Critical Alert
Patient: {patient_id}
Heart Rate: {hr}
Oxygen: {o2}
Time: {datetime.utcnow()}
"""

    try:
        get_sns().publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject="Critical Health Alert",
            Message=message
        )
    except Exception as e:
        logger.error(e)

def store_alert(patient_id, message):
    try:
        table = get_dynamodb().Table(ALERTS_TABLE)
        table.put_item(Item={
            "alert_id": str(uuid.uuid4()),
            "patient_id": patient_id,
            "message": message,
            "timestamp": datetime.utcnow().isoformat()
        })
    except Exception as e:
        logger.error(e)

# ─── Routes (Pages) ─────────────────────────────────
@app.route("/")
def home():
    return render_template("index.html")

@app.route("/dashboard")
def dashboard():
    return render_template("dashboard.html")

@app.route("/patient")
def patient():
    return render_template("patient.html")

@app.route("/login")
def login():
    return render_template("login.html")

@app.route("/register")
def register():
    return render_template("register.html")

# ─── Submit Vitals ──────────────────────────────────
@app.route("/submit-data", methods=["POST"])
def submit_data():
    try:
        data = request.get_json() or request.form

        patient_id = data.get("patient_id")
        hr = float(data.get("heart_rate"))
        o2 = float(data.get("oxygen_level"))
        temp = data.get("temperature")

        timestamp = datetime.utcnow().isoformat()
        status = evaluate_vitals(hr, o2)

        item = {
            "patient_id": patient_id,
            "timestamp": timestamp,
            "heart_rate": str(hr),
            "oxygen_level": str(o2),
            "status": status
        }

        if temp:
            item["temperature"] = str(temp)

        get_dynamodb().Table(HEALTH_LOGS_TABLE).put_item(Item=item)

        if status == "critical":
            msg = f"Critical Alert: {patient_id} HR:{hr} O2:{o2}"
            send_sns_alert(patient_id, hr, o2)
            store_alert(patient_id, msg)

        return jsonify({"status": status})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ─── Dashboard API (🔥 NEW - IMPORTANT) ─────────────
@app.route("/dashboard-data")
def dashboard_data():
    patient_id = request.args.get("patient_id")

    if not patient_id:
        return jsonify({"error": "patient_id required"}), 400

    try:
        db = get_dynamodb()

        # ── Fetch Records ──
        table = db.Table(HEALTH_LOGS_TABLE)
        response = table.query(
            KeyConditionExpression=Key("patient_id").eq(patient_id),
            ScanIndexForward=False
        )
        records = response.get("Items", [])

        # ── Fetch Alerts ──
        alert_table = db.Table(ALERTS_TABLE)
        alert_resp = alert_table.scan(
            FilterExpression=Attr("patient_id").eq(patient_id)
        )
        alerts = alert_resp.get("Items", [])

        # ── Stats Calculation ──
        total = len(records)
        normal = 0
        critical = 0
        total_hr = 0
        total_o2 = 0

        for r in records:
            hr = float(r["heart_rate"])
            o2 = float(r["oxygen_level"])

            total_hr += hr
            total_o2 += o2

            if r["status"] == "critical":
                critical += 1
            else:
                normal += 1

        avg_hr = round(total_hr / total, 2) if total else 0
        avg_o2 = round(total_o2 / total, 2) if total else 0

        return jsonify({
            "records": records,
            "alerts": alerts,
            "stats": {
                "total": total,
                "normal": normal,
                "critical": critical,
                "alerts": len(alerts),
                "avg_hr": avg_hr,
                "avg_o2": avg_o2
            }
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ─── Run ────────────────────────────────────────────
if __name__ == "__main__":
    app.run(debug=True)
