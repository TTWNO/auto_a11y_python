#!/usr/bin/env python3
"""
Quick script to check if ErrButtonTextLowContrast is in the IssueCatalog
"""

import sys
import types
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Stub the package hierarchy to avoid circular imports via __init__.py
for pkg in ['auto_a11y', 'auto_a11y.reporting']:
    if pkg not in sys.modules:
        mod = types.ModuleType(pkg)
        mod.__path__ = [str(REPO_ROOT / pkg.replace('.', '/'))]
        sys.modules[pkg] = mod

from auto_a11y.reporting.issue_catalog import IssueCatalog

# Check if it exists
if 'ErrButtonTextLowContrast' in IssueCatalog.ISSUES:
    print("ERROR: ErrButtonTextLowContrast IS STILL IN IssueCatalog.ISSUES!")
    print(f"   Value: {IssueCatalog.ISSUES['ErrButtonTextLowContrast']}")
    sys.exit(1)
else:
    print("SUCCESS: ErrButtonTextLowContrast is NOT in IssueCatalog.ISSUES")

# List all button-related tests
button_tests = [k for k in IssueCatalog.ISSUES.keys() if 'Button' in k]
print(f"\nButton tests found ({len(button_tests)}):")
for test in sorted(button_tests):
    print(f"  - {test}")

sys.exit(0)
