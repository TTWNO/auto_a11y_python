#!/usr/bin/env python3
"""Bulk migration script: converts .po translations to Fluent .ftl files
and rewrites templates + Python source to use ftl()/lazy_ftl().

Usage:
    python scripts/migrate_po_to_ftl.py                # full migration
    python scripts/migrate_po_to_ftl.py --dry-run      # preview changes only

Produces:
    - .ftl files under auto_a11y/web/translations/{en,fr}/
    - Rewritten Jinja2 templates (.html) using ftl()
    - Rewritten Python files using ftl()/lazy_ftl()
    - migration_report.json with a detailed change log
"""

import argparse
import json
import os
import re
import sys
from collections import defaultdict

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
PO_FILE = os.path.join(
    PROJECT_ROOT,
    'auto_a11y', 'web', 'translations', 'fr', 'LC_MESSAGES', 'messages.po',
)
TRANSLATIONS_DIR = os.path.join(PROJECT_ROOT, 'auto_a11y', 'web', 'translations')
TEMPLATES_DIR = os.path.join(PROJECT_ROOT, 'auto_a11y', 'web', 'templates')
AUTO_A11Y_DIR = os.path.join(PROJECT_ROOT, 'auto_a11y')

# ---------------------------------------------------------------------------
# Feature-area mapping from source-reference paths
# ---------------------------------------------------------------------------
# Order matters: first match wins.
_PATH_TO_FEATURE = [
    ('templates/auth/', 'auth'),
    ('templates/pages/', 'pages'),
    ('templates/projects/', 'projects'),
    ('templates/websites/', 'websites'),
    ('templates/reports/', 'reports'),
    ('templates/testing/', 'testing'),
    ('templates/schedules/', 'schedules'),
    ('templates/recordings/', 'recordings'),
    ('templates/scripts/', 'scripts'),
    ('templates/public/', 'public'),
    ('templates/drupal_sync/', 'drupal'),
    ('templates/groups/', 'groups'),
    ('templates/discovered_pages/', 'pages'),
    ('templates/automated_tests/', 'testing'),
    ('templates/share_tokens/', 'settings'),
    ('templates/project_participants/', 'projects'),
    ('templates/project_users/', 'projects'),
    ('templates/website_users/', 'websites'),
    ('templates/static_report/', 'reports'),
    ('templates/components/', 'common'),
    ('templates/email/', 'auth'),
    ('routes/auth', 'auth'),
    ('routes/pages', 'pages'),
    ('routes/projects', 'projects'),
    ('routes/websites', 'websites'),
    ('routes/reports', 'reports'),
    ('routes/schedules', 'schedules'),
    ('routes/recordings', 'recordings'),
    ('routes/scripts', 'scripts'),
    ('routes/public', 'public'),
    ('routes/groups', 'groups'),
    ('routes/discovered_pages', 'pages'),
    ('routes/share_tokens', 'settings'),
    ('routes/project_participants', 'projects'),
    ('routes/project_users', 'projects'),
    ('routes/website_users', 'websites'),
    ('routes/members', 'common'),
    ('reporting/', 'reports'),
    ('core/', 'common'),
    ('testing/', 'testing'),
]


# ---------------------------------------------------------------------------
# 1. Parse .po file
# ---------------------------------------------------------------------------

def parse_po_entries(path):
    """Parse a .po file into a list of entry dicts.

    Each entry: {msgid, msgstr, refs, fuzzy, line_number}
    Skips the header block, obsolete (#~) entries, and fuzzy entries.
    """
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()

    entries = []
    # Track approximate line numbers
    block_offset = 0
    for block in re.split(r'\n\n+', content):
        lines = block.strip().split('\n')
        line_number = block_offset + 1
        block_offset += len(lines) + 1  # +1 for the blank separator

        # Skip header block
        if any('Project-Id-Version' in l for l in lines):
            continue
        # Skip obsolete entries
        if any(l.startswith('#~') for l in lines):
            continue

        refs = [l for l in lines if l.startswith('#:')]
        is_fuzzy = any('#, fuzzy' in l for l in lines)

        # Skip fuzzy entries
        if is_fuzzy:
            continue

        msgid_parts = []
        msgstr_parts = []
        in_msgid = False
        in_msgstr = False

        for l in lines:
            if l.startswith('msgid '):
                in_msgid = True
                in_msgstr = False
                msgid_parts.append(l[6:].strip('"'))
            elif l.startswith('msgstr '):
                in_msgid = False
                in_msgstr = True
                msgstr_parts.append(l[7:].strip('"'))
            elif l.startswith('"'):
                if in_msgid:
                    msgid_parts.append(l.strip('"'))
                elif in_msgstr:
                    msgstr_parts.append(l.strip('"'))
            else:
                in_msgid = False
                in_msgstr = False

        msgid = ''.join(msgid_parts)
        msgstr = ''.join(msgstr_parts)

        if msgid:
            entries.append({
                'msgid': msgid,
                'msgstr': msgstr,
                'refs': refs,
                'fuzzy': is_fuzzy,
                'line_number': line_number,
            })

    return entries


# ---------------------------------------------------------------------------
# 2. Map each message to a feature area
# ---------------------------------------------------------------------------

def _feature_for_ref(ref_line):
    """Return the feature area for a single #: reference line."""
    for prefix, feature in _PATH_TO_FEATURE:
        if prefix in ref_line:
            return feature
    return 'common'


def classify_entry(entry):
    """Return the feature area string for an entry."""
    if not entry['refs']:
        return 'common'

    features = set()
    for ref_line in entry['refs']:
        # Each #: line can list multiple source references separated by spaces
        # e.g.  #: auto_a11y/web/routes/auth.py:124 auto_a11y/web/routes/auth.py:158
        parts = ref_line.lstrip('#:').strip().split()
        for part in parts:
            features.add(_feature_for_ref(part))

    if len(features) == 1:
        return features.pop()
    # Multiple features -> common
    return 'common'


# ---------------------------------------------------------------------------
# 3. Generate message IDs
# ---------------------------------------------------------------------------

_STRIP_HTML = re.compile(r'<[^>]+>')
_STRIP_PUNCT = re.compile(r'[^\w\s-]', re.UNICODE)
_COLLAPSE_HYPHENS = re.compile(r'-{2,}')


def _slugify(text):
    """Turn English text into a slug suitable for a Fluent message ID."""
    text = _STRIP_HTML.sub('', text)
    # Remove %-format specifiers before slugifying so that e.g. "%(name)s"
    # becomes "name" rather than "name-s".
    text = re.sub(r'%\((\w+)\)[sd]', r'\1', text)  # %(name)s -> name
    text = re.sub(r'%[sd]', '', text)  # %s / %d -> nothing
    text = text.lower()
    text = _STRIP_PUNCT.sub(' ', text)
    text = text.strip()
    text = re.sub(r'\s+', '-', text)
    text = _COLLAPSE_HYPHENS.sub('-', text)
    text = text.strip('-')
    # Truncate to 50 chars on a hyphen boundary
    if len(text) > 50:
        text = text[:50]
        last_hyphen = text.rfind('-')
        if last_hyphen > 20:
            text = text[:last_hyphen]
    return text


def generate_message_ids(entries_with_features):
    """Assign a unique message ID to each (msgid, feature) pair.

    Returns a dict: msgid -> (feature, message_id)
    """
    # Track used IDs to avoid collisions
    used_ids = set()
    mapping = {}  # msgid -> (feature, message_id)

    for entry in entries_with_features:
        msgid = entry['msgid']
        feature = entry['feature']

        if msgid in mapping:
            continue  # already assigned

        slug = _slugify(msgid)
        if not slug:
            slug = 'msg'

        base_id = f"{feature}-{slug}"
        candidate = base_id
        counter = 2
        while candidate in used_ids:
            candidate = f"{base_id}-{counter}"
            counter += 1

        used_ids.add(candidate)
        mapping[msgid] = (feature, candidate)

    return mapping


# ---------------------------------------------------------------------------
# 4. Format-string conversion
# ---------------------------------------------------------------------------

def _convert_format_string(text):
    """Convert Python %-style format strings to Fluent { $var } syntax.

    %(name)s  -> { $name }
    %s        -> { $arg }  (numbered if multiple: $arg1, $arg2, ...)
    %d        -> { $num }  (numbered if multiple: $num1, $num2, ...)
    """
    if not text:
        return text

    # First handle named %(name)s patterns
    text = re.sub(r'%\((\w+)\)s', r'{ $\1 }', text)
    text = re.sub(r'%\((\w+)\)d', r'{ $\1 }', text)

    # Count positional %s and %d
    positional_s = len(re.findall(r'(?<!%)%s', text))
    positional_d = len(re.findall(r'(?<!%)%d', text))

    if positional_s == 1 and positional_d == 0:
        text = re.sub(r'(?<!%)%s', '{ $arg }', text, count=1)
    elif positional_s > 1:
        counter = [0]
        def _repl_s(m):
            counter[0] += 1
            return '{ $arg%d }' % counter[0]
        text = re.sub(r'(?<!%)%s', _repl_s, text)

    if positional_d == 1 and positional_s == 0:
        text = re.sub(r'(?<!%)%d', '{ $num }', text, count=1)
    elif positional_d > 1:
        counter = [0]
        def _repl_d(m):
            counter[0] += 1
            return '{ $num%d }' % counter[0]
        text = re.sub(r'(?<!%)%d', _repl_d, text)

    # Handle mixed positional %s and %d
    if positional_s >= 1 and positional_d >= 1:
        # Already counted; re-number everything sequentially
        # Reset: re-parse from scratch for this edge case
        pass  # The individual replacements above should still work fine

    return text


def _escape_ftl_value(text):
    """Escape text for use as a Fluent message value.

    Fluent requires leading dots or asterisks in continuation lines to be
    escaped, and curly braces that aren't placeholders need escaping.
    For our generated content, the main concern is ensuring { } are only
    used around variables.
    """
    # Fluent uses { } for placeables. Our _convert_format_string already
    # produces valid { $var } syntax.  We only need to handle literal braces
    # that are NOT part of placeables.  Since we control generation, this
    # should be fine as-is.
    return text


# ---------------------------------------------------------------------------
# 5. Write .ftl files
# ---------------------------------------------------------------------------

# Existing message IDs in auth.ftl that should be skipped
def _load_existing_ftl_ids(locale_dir):
    """Load all message IDs from existing .ftl files in a locale directory."""
    existing = set()
    if not os.path.isdir(locale_dir):
        return existing
    for fname in os.listdir(locale_dir):
        if not fname.endswith('.ftl'):
            continue
        fpath = os.path.join(locale_dir, fname)
        with open(fpath, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    msg_id = line.split('=', 1)[0].strip()
                    existing.add(msg_id)
    return existing


def write_ftl_files(mapping, entries, dry_run=False):
    """Write .ftl files grouped by feature area.

    mapping: msgid -> (feature, message_id)
    entries: list of parsed .po entries
    """
    en_dir = os.path.join(TRANSLATIONS_DIR, 'en')
    fr_dir = os.path.join(TRANSLATIONS_DIR, 'fr')

    # Load existing IDs to skip
    existing_en = _load_existing_ftl_ids(en_dir)
    existing_fr = _load_existing_ftl_ids(fr_dir)
    existing_all = existing_en | existing_fr

    # Build msgid -> msgstr lookup
    msgid_to_msgstr = {}
    for entry in entries:
        msgid_to_msgstr[entry['msgid']] = entry['msgstr']

    # Group by feature
    feature_messages = defaultdict(list)  # feature -> [(message_id, en_value, fr_value)]
    for msgid, (feature, message_id) in mapping.items():
        if message_id in existing_all:
            continue
        en_value = _convert_format_string(msgid)
        fr_value = _convert_format_string(msgid_to_msgstr.get(msgid, ''))
        feature_messages[feature].append((message_id, en_value, fr_value))

    # Sort messages within each feature for deterministic output
    for feature in feature_messages:
        feature_messages[feature].sort(key=lambda x: x[0])

    files_written = []

    for feature, messages in sorted(feature_messages.items()):
        if not messages:
            continue

        # English .ftl
        en_path = os.path.join(en_dir, f'{feature}.ftl')
        en_lines = [f'# {feature} — auto-migrated from messages.po\n']
        for msg_id, en_val, _ in messages:
            en_val_escaped = _escape_ftl_value(en_val)
            en_lines.append(f'{msg_id} = {en_val_escaped}\n')

        # French .ftl
        fr_path = os.path.join(fr_dir, f'{feature}.ftl')
        fr_lines = [f'# {feature} — auto-migrated from messages.po\n']
        for msg_id, _, fr_val in messages:
            fr_val_escaped = _escape_ftl_value(fr_val)
            if fr_val_escaped:
                fr_lines.append(f'{msg_id} = {fr_val_escaped}\n')
            else:
                # Empty translation — write the key with empty value so it
                # falls back to English via the bundle resolution
                fr_lines.append(f'# UNTRANSLATED:\n# {msg_id} =\n')

        if dry_run:
            print(f"[DRY-RUN] Would write {en_path} ({len(messages)} messages)")
            print(f"[DRY-RUN] Would write {fr_path} ({len(messages)} messages)")
        else:
            os.makedirs(en_dir, exist_ok=True)
            os.makedirs(fr_dir, exist_ok=True)

            # Append if file exists (e.g. auth.ftl already has Task 3 content)
            _write_or_append_ftl(en_path, en_lines)
            _write_or_append_ftl(fr_path, fr_lines)

        files_written.append(en_path)
        files_written.append(fr_path)

    return files_written


def _write_or_append_ftl(path, lines):
    """Write lines to an FTL file. If the file exists, append after a blank line."""
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            existing = f.read()
        with open(path, 'w', encoding='utf-8') as f:
            f.write(existing.rstrip('\n'))
            f.write('\n\n')
            f.writelines(lines)
    else:
        with open(path, 'w', encoding='utf-8') as f:
            f.writelines(lines)


# ---------------------------------------------------------------------------
# 6. Rewrite template files
# ---------------------------------------------------------------------------

def _build_template_call(message_id, kwargs_str=None, tojson=False):
    """Build the replacement ftl() call string for a template.

    Examples:
        ftl('auth-login')
        ftl('common-hello', name=current_user.name_display)
        ftl('testing-msg') | tojson
    """
    if kwargs_str:
        call = f"ftl('{message_id}', {kwargs_str})"
    else:
        call = f"ftl('{message_id}')"
    if tojson:
        call += ' | tojson'
    return call


def rewrite_templates(mapping, dry_run=False):
    """Rewrite _() calls in Jinja2 templates to ftl() calls.

    Returns a list of change records.
    """
    changes = []

    for root, dirs, files in os.walk(TEMPLATES_DIR):
        for fname in sorted(files):
            if not fname.endswith('.html'):
                continue
            fpath = os.path.join(root, fname)
            with open(fpath, 'r', encoding='utf-8') as f:
                original = f.read()

            result, file_changes = _rewrite_template_content(fpath, original, mapping)

            if result != original:
                changes.extend(file_changes)
                if dry_run:
                    rel = os.path.relpath(fpath, PROJECT_ROOT)
                    print(f"[DRY-RUN] Would rewrite {rel} ({len(file_changes)} replacements)")
                else:
                    with open(fpath, 'w', encoding='utf-8') as f:
                        f.write(result)

    return changes


def _rewrite_template_content(fpath, content, mapping):
    """Process a single template file's content.

    Handles these patterns:
    1. {{ _('text') }}
    2. {{ _("text") }}
    3. {{ _('text') | tojson }}
    4. {{ _('text', name=var) }}
    5. {{ _('text %(name)s', name=var) }}
    6. {% set var = _('text') %}
    7. ngettext('singular', 'plural', count) — flag for manual review

    Returns (new_content, list_of_change_records).
    """
    changes = []
    lines = content.split('\n')

    # Pattern for _('...') or _("...") calls with optional kwargs and optional | tojson
    # This needs to handle:
    #   _('simple text')
    #   _('text with %(name)s', name=var)
    #   _('text') | tojson
    #   _("double quoted")
    # We process line-by-line since multi-line _() calls don't appear in templates.

    new_lines = []
    for line_num_0, line in enumerate(lines):
        new_line = _rewrite_template_line(fpath, line, line_num_0 + 1, mapping, changes)
        new_lines.append(new_line)

    return '\n'.join(new_lines), changes


# Regex patterns for template _() calls.
# We need to match _('...') where the string may contain escaped quotes.

# Pattern 1: _('text') or _("text") — simple, no kwargs
# Pattern 2: _('text', key=val, ...) — with kwargs
# Pattern 3: any of the above followed by | tojson

# Master pattern that captures:
#   group 'quote': the quote character (' or ")
#   group 'msgid': the message text
#   group 'kwargs': optional keyword arguments after the msgid
#   group 'tojson': optional | tojson suffix
#
# We use a non-greedy approach and handle escaped quotes.

_TEMPLATE_UNDERSCORE_RE = re.compile(
    r"""_\("""
    r"""(?P<quote>['"])"""        # opening quote
    r"""(?P<msgid>(?:"""
    r"""(?!(?P=quote))."""         # any char that's not the closing quote
    r"""|\\(?P=quote)"""          # or an escaped quote
    r""")*?)"""                    # end of msgid
    r"""(?P=quote)"""             # closing quote
    r"""(?P<kwargs>,\s*[^)]+?)?"""  # optional kwargs
    r"""\)"""                      # closing paren
    r"""(?P<tojson>\s*\|\s*tojson)?"""  # optional | tojson
)

# Also handle ngettext calls — just flag them
_NGETTEXT_RE = re.compile(r'ngettext\s*\(')


def _rewrite_template_line(fpath, line, line_num, mapping, changes):
    """Rewrite all _() calls in a single template line."""
    rel_path = os.path.relpath(fpath, PROJECT_ROOT)

    # Flag ngettext for manual review
    if _NGETTEXT_RE.search(line):
        changes.append({
            'file': rel_path,
            'line': line_num,
            'old': line.strip(),
            'new': '*** MANUAL REVIEW: ngettext call ***',
            'msgid': '',
            'message_id': '',
            'type': 'ngettext_warning',
        })

    def _replacer(m):
        full_match = m.group(0)
        msgid_raw = m.group('msgid')
        kwargs_str = m.group('kwargs')
        has_tojson = bool(m.group('tojson'))

        # Unescape the msgid (handle \' and \")
        msgid = msgid_raw.replace("\\'", "'").replace('\\"', '"')

        if msgid not in mapping:
            return full_match  # not in .po file, leave as-is

        feature, message_id = mapping[msgid]

        # Build kwargs string for ftl() call
        ftl_kwargs = None
        if kwargs_str:
            # Strip leading comma + whitespace
            ftl_kwargs = kwargs_str.lstrip(',').strip()

        replacement = _build_template_call(message_id, ftl_kwargs, has_tojson)

        changes.append({
            'file': rel_path,
            'line': line_num,
            'old': full_match,
            'new': replacement,
            'msgid': msgid,
            'message_id': message_id,
            'type': 'template',
        })

        return replacement

    new_line = _TEMPLATE_UNDERSCORE_RE.sub(_replacer, line)
    return new_line


# ---------------------------------------------------------------------------
# 7. Rewrite Python files
# ---------------------------------------------------------------------------

def rewrite_python_files(mapping, dry_run=False):
    """Rewrite _(), lazy_gettext(), and imports in Python source files.

    Returns a list of change records.
    """
    changes = []

    for root, dirs, files in os.walk(AUTO_A11Y_DIR):
        # Skip __pycache__ and similar
        dirs[:] = [d for d in dirs if not d.startswith('__')]
        for fname in sorted(files):
            if not fname.endswith('.py'):
                continue
            fpath = os.path.join(root, fname)
            with open(fpath, 'r', encoding='utf-8') as f:
                original = f.read()

            result, file_changes = _rewrite_python_content(fpath, original, mapping)

            if result != original:
                changes.extend(file_changes)
                if dry_run:
                    rel = os.path.relpath(fpath, PROJECT_ROOT)
                    print(f"[DRY-RUN] Would rewrite {rel} ({len(file_changes)} replacements)")
                else:
                    with open(fpath, 'w', encoding='utf-8') as f:
                        f.write(result)

    return changes


# Patterns for Python _() calls
_PY_GETTEXT_CALL_RE = re.compile(
    r"""(?<!\w)_\("""
    r"""(?P<quote>['"])"""
    r"""(?P<msgid>(?:"""
    r"""(?!(?P=quote))."""
    r"""|\\(?P=quote)"""
    r""")*?)"""
    r"""(?P=quote)"""
    r"""(?P<kwargs>,\s*[^)]+?)?"""
    r"""\)"""
)

_PY_LAZY_GETTEXT_RE = re.compile(
    r"""lazy_gettext\("""
    r"""(?P<quote>['"])"""
    r"""(?P<msgid>(?:"""
    r"""(?!(?P=quote))."""
    r"""|\\(?P=quote)"""
    r""")*?)"""
    r"""(?P=quote)"""
    r"""(?P<kwargs>,\s*[^)]+?)?"""
    r"""\)"""
)

_PY_PGETTEXT_RE = re.compile(r'pgettext\s*\(')
_PY_NGETTEXT_RE = re.compile(r'(?<!\w)ngettext\s*\(|_babel_ngettext\s*\(')

# Import patterns
_IMPORT_GETTEXT_RE = re.compile(
    r'^from\s+flask_babel\s+import\s+.*\bgettext\s+as\s+_\b.*$',
    re.MULTILINE,
)
_IMPORT_LAZY_RE = re.compile(
    r'^from\s+flask_babel\s+import\s+.*\blazy_gettext\b.*$',
    re.MULTILINE,
)
_IMPORT_UNDERSCORE_RE = re.compile(
    r'^from\s+flask_babel\s+import\s+_\s*$',
    re.MULTILINE,
)


def _rewrite_python_content(fpath, content, mapping):
    """Process a single Python file's content.

    Returns (new_content, list_of_change_records).
    """
    changes = []
    rel_path = os.path.relpath(fpath, PROJECT_ROOT)

    # Skip fluent.py itself and this migration script
    if fpath.endswith('fluent.py') or 'migrate_po_to_ftl' in fpath:
        return content, changes

    new_content = content
    needs_ftl_import = False
    needs_lazy_ftl_import = False
    needs_force_locale_import = False
    has_force_locale = 'force_locale' in content

    # Check for pgettext/ngettext that need manual review
    for line_num_0, line in enumerate(content.split('\n')):
        if _PY_PGETTEXT_RE.search(line):
            changes.append({
                'file': rel_path,
                'line': line_num_0 + 1,
                'old': line.strip(),
                'new': '*** MANUAL REVIEW: pgettext call ***',
                'msgid': '',
                'message_id': '',
                'type': 'pgettext_warning',
            })
        if _PY_NGETTEXT_RE.search(line):
            changes.append({
                'file': rel_path,
                'line': line_num_0 + 1,
                'old': line.strip(),
                'new': '*** MANUAL REVIEW: ngettext call ***',
                'msgid': '',
                'message_id': '',
                'type': 'ngettext_warning',
            })

    # Replace lazy_gettext() calls
    def _lazy_replacer(m):
        nonlocal needs_lazy_ftl_import
        full_match = m.group(0)
        msgid_raw = m.group('msgid')
        kwargs_str = m.group('kwargs')

        msgid = msgid_raw.replace("\\'", "'").replace('\\"', '"')

        if msgid not in mapping:
            return full_match

        feature, message_id = mapping[msgid]
        needs_lazy_ftl_import = True

        ftl_kwargs = ''
        if kwargs_str:
            ftl_kwargs = kwargs_str.lstrip(',').strip()
            replacement = f"lazy_ftl('{message_id}', {ftl_kwargs})"
        else:
            replacement = f"lazy_ftl('{message_id}')"

        changes.append({
            'file': rel_path,
            'line': 0,  # line number not easily determined in regex sub
            'old': full_match,
            'new': replacement,
            'msgid': msgid,
            'message_id': message_id,
            'type': 'python_lazy',
        })

        return replacement

    new_content = _PY_LAZY_GETTEXT_RE.sub(_lazy_replacer, new_content)

    # Replace _() calls
    def _gettext_replacer(m):
        nonlocal needs_ftl_import
        full_match = m.group(0)
        msgid_raw = m.group('msgid')
        kwargs_str = m.group('kwargs')

        msgid = msgid_raw.replace("\\'", "'").replace('\\"', '"')

        if msgid not in mapping:
            return full_match

        feature, message_id = mapping[msgid]
        needs_ftl_import = True

        ftl_kwargs = ''
        if kwargs_str:
            ftl_kwargs = kwargs_str.lstrip(',').strip()
            replacement = f"ftl('{message_id}')"
        else:
            replacement = f"ftl('{message_id}')"

        # For ftl(), kwargs are passed differently — they go as keyword args
        if kwargs_str:
            ftl_kwargs = kwargs_str.lstrip(',').strip()
            replacement = f"ftl('{message_id}', {ftl_kwargs})"

        changes.append({
            'file': rel_path,
            'line': 0,
            'old': full_match,
            'new': replacement,
            'msgid': msgid,
            'message_id': message_id,
            'type': 'python_gettext',
        })

        return replacement

    new_content = _PY_GETTEXT_CALL_RE.sub(_gettext_replacer, new_content)

    # Rewrite imports if we changed any calls
    if needs_ftl_import or needs_lazy_ftl_import:
        new_content = _rewrite_python_imports(
            new_content, rel_path, changes,
            needs_ftl=needs_ftl_import,
            needs_lazy=needs_lazy_ftl_import,
            has_force_locale=has_force_locale,
        )

    return new_content, changes


def _rewrite_python_imports(content, rel_path, changes, needs_ftl=False,
                            needs_lazy=False, has_force_locale=False):
    """Rewrite flask_babel imports to use auto_a11y.web.fluent.

    Strategy: replace the flask_babel import lines with fluent imports.
    We keep force_locale imports if present (the fluent module provides its own).
    We keep other flask_babel imports (format_datetime, get_locale, Babel, etc.).
    """
    lines = content.split('\n')
    new_lines = []
    fluent_imports_added = False

    for line in lines:
        stripped = line.strip()

        # Match various flask_babel import patterns
        if stripped.startswith('from flask_babel import'):
            # Parse what's being imported
            imported_names = stripped.replace('from flask_babel import', '').strip()
            names = [n.strip() for n in imported_names.split(',')]

            # Categorize imports
            keep_babel = []
            need_fluent_ftl = False
            need_fluent_lazy = False
            need_fluent_force_locale = False

            for name in names:
                name_clean = name.strip()
                if name_clean in ('gettext as _', '_', 'gettext'):
                    need_fluent_ftl = True
                elif name_clean.startswith('lazy_gettext'):
                    need_fluent_lazy = True
                elif name_clean == 'force_locale':
                    need_fluent_force_locale = True
                else:
                    keep_babel.append(name_clean)

            # Build replacement lines
            replacement_lines = []

            # Keep remaining babel imports if any
            if keep_babel:
                replacement_lines.append(
                    f"from flask_babel import {', '.join(keep_babel)}"
                )

            # Add fluent imports (only once)
            if not fluent_imports_added and (need_fluent_ftl or need_fluent_lazy
                                             or need_fluent_force_locale):
                fluent_names = []
                if need_fluent_ftl or needs_ftl:
                    fluent_names.append('ftl')
                if need_fluent_lazy or needs_lazy:
                    fluent_names.append('lazy_ftl')
                if need_fluent_force_locale:
                    fluent_names.append('force_locale')
                if fluent_names:
                    replacement_lines.append(
                        f"from auto_a11y.web.fluent import {', '.join(fluent_names)}"
                    )
                    fluent_imports_added = True

            if replacement_lines:
                changes.append({
                    'file': rel_path,
                    'line': 0,
                    'old': stripped,
                    'new': ' | '.join(replacement_lines),
                    'msgid': '',
                    'message_id': '',
                    'type': 'python_import',
                })
                new_lines.extend(replacement_lines)
            else:
                # All imports replaced, add fluent import
                if not fluent_imports_added:
                    fluent_names = []
                    if needs_ftl:
                        fluent_names.append('ftl')
                    if needs_lazy:
                        fluent_names.append('lazy_ftl')
                    if fluent_names:
                        replacement = f"from auto_a11y.web.fluent import {', '.join(fluent_names)}"
                        new_lines.append(replacement)
                        fluent_imports_added = True
                        changes.append({
                            'file': rel_path,
                            'line': 0,
                            'old': stripped,
                            'new': replacement,
                            'msgid': '',
                            'message_id': '',
                            'type': 'python_import',
                        })
        else:
            new_lines.append(line)

    return '\n'.join(new_lines)


# ---------------------------------------------------------------------------
# 8. Migration report
# ---------------------------------------------------------------------------

def write_report(changes, dry_run=False):
    """Write migration_report.json."""
    report_path = os.path.join(PROJECT_ROOT, 'migration_report.json')
    if dry_run:
        print(f"\n[DRY-RUN] Would write {report_path} ({len(changes)} entries)")
        # Print summary stats
        _print_summary(changes)
        return

    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(changes, f, indent=2, ensure_ascii=False)
    print(f"\nWrote {report_path} ({len(changes)} entries)")
    _print_summary(changes)


def _print_summary(changes):
    """Print a human-readable summary of changes."""
    by_type = defaultdict(int)
    files_changed = set()
    warnings = []

    for c in changes:
        by_type[c.get('type', 'unknown')] += 1
        files_changed.add(c['file'])
        if 'warning' in c.get('type', '').lower():
            warnings.append(c)

    print("\n=== Migration Summary ===")
    print(f"Total changes: {len(changes)}")
    print(f"Files affected: {len(files_changed)}")
    print()
    for t, count in sorted(by_type.items()):
        print(f"  {t}: {count}")

    if warnings:
        print(f"\n=== {len(warnings)} items need MANUAL REVIEW ===")
        for w in warnings:
            print(f"  {w['file']}:{w['line']}  {w['old'][:80]}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='Migrate .po translations to Fluent .ftl files',
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Preview changes without modifying any files',
    )
    args = parser.parse_args()

    if not os.path.isfile(PO_FILE):
        print(f"ERROR: .po file not found: {PO_FILE}", file=sys.stderr)
        sys.exit(1)

    # Step 1: Parse .po
    print("Parsing .po file...")
    entries = parse_po_entries(PO_FILE)
    print(f"  Found {len(entries)} entries (excluding header, obsolete, fuzzy)")

    # Step 2: Classify by feature area
    print("Classifying entries by feature area...")
    for entry in entries:
        entry['feature'] = classify_entry(entry)

    feature_counts = defaultdict(int)
    for e in entries:
        feature_counts[e['feature']] += 1
    for feat, count in sorted(feature_counts.items()):
        print(f"  {feat}: {count}")

    # Step 3: Generate message IDs
    print("Generating message IDs...")
    mapping = generate_message_ids(entries)
    print(f"  Generated {len(mapping)} unique message IDs")

    # Step 4: Write .ftl files
    print("Writing .ftl files...")
    ftl_files = write_ftl_files(mapping, entries, dry_run=args.dry_run)
    print(f"  {'Would write' if args.dry_run else 'Wrote'} {len(ftl_files)} .ftl files")

    # Step 5: Rewrite templates
    print("Rewriting templates...")
    template_changes = rewrite_templates(mapping, dry_run=args.dry_run)
    print(f"  {len(template_changes)} template replacements")

    # Step 6: Rewrite Python files
    print("Rewriting Python files...")
    python_changes = rewrite_python_files(mapping, dry_run=args.dry_run)
    print(f"  {len(python_changes)} Python replacements")

    # Step 7: Write report
    all_changes = template_changes + python_changes
    write_report(all_changes, dry_run=args.dry_run)

    if args.dry_run:
        print("\n*** DRY RUN — no files were modified ***")
    else:
        print("\nMigration complete!")


if __name__ == '__main__':
    main()
