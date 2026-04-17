#!/usr/bin/env python3
"""Check Documents touchpoint fixture status"""
from __future__ import annotations

from typing import Any

import sys
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure
import os

MONGO_URI = os.environ.get('MONGODB_URI', 'mongodb://localhost:27017/')
DB_NAME = os.environ.get('MONGODB_DATABASE', 'auto_a11y')

try:
    client: MongoClient[dict[str, Any]] = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
    client.admin.command('ping')
except (ConnectionFailure, Exception) as e:
    print(f"SKIP: MongoDB not available ({e})")
    sys.exit(0)

db = client[DB_NAME]

print("=" * 70)
print("DOCUMENTS TOUCHPOINT FIXTURES")
print("=" * 70)

# Document-related error codes
doc_codes = [
    'DiscoPDFLinksFound',
    'ErrDocumentLinkMissingFileType',
    'ErrDocumentLinkWrongLanguage',
    'ErrMissingDocumentType',
    'WarnGenericDocumentLinkText',
    'WarnMissingDocumentMetadata'
]

print('\nFixture counts by error code:')
for code in doc_codes:
    count = db.fixture_tests.count_documents({'expected_code': code})
    print(f'  {code}: {count} fixtures')
    if count > 0:
        # Show sample paths
        samples = list(db.fixture_tests.find({'expected_code': code}, {'fixture_path': 1}).limit(2))
        for s in samples:
            print(f'    - {s.get("fixture_path")}')

print('\n' + '=' * 70)
print('FIXTURES IN Documents/ DIRECTORY')
print('=' * 70)

docs_dir_fixtures = list(db.fixture_tests.find({'fixture_path': {'$regex': '^Documents/'}}))
print(f'\nFound {len(docs_dir_fixtures)} fixtures in Documents/ directory')
for f in docs_dir_fixtures:
    print(f"  {f.get('expected_code')}: {f.get('fixture_path')}")

client.close()
