"""
Desktop-mode routes for Electron integration.
This blueprint is only registered when DESKTOP_MODE=True.
"""
from __future__ import annotations

import os
from flask import Blueprint, Response, jsonify, request, current_app

desktop_bp = Blueprint('desktop', __name__)


@desktop_bp.route('/shutdown', methods=['POST'])
def shutdown() -> Response | tuple[Response, int]:
    """
    Graceful Flask shutdown for Electron.
    Only available from localhost.
    Electron calls this during app quit for cross-platform shutdown
    (SIGTERM does not work on Windows).
    """
    if request.remote_addr not in ('127.0.0.1', '::1'):
        return jsonify({'error': 'Localhost only'}), 403

    # Respond before exiting so Electron gets confirmation
    response = jsonify({'status': 'shutting_down'})

    # Use os._exit(0) because sys.exit() only raises SystemExit which Flask catches.
    # os._exit(0) skips atexit handlers (like APScheduler shutdown), but this is
    # acceptable because Electron is the one managing the process lifecycle.
    import threading
    threading.Timer(0.5, lambda: os._exit(0)).start()

    return response
