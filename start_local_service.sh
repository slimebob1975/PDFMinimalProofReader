#!/usr/bin/env bash

set -e

# ===================== CONFIG (edit if needed) ===============================

# Temporary folder for the virtual environment
TEMP_DIR="/tmp"

# Project folder = folder where this script is located
DEV_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Base Python interpreter
#
# Common alternatives:
#   /usr/bin/python3
#   /opt/homebrew/bin/python3
#   /usr/local/bin/python3
#
BASE_PYTHON="/opt/homebrew/bin/python3"

# Virtual environment
VENV_NAME="pdfminimalproofreader_venv"
VENV_DIR="${TEMP_DIR}/${VENV_NAME}"

# Executables inside the venv
PY="${VENV_DIR}/bin/python"
ACTIVATE="${VENV_DIR}/bin/activate"

# Requirements file
REQUIREMENTS="${DEV_ROOT}/requirements.txt"

# FastAPI application
APP="app.main:app"
PORT=8060

# ===================== END CONFIG ===========================================


function fail() {
    echo
    echo "ERROR"
    echo "-----"
    echo "$1"
    echo
    exit 1
}


echo
echo "PDF Minimal ProofReader"
echo "======================="
echo


# ---------------------------------------------------------------------------
# Basic checks
# ---------------------------------------------------------------------------

if [ ! -x "${BASE_PYTHON}" ]; then
    fail "Python interpreter not found or not executable: ${BASE_PYTHON}"
fi

if [ ! -d "${DEV_ROOT}" ]; then
    fail "Project directory not found: ${DEV_ROOT}"
fi

if [ ! -f "${REQUIREMENTS}" ]; then
    fail "requirements.txt not found: ${REQUIREMENTS}"
fi


# ---------------------------------------------------------------------------
# Create virtual environment if needed
#
# We create it without pip first, then bootstrap pip explicitly.
# ---------------------------------------------------------------------------

if [ ! -x "${PY}" ]; then

    echo "Creating virtual environment:"
    echo "  ${VENV_DIR}"
    echo

    "${BASE_PYTHON}" -m venv --without-pip "${VENV_DIR}"

    echo "Installing pip in virtual environment..."
    echo

    "${PY}" -m ensurepip --default-pip

else

    echo "Using existing virtual environment:"
    echo "  ${VENV_DIR}"
    echo

fi


# ---------------------------------------------------------------------------
# Activate virtual environment
# ---------------------------------------------------------------------------

if [ ! -f "${ACTIVATE}" ]; then
    fail "Virtual environment activation script not found: ${ACTIVATE}"
fi

# shellcheck disable=SC1090
source "${ACTIVATE}"


# ---------------------------------------------------------------------------
# Show environment information
# ---------------------------------------------------------------------------

echo "Python environment:"
"${PY}" --version
"${PY}" -m pip --version
echo


# ---------------------------------------------------------------------------
# Update repository
# ---------------------------------------------------------------------------

echo "Updating Git repository..."
echo

cd "${DEV_ROOT}"
git pull


# ---------------------------------------------------------------------------
# Install/update project dependencies
# ---------------------------------------------------------------------------

echo
echo "Installing Python requirements..."
echo

"${PY}" -m pip install -r "${REQUIREMENTS}"


# ---------------------------------------------------------------------------
# Verify FastAPI application import
# ---------------------------------------------------------------------------

echo
echo "Checking FastAPI application..."
echo

"${PY}" -c "from app.main import app; print('FastAPI application imported successfully.')"


# ---------------------------------------------------------------------------
# Start service
# ---------------------------------------------------------------------------

echo
echo "Starting PDF Minimal ProofReader..."
echo
echo "URL: http://127.0.0.1:${PORT}"
echo
echo "Press Ctrl+C to stop the service."
echo

exec "${PY}" -m uvicorn \
    "${APP}" \
    --reload \
    --host 127.0.0.1 \
    --port "${PORT}" \
    --log-level debug