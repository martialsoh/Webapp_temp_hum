#!/bin/bash
set -e

echo "Installing system dependencies..."
sudo apt-get update
sudo apt-get install -y build-essential python3-dev python3-pip python3-setuptools libgpiod2

echo "Creating virtual environment..."
python3 -m venv venv --system-site-packages

echo "Activating virtual environment..."
source venv/bin/activate

echo "Upgrading pip..."
pip install --upgrade pip

echo "Installing Python packages from requirements.txt..."
pip install -r requirements.txt

echo "Ensuring SQLite DB exists..."
python3 init_db.py

echo "Launching Flask web application..."
python3 app.py
