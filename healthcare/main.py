"""
Healthcare Patient Monitoring System - Backend (Flask)
Fully IAM Role-based (NO credentials, NO region hardcoding)
"""

import os
import uuid
import boto3
import logging
from datetime import datetime
from flask import Flask, request, jsonify, render_template
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Key, Attr

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── Flask App ────────────────────────────────────────────────────────────────
# Set template_folder to '.' so Flask looks for HTML files in the same directory
app = Flask(__name__, template_folder='.')

# ─── ENV VARIABLES (IAM + .env) ───────────────────────────────────────────────
# Correctly fetch the SNS topic from environment variable, fallback to your provided ARN
SNS_TOPIC_ARN = os.getenv("SNS_TOPIC_ARN", "arn:aws:sns:ap-south-1:152655458564:health_care")

if not SNS_TOPIC_ARN:
    logger.warning("⚠️ SNS_TOPIC_ARN not set in environment")

# ─── Tables ───────────────────────────────────────────────────────────────────
HEALTH_LOGS_TABLE = "patient_health_logs"
ALERTS_TABLE = "alerts"

# ─── AWS Clients (IAM ROLE AUTO) ──────────────────────────────────────────────
# boto3 automatically uses:
# - EC2 IAM role attached to the instance
# - AWS_DEFAULT_REGION from environment

def get_dynamodb():
    return boto3.resource("dynamodb")

def get_sns():
    return boto3.client("sns")

# ─── Ensure Tables Exist (SAFE VERSION) ───────────────────────────────────────
def ensure_tables_exist():
    try:
        client = boto3.client("dynamodb")
        existing_tables = client.list_tables()["TableNames"]

        if HEALTH_LOGS_TABLE not in existing_tables:
            logger.warning(f"{HEALTH_LOGS_TABLE} not found. Skipping creation (IAM may restrict).")

        if ALERTS_TABLE not in existing_tables:
            logger.warning(f"{ALERTS_TABLE} not found. Skipping creation (IAM may restrict).")

    except Exception as e:
        logger.error(f"Table check failed: {e}")

# ─── Business Logic ───────────────────────────────────────────────────────────
def evaluate_vitals(heart_rate, oxygen_level):
    return "critical" if heart_rate > 110 or oxygen_level < 90 else "normal"


def send_sns_alert(patient_id, heart_rate, oxygen_level):
    if not SNS_TOPIC_ARN:
        logger.warning("SNS_TOPIC_ARN missing → skipping alert")
        return

    message = (
        f"🚨 Critical Alert: Patient {patient_id}\n"
        f"Heart Rate: {heart_rate} bpm\n"
        f"Oxygen Level: {oxygen_level}%\n"
        f"Time: {datetime.utcnow().isoformat()}Z"
    )

    try:
        sns = get_sns()
        sns.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject=f"Critical Alert: Patient {patient_id}",
            Message=message,
        )
        logger.info(f"SNS alert sent for {patient_id}")
    except ClientError as e:
        logger.error(f"SNS error: {e}")


def store_alert(patient_id, message):
    try:
        table = get_dynamodb().Table(ALERTS_TABLE)
        table.put_item(Item={
            "alert_id": str(uuid.uuid4()),
            "patient_id": patient_id,
            "message": message,
            "timestamp": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        })
    except ClientError as e:
        logger.error(f"Alert store error: {e}")

# ─── Startup ──────────────────────────────────────────────────────────────────
@app.before_first_request
def startup():
    ensure_tables_exist()
    logger.info("App started with IAM role authentication")

# ─── Routes (Pages) ───────────────────────────────────────────────────────────
@app.route("/")
def home():
    return render_template("index.html")

@app.route("/patient")
def patient_page():
    return render_template("patient.html")

@app.route("/dashboard")
def dashboard():
    return render_template("dashboard.html")

@app.route("/login")
def login_page():
    return render_template("login.html")

@app.route("/register")
def register_page():
    return render_template("register.html")


# ─── API: Submit Data ─────────────────────────────────────────────────────────
@app.route("/submit-data", methods=["POST"])
def submit_data():
    try:
        data = request.get_json() or request.form

        patient_id = data.get("patient_id")
        heart_rate = float(data.get("heart_rate"))
        oxygen_level = float(data.get("oxygen_level"))
        temperature = data.get("temperature") # Handle optional temperature from patient.html

        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        status = evaluate_vitals(heart_rate, oxygen_level)

        table = get_dynamodb().Table(HEALTH_LOGS_TABLE)

        item = {
            "patient_id": patient_id,
            "timestamp": timestamp,
            "heart_rate": str(heart_rate),
            "oxygen_level": str(oxygen_level),
            "status": status,
        }
        
        # Only add temperature if it was submitted
        if temperature:
            item["temperature"] = str(temperature)

        table.put_item(Item=item)

        if status == "critical":
            msg = f"Critical Alert: Patient {patient_id} - HR: {heart_rate}, O2: {oxygen_level}%"
            send_sns_alert(patient_id, heart_rate, oxygen_level)
            store_alert(patient_id, msg)

        return jsonify({
            "status": status,
            "patient_id": patient_id,
            "timestamp": timestamp,
            "message": "Stored successfully"
        })

    except Exception as e:
        logger.error(f"Submit error: {e}")
        return jsonify({"error": str(e)}), 500


# ─── API: Get Patient Data ────────────────────────────────────────────────────
@app.route("/patient-data")
def get_patient_data():
    patient_id = request.args.get("patient_id")
    if not patient_id:
        return jsonify({"error": "Missing patient_id"}), 400

    try:
        table = get_dynamodb().Table(HEALTH_LOGS_TABLE)

        # NOTE: This assumes 'timestamp' is your Sort Key. 
        # If your table ONLY has a Partition Key, remove the ScanIndexForward=False line.
        response = table.query(
            KeyConditionExpression=Key("patient_id").eq(patient_id),
            ScanIndexForward=False 
        )

        items = response.get("Items", [])
        
        # Wrapped to match dashboard.html expectations
        return jsonify({
            "records": items,
            "count": len(items)
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── API: Get Alerts ──────────────────────────────────────────────────────────
@app.route("/alerts")
def get_alerts():
    patient_id = request.args.get("patient_id")
    if not patient_id:
        return jsonify({"error": "Missing patient_id"}), 400

    try:
        table = get_dynamodb().Table(ALERTS_TABLE)

        response = table.scan(
            FilterExpression=Attr("patient_id").eq(patient_id)
        )

        items = response.get("Items", [])
        
        # Wrapped to match dashboard.html expectations
        return jsonify({
            "alerts": items,
            "count": len(items)
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── Health ───────────────────────────────────────────────────────────────────
@app.route("/health")
def health():
    return jsonify({"status": "healthy"})


# ─── Run ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
