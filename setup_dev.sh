#!/bin/bash
# Exit on any error
set -e

# 1. Create a virtual environment
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# 2. Activate the virtual environment
source venv/bin/activate

# 3. Install django-cotton in editable mode and other dependencies
echo "Installing dependencies..."
pip install -e '.[test]'

# 4. Install wove
echo "Installing wove..."
pip install wove

echo "Development environment setup complete."
echo "To activate the virtual environment, run: source venv/bin/activate"
