from flask import Flask, render_template, request, redirect, jsonify, Response, stream_with_context
import sqlite3
import adafruit_dht
import board
import json
from gpiozero import OutputDevice
import time
import threading
from flask_mail import Mail, Message
import os
from dotenv import load_dotenv
from io import StringIO
import csv

# Load environment variables from .env
load_dotenv()

app = Flask(__name__)

# Database file
DATABASE = "temperature_log.db"

# Flask-Mail SMTP Configuration from environment variables
app.config['MAIL_SERVER'] = os.getenv('MAIL_SERVER')  # e.g., "smtp.gmail.com"
app.config['MAIL_PORT'] = int(os.getenv('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = os.getenv('MAIL_USE_TLS', 'True').lower() == 'true'
app.config['MAIL_USE_SSL'] = os.getenv('MAIL_USE_SSL', 'False').lower() == 'true'
app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME')  # SMTP username/email
app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD')  # SMTP password/app password
app.config['MAIL_DEFAULT_SENDER'] = os.getenv('MAIL_DEFAULT_SENDER')  # e.g., your email address

mail = Mail(app)

units = {}  # unit_id -> {"name": str, "sensor": adafruit_dht.DHT22, "fan": OutputDevice}
units_lock = threading.Lock()

############################
# GPIO + Sensor Management #
############################

def get_board_pin(pin_str):
    try:
        return getattr(board, pin_str)
    except AttributeError:
        print(f"Invalid board pin name: {pin_str}")
        return None

def load_units():
    with sqlite3.connect(DATABASE) as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, name, dht_pin, fan_pin FROM units WHERE active=1")
        results = cur.fetchall()

    new_units = {}
    for (unit_id, name, dht_pin_str, fan_pin_num) in results:
        print(f"Loading unit {unit_id}: {name} (Sensor pin: {dht_pin_str}, Fan pin: GPIO{fan_pin_num})")
        
        dht_pin = get_board_pin(dht_pin_str)
        if dht_pin is None:
            print(f"[Unit {unit_id}] Skipped: Invalid DHT pin '{dht_pin_str}'")
            continue

        try:
            sensor = adafruit_dht.DHT22(dht_pin)
        except Exception as e:
            print(f"[Unit {unit_id}] Skipped: Failed to init DHT22 - {e}")
            continue

        try:
            fan = OutputDevice(fan_pin_num)
        except Exception as e:
            print(f"[Unit {unit_id}] Skipped: Failed to init fan on GPIO{fan_pin_num} - {e}")
            continue

        new_units[unit_id] = {"name": name, "sensor": sensor, "fan": fan}
        print(f"[Unit {unit_id}] Loaded successfully")

    with units_lock:
        global units
        units = new_units


def read_sensor(unit_id):
    with units_lock:
        sensor = units[unit_id]["sensor"]
    try:
        temperature = sensor.temperature
        humidity = sensor.humidity
        return (round(temperature, 2) if temperature is not None else None,
                round(humidity, 2) if humidity is not None else None)
    except RuntimeError as e:
        print(f"DHT RuntimeError (unit {unit_id}): {e}")
        return None, None
    except Exception as e:
        print(f"Sensor read failed for unit {unit_id}: {e}")
        return None, None

def get_fan_status(unit_id):
    with units_lock:
        fan = units[unit_id]["fan"]
        return fan.value == 1

def set_fan(unit_id, turn_on):
    with units_lock:
        fan = units[unit_id]["fan"]
    if turn_on:
        fan.on()
    else:
        fan.off()

########################
# Settings & Logging   #
########################

def get_settings():
    try:
        with sqlite3.connect(DATABASE) as conn:
            cur = conn.cursor()
            cur.execute("SELECT key, value FROM settings")
            rows = cur.fetchall()
            settings = {k: v for k, v in rows}
        return {
            "temp_spec_min": float(settings.get("temp_spec_min", 10)),
            "temp_spec_max": float(settings.get("temp_spec_max", 40))
        }
    except Exception as e:
        print(f"Failed to get settings: {e}")
        return {"temp_spec_min": 10, "temp_spec_max": 40}

def log_data(unit_id, temperature, humidity, fan_on):
    try:
        with sqlite3.connect(DATABASE) as conn:
            conn.execute(
                "INSERT INTO temperature_log (unit_id, temperature, humidity, fan_status) VALUES (?, ?, ?, ?)",
                (unit_id, temperature, humidity, int(fan_on))
            )
            conn.commit()
    except Exception as e:
        print(f"Failed to log data: {e}")

##########################
# Email Alerting         #
##########################

def send_email_alert(unit_id, temperature):
    with sqlite3.connect(DATABASE) as conn:
        recipients = [row[0] for row in conn.execute("SELECT email FROM email_recipients").fetchall()]

    if not recipients:
        print(f"No email recipients to alert for unit {unit_id}")
        return

    subject = f"Temperature Alert for Unit {unit_id}"
    body = f"Alert: The temperature for unit {unit_id} is out of range: {temperature}°C."

    with app.app_context():
        for recipient in recipients:
            try:
                msg = Message(subject=subject,
                              recipients=[recipient],
                              body=body)
                mail.send(msg)
                print(f"Alert email sent to {recipient} for unit {unit_id}")
            except Exception as e:
                print(f"Failed to send email to {recipient}: {e}")

########################
# Monitoring Loop      #
########################

def monitor_loop():
    last_reload = 0
    while True:
        now = time.time()
        if now - last_reload > 60:
            load_units()
            last_reload = now

        settings = get_settings()
        min_temp = settings["temp_spec_min"]
        max_temp = settings["temp_spec_max"]

        with units_lock:
            unit_ids = list(units.keys())

        for unit_id in unit_ids:
            temp, hum = read_sensor(unit_id)
            fan_on = get_fan_status(unit_id)
            if temp is not None:
                log_data(unit_id, temp, hum, fan_on)
                if temp < min_temp or temp > max_temp:
                    send_email_alert(unit_id, temp)
            time.sleep(2)
        time.sleep(10)

########################
# Flask Routes         #
########################

@app.route("/")
def index():
    settings = get_settings()
    data = {}
    with units_lock:
        for unit_id, unit in units.items():
            temp, hum = read_sensor(unit_id)
            fan = get_fan_status(unit_id)
            data[unit_id] = {
                "name": unit["name"],
                "temperature": temp,
                "humidity": hum,
                "fan_status": "ON" if fan else "OFF"
            }
    return render_template("index_multi.html",
                           units=data,
                           temp_spec_min=settings["temp_spec_min"],
                           temp_spec_max=settings["temp_spec_max"])

@app.route("/units", methods=["GET"])
def list_units():
    with sqlite3.connect(DATABASE) as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, name, dht_pin, fan_pin FROM units WHERE active=1")
        rows = cur.fetchall()
    print(f"[DEBUG] /units returned: {rows}")
    return jsonify([{"id": r[0], "name": r[1], "dht_pin": r[2], "fan_pin": r[3]} for r in rows])

@app.route("/units/add", methods=["POST"])
def add_unit():
    name = request.form.get("name")
    dht_pin = request.form.get("dht_pin")
    fan_pin = request.form.get("fan_pin")
    if not (name and dht_pin and fan_pin):
        return jsonify({"success": False, "error": "Missing fields"}), 400
    try:
        fan_pin = int(fan_pin)
    except ValueError:
        return jsonify({"success": False, "error": "Invalid fan_pin"}), 400
    with sqlite3.connect(DATABASE) as conn:
        try:
            conn.execute("INSERT INTO units (name, dht_pin, fan_pin, active) VALUES (?, ?, ?, 1)",
                         (name, dht_pin, fan_pin))
            conn.commit()
        except sqlite3.IntegrityError:
            return jsonify({"success": False, "error": "Unit name exists"}), 400
    if not hasattr(board, dht_pin):
        return jsonify({"success": False, "error": f"Invalid DHT pin: {dht_pin}"}), 400
    # Make sure units in memory are reloaded
    print("[/units/add] Reloading units after insert...")
    load_units()
    print("[/units/add] Reload complete.")
    return jsonify({"success": True})


@app.route("/units/remove/<int:unit_id>", methods=["POST"])
def remove_unit(unit_id):
    with sqlite3.connect(DATABASE) as conn:
        conn.execute("UPDATE units SET active = 0 WHERE id=?", (unit_id,))
        conn.commit()
    load_units()
    return jsonify({"success": True})

@app.route("/data", methods=["GET"])
def get_data():
    response = {}
    with units_lock:
        for unit_id, unit in units.items():
            temp, hum = read_sensor(unit_id)
            fan = get_fan_status(unit_id)
            response[str(unit_id)] = {
                "name": unit["name"],
                "temperature": temp,
                "humidity": hum,
                "fan_status": "ON" if fan else "OFF"
            }
    return jsonify(response)

@app.route("/set_limit", methods=["POST"])
def set_limit():
    min_temp = request.form.get("temp_spec_min")
    max_temp = request.form.get("temp_spec_max")
    with sqlite3.connect(DATABASE) as conn:
        conn.execute("REPLACE INTO settings (key, value) VALUES (?, ?)", ("temp_spec_min", min_temp))
        conn.execute("REPLACE INTO settings (key, value) VALUES (?, ?)", ("temp_spec_max", max_temp))
        conn.commit()
    return redirect("/")

@app.route("/email", methods=["POST"])
def manage_email():
    email = request.form.get("email")
    action = request.form.get("action")
    if not email:
        return "Missing email", 400
    with sqlite3.connect(DATABASE) as conn:
        if action == "add":
            conn.execute("INSERT OR IGNORE INTO email_recipients (email) VALUES (?)", (email,))
        elif action == "remove":
            conn.execute("DELETE FROM email_recipients WHERE email=?", (email,))
        conn.commit()
    return redirect("/")

@app.route("/export", methods=["GET"])
def export_data():
    start = request.args.get("start_date")
    end = request.args.get("end_date")
    unit_id = request.args.get("unit_id")
    query = """SELECT timestamp, unit_id, temperature, humidity, fan_status
               FROM temperature_log WHERE date(timestamp) BETWEEN ? AND ?"""
    params = [start, end]
    if unit_id:
        query += " AND unit_id=?"
        params.append(unit_id)
    with sqlite3.connect(DATABASE) as conn:
        rows = conn.execute(query, params).fetchall()
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["timestamp", "unit_id", "temperature", "humidity", "fan_status"])
    writer.writerows(rows)
    return Response(output.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=temperature_log.csv"})
                    
from flask import Response, stream_with_context
import json

@app.route("/events")
def sse_stream():
    def event_stream():
        while True:
            try:
                with units_lock:
                    snapshot = {}
                    for unit_id, unit in units.items():
                        temp, hum = read_sensor(unit_id)  # call once
                        snapshot[unit_id] = {
                            "name": unit["name"],
                            "temperature": temp,
                            "humidity": hum,
                            "fan_status": "ON" if get_fan_status(unit_id) else "OFF"
                        }
                yield f"data: {json.dumps(snapshot)}\n\n"
            except Exception as e:
                print(f"[SSE] Stream error: {e}")
                yield "data: {}\n\n"
                time.sleep(2)
                continue
            time.sleep(5)

    return Response(stream_with_context(event_stream()), mimetype="text/event-stream")

########################
# Main Run             #
########################

if __name__ == "__main__":
    load_units()
    monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
    monitor_thread.start()
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
