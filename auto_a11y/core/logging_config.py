"""
Logging configuration for Auto A11y
"""

import logging
import logging.config
import os
from pathlib import Path

# Get log level from environment or default to WARNING for production
LOG_LEVEL = os.getenv('LOG_LEVEL', 'WARNING').upper()
DEBUG_MODE = os.getenv('DEBUG', 'False').lower() == 'true'

# If in debug mode, show more logs
if DEBUG_MODE:
    LOG_LEVEL = 'INFO'

# Resolve log directory — in desktop mode, use USER_DATA_DIR (writable);
# otherwise use the project-relative logs/ directory.
_user_data_dir = os.getenv('USER_DATA_DIR', '')
if os.getenv('DESKTOP_MODE', 'False').lower() == 'true' and _user_data_dir:
    LOGS_DIR = Path(_user_data_dir) / 'logs'
else:
    LOGS_DIR = Path(__file__).parent.parent.parent / 'logs'
LOGS_DIR.mkdir(exist_ok=True, parents=True)

LOGGING_CONFIG = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'standard': {
            'format': '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            'datefmt': '%Y-%m-%d %H:%M:%S'
        },
        'simple': {
            'format': '%(levelname)s - %(message)s'
        }
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'level': LOG_LEVEL,
            'formatter': 'simple',
            'stream': 'ext://sys.stdout'
        },
        'file': {
            'class': 'logging.handlers.RotatingFileHandler',
            'level': 'INFO',  # Always log INFO and above to file
            'formatter': 'standard',
            'filename': str(LOGS_DIR / 'auto_a11y.log'),
            'maxBytes': 10485760,  # 10MB
            'backupCount': 5
        }
    },
    'loggers': {
        '': {  # Root logger
            'level': LOG_LEVEL,
            'handlers': ['console', 'file']
        },
        'auto_a11y': {
            'level': LOG_LEVEL,
            'handlers': ['console', 'file'],
            'propagate': False
        },
        # Silence verbose third-party libraries
        'playwright': {
            'level': 'WARNING',
            'handlers': ['console', 'file'],
            'propagate': False
        },
        'websockets': {
            'level': 'WARNING',
            'handlers': ['file'],
            'propagate': False
        },
        'urllib3': {
            'level': 'WARNING',
            'handlers': ['file'],
            'propagate': False
        },
        'werkzeug': {
            'level': 'WARNING',
            'handlers': ['console', 'file'],
            'propagate': False
        },
        'httpx': {
            'level': 'WARNING',
            'handlers': ['file'],
            'propagate': False
        }
    }
}

def setup_logging():
    """Configure logging for the application"""
    logging.config.dictConfig(LOGGING_CONFIG)
    
    # Set specific loggers to reduce noise
    logging.getLogger('playwright').setLevel(logging.WARNING)
    logging.getLogger('playwright._impl').setLevel(logging.WARNING)
    logging.getLogger('websockets.client').setLevel(logging.WARNING)
    logging.getLogger('websockets.protocol').setLevel(logging.WARNING)
    
    # Log configuration info
    logger = logging.getLogger(__name__)
    if DEBUG_MODE:
        logger.info(f"Logging configured - Level: {LOG_LEVEL}, Debug: {DEBUG_MODE}")
    
    return logger


def reconfigure_log_path(log_dir):
    """Redirect log file to a different directory (for desktop mode).
    Call after setup_logging() has been called."""
    log_dir = Path(log_dir)
    log_dir.mkdir(exist_ok=True, parents=True)
    new_log_path = str(log_dir / 'auto_a11y.log')

    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        if isinstance(handler, logging.handlers.RotatingFileHandler):
            handler.close()
            handler.baseFilename = new_log_path
            handler.stream = handler._open()
            break