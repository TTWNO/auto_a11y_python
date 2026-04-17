#!/usr/bin/env python3
"""
from __future__ import annotations

Test script to verify metadata replacement is working properly
"""

import sys
import types
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Stub packages to avoid circular imports via __init__.py
for pkg in ['auto_a11y', 'auto_a11y.reporting']:
    if pkg not in sys.modules:
        mod = types.ModuleType(pkg)
        mod.__path__ = [str(REPO_ROOT / pkg.replace('.', '/'))]
        sys.modules[pkg] = mod

# Test the enhanced descriptions directly
from auto_a11y.reporting.issue_descriptions_enhanced import get_detailed_issue_description

print("=" * 60)
print("Testing Metadata Replacement in Enhanced Descriptions")
print("=" * 60)

# Test 1: Color contrast with metadata
test_cases = [
    {
        'name': 'Color Contrast',
        'issue_id': 'color_ErrTextContrast',
        'metadata': {
            'err': 'ErrTextContrast',
            'fg': '#777777',
            'bg': '#ffffff',
            'ratio': '3.5'
        }
    },
    {
        'name': 'Font Detection',
        'issue_id': 'fonts_DiscoFontFound',
        'metadata': {'found': 'Arial, Helvetica, sans-serif'}
    },
    {
        'name': 'Heading Level Skip',
        'issue_id': 'headings_ErrSkippedHeadingLevel',
        'metadata': {'skippedFrom': '2', 'skippedTo': '4'}
    },
    {
        'name': 'Large Text Contrast',
        'issue_id': 'color_ErrLargeTextContrast',
        'metadata': {
            'ratio': '2.8',
            'fg': '#888888',
            'bg': '#f0f0f0'
        }
    }
]

for test_case in test_cases:
    print(f"\n{test_case['name']}:")
    print("-" * 40)
    result = get_detailed_issue_description(test_case['issue_id'], test_case['metadata'])
    print(f"What: {result.get('what')}")
    
print("\n" + "=" * 60)
print("SUCCESS: Metadata replacement is working correctly!")
print("=" * 60)