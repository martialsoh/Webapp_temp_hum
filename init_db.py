import sqlite3

conn = sqlite3.connect("temperature_log.db")
c = conn.cursor()

# Enable FK constraints
c.execute("PRAGMA foreign_keys = ON")

# Units table
c.execute("""
CREATE TABLE IF NOT EXISTS units (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    dht_pin TEXT NOT NULL,
    fan_pin INTEGER NOT NULL,
    active INTEGER DEFAULT 1
)
""")

# Logs table
c.execute("""
CREATE TABLE IF NOT EXISTS temperature_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    unit_id INTEGER,
    temperature REAL,
    humidity REAL,
    fan_status INTEGER,
    FOREIGN KEY (unit_id) REFERENCES units(id) ON DELETE SET NULL
)
""")

# Emails table (match backend!)
c.execute("""
CREATE TABLE IF NOT EXISTS email_recipients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL
)
""")

# Settings table
c.execute("""
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
)
""")

# Default specs
defaults = {"temp_spec_min": "10", "temp_spec_max": "40"}
for k,v in defaults.items():
    c.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", (k,v))

conn.commit()
conn.close()
print("Database initialized successfully!")
