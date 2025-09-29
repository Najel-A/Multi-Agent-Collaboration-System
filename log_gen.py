#!/usr/bin/env python3
import os
import time
import random
from datetime import datetime

# Path to the log file inside your sample-logs folder
LOG_DIR = "sample-logs"
LOG_FILE = os.path.join(LOG_DIR, "demo.log")

# Make sure folder exists
os.makedirs(LOG_DIR, exist_ok=True)

levels = ["INFO", "WARN", "ERROR", "DEBUG"]
messages = [
    "User login successful",
    "User login failed",
    "Order placed successfully",
    "Payment failed due to timeout",
    "Cache miss",
    "Cache refreshed",
    "Database connection established",
    "Database query failed",
]

def generate_log():
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    level = random.choice(levels)
    msg = random.choice(messages)
    return f"{ts} [{level}] {msg}"

if __name__ == "__main__":
    print(f"Writing logs to {LOG_FILE}... (Ctrl+C to stop)")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        while True:
            line = generate_log()
            f.write(line + "\n")
            f.flush()  # force write so Filebeat picks it up quickly
            print(line)  # also show in console
            time.sleep(random.uniform(0.5, 2))  # 0.5–2s between logs
