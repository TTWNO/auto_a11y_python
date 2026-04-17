"""
Testing engine for accessibility tests
"""

from __future__ import annotations

from .test_runner import TestRunner
from .script_injector import ScriptInjector
from .result_processor import ResultProcessor

__all__ = [
    'TestRunner',
    'ScriptInjector',
    'ResultProcessor'
]