#!/usr/bin/env python3
"""
ATTE Local Bridge Server
Runs on localhost:7742. Accepts commands from the Grist scanning
dashboard widget and executes local scripts.

Run with: python3 atte_bridge.py
"""

from flask import Flask, jsonify, request
from pathlib import Path
import subprocess
import os
import threading
import time

app = Flask(__name__)

# ── CONFIG ────────────────────────────────────────────────────────────────────
INCOMING_ROOT = Path.home() / "atte-workspace" / "incoming"
SYNC_ROOT     = Path("/home/piobman/Nextcloud2/At the Tail End")
SCANNER_DIR   = Path.home() / "atte-scanner"
PROCESSOR_DIR = Path.home() / "atte-processor"

# ── CORS ──────────────────────────────────────────────────────────────────────
@app.after_request
def add_cors(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return response

@app.route('/options', methods=['OPTIONS'])
def options():
    return jsonify({}), 200

# ── STATUS ────────────────────────────────────────────────────────────────────
@app.route('/health')
def health():
    return jsonify({'status': 'ok', 'service': 'ATTE Bridge'})

@app.route('/status/<item_id>')
def item_status(item_id):
    """Check what files exist for an item — incoming and Nextcloud."""
    item_id = item_id.upper()
    prefix = item_id.split('-')[0].lower()

    folder_map = {
        'eph': 'ephemera', 'game': 'gaming', 'music': 'music',
        'media': 'media', 'toy': 'toys', 'smgds': 'smallgoods'
    }
    subfolder = folder_map.get(prefix, 'ephemera')

    item_dir = INCOMING_ROOT / item_id
    pages_dir = item_dir / "pages"

    # Check incoming
    czur_pdf = list(item_dir.glob("*_czur.pdf")) if item_dir.exists() else []
    tiffs = list(pages_dir.glob("*_master.tif")) if pages_dir.exists() else []
    pagemap = item_dir / f"{item_id}_pagemap.txt" if item_dir.exists() else None

    # Check Nextcloud outputs
    masters_dir = SYNC_ROOT / "masters" / subfolder
    masters = list(masters_dir.glob(f"{item_id}-S*_master.tif")) if masters_dir.exists() else []

    derivatives_jpg = SYNC_ROOT / "derivatives" / "jpg" / subfolder
    jpgs = list(derivatives_jpg.glob(f"{item_id}-S*_web.jpg")) if derivatives_jpg.exists() else []

    research_pdfs = SYNC_ROOT / "research_pdfs"
    final_pdf = (research_pdfs / f"{item_id}_complete.pdf").exists() if research_pdfs.exists() else False

    return jsonify({
        'item_id': item_id,
        'subfolder': subfolder,
        # Incoming workspace
        'czur_pdf_ready': len(czur_pdf) > 0,
        'czur_pdf_name': czur_pdf[0].name if czur_pdf else None,
        'tiffs_in_workspace': len(tiffs),
        'tiff_names': [t.name for t in sorted(tiffs)],
        'pagemap_exists': pagemap.exists() if pagemap else False,
        'pagemap_contents': pagemap.read_text() if pagemap and pagemap.exists() else None,
        # Nextcloud outputs
        'masters_count': len(masters),
        'master_names': [m.name for m in sorted(masters)],
        'derivatives_count': len(jpgs),
        'final_pdf_ready': final_pdf,
        # Derived step status
        'step_czur': len(czur_pdf) > 0,
        'step_flatbed': len(tiffs) > 0 or len(masters) > 0,
        'step_pagemap': (pagemap.exists() if pagemap else False) or len(masters) > 0,
        'step_processed': final_pdf,
        'step_derivatives': len(jpgs) > 0,
    })

# ── ACTIONS ───────────────────────────────────────────────────────────────────
def run_in_terminal(cmd):
    """Run a command in a new terminal window so user can see output."""
    # Try different terminal emulators
    terminals = [
        ['xterm', '-e', cmd],
        ['gnome-terminal', '--', 'bash', '-c', cmd + '; read -p "Press Enter to close..."'],
        ['mate-terminal', '--', 'bash', '-c', cmd + '; read -p "Press Enter to close..."'],
        ['xfce4-terminal', '-e', cmd],
        ['konsole', '-e', cmd],
    ]
    for term in terminals:
        try:
            subprocess.Popen(term)
            return True
        except FileNotFoundError:
            continue
    # Fallback: run in background without terminal
    subprocess.Popen(['bash', '-c', cmd])
    return False

@app.route('/run/scan', methods=['POST'])
def run_scan():
    """Open scan launcher in a terminal."""
    cmd = f"bash {SCANNER_DIR}/scan.sh"
    run_in_terminal(cmd)
    return jsonify({'status': 'ok', 'message': 'Scan launcher opened'})

@app.route('/run/czur-combine', methods=['POST'])
def run_czur_combine():
    """Run CZUR combine script in a terminal."""
    cmd = f"bash {SCANNER_DIR}/czur_combine.sh"
    run_in_terminal(cmd)
    return jsonify({'status': 'ok', 'message': 'CZUR combine opened'})

@app.route('/run/pagemap', methods=['POST'])
def run_pagemap():
    """Run pagemap helper in a terminal."""
    cmd = f"bash {SCANNER_DIR}/pagemap_helper.sh"
    run_in_terminal(cmd)
    return jsonify({'status': 'ok', 'message': 'Pagemap helper opened'})

@app.route('/run/process', methods=['POST'])
def run_process():
    """Check processor status."""
    # Check if processor is running
    result = subprocess.run(['pgrep', '-f', 'atte_processor'], capture_output=True, text=True)
    running = result.returncode == 0

    if not running:
        # Start processor in background
        subprocess.Popen(['python3', str(PROCESSOR_DIR / 'atte_processor.py')])
        return jsonify({'status': 'ok', 'message': 'Processor started'})
    else:
        return jsonify({'status': 'ok', 'message': 'Processor already running'})

@app.route('/processor/status')
def processor_status():
    """Check if processor is running."""
    result = subprocess.run(['pgrep', '-f', 'atte_processor'], capture_output=True, text=True)
    running = result.returncode == 0

    # Get last few lines of log
    log_file = SYNC_ROOT / "processing.log"
    recent_log = []
    if log_file.exists():
        with open(log_file) as f:
            lines = f.readlines()
            recent_log = [l.strip() for l in lines[-8:] if l.strip()]

    return jsonify({
        'running': running,
        'recent_log': recent_log
    })

@app.route('/run/open-pdf', methods=['POST'])
def open_pdf():
    """Open the completed PDF for an item."""
    data = request.json or {}
    item_id = data.get('item_id', '').upper()
    pdf_path = SYNC_ROOT / "research_pdfs" / f"{item_id}_complete.pdf"

    if pdf_path.exists():
        subprocess.Popen(['xdg-open', str(pdf_path)])
        return jsonify({'status': 'ok', 'message': f'Opening {pdf_path.name}'})
    else:
        return jsonify({'status': 'error', 'message': 'PDF not found'}), 404

if __name__ == '__main__':
    print("")
    print("╔══════════════════════════════════════════╗")
    print("║     ATTE Bridge Server                   ║")
    print("║     Running on localhost:7742            ║")
    print("╚══════════════════════════════════════════╝")
    print("")
    print("Keep this running while using the Grist widget.")
    print("Press Ctrl+C to stop.")
    print("")
    app.run(host='127.0.0.1', port=7742, debug=False)
