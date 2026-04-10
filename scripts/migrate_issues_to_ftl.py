#!/usr/bin/env python3
"""
Migrate issue descriptions and WCAG labels to Fluent FTL format.

This script reads:
  - auto_a11y/reporting/issue_descriptions_enhanced.py (English issue descriptions)
  - auto_a11y/reporting/issue_translations_fr.json (French issue descriptions)
  - auto_a11y/reporting/issue_translations_inline.py (EN->FR simple string translations)
  - auto_a11y/reporting/wcag_translations_fr.py (EN->FR WCAG criterion names)

And generates:
  - auto_a11y/web/translations/en/issues.ftl
  - auto_a11y/web/translations/fr/issues.ftl
  - auto_a11y/web/translations/en/inline-issues.ftl
  - auto_a11y/web/translations/fr/inline-issues.ftl
  - auto_a11y/web/translations/en/wcag.ftl
  - auto_a11y/web/translations/fr/wcag.ftl
"""

import json
import os
import re
import sys
import hashlib

# Project root
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORTING_DIR = os.path.join(PROJECT_ROOT, 'auto_a11y', 'reporting')
TRANSLATIONS_DIR = os.path.join(PROJECT_ROOT, 'auto_a11y', 'web', 'translations')


def escape_ftl_value(text):
    """Escape a string for use as an FTL value.

    FTL values must not start with a dot, asterisk, or bracket on a line,
    and braces { } must be escaped as {"{"} / {"}"}.
    Uses single-pass replacement to avoid double-escaping.
    """
    if not text:
        return ''
    # Escape literal braces in a single pass to avoid double-escaping
    result = []
    for ch in text:
        if ch == '{':
            result.append('{"{"}'  )
        elif ch == '}':
            result.append('{"}"}'  )
        elif ch == '\n':
            result.append('\n    ')
        else:
            result.append(ch)
    return ''.join(result)


def slugify_for_ftl(text):
    """Convert an English text string to a stable FTL message ID.

    Uses a hash-based approach to guarantee unique, valid FTL identifiers
    for the ~550 inline issue translation strings.
    """
    # Start with a readable prefix from the text
    slug = text.lower().strip()
    # Replace non-alphanumeric with hyphens
    slug = re.sub(r'[^a-z0-9]+', '-', slug)
    # Remove leading/trailing hyphens
    slug = slug.strip('-')
    # Truncate to a reasonable length
    slug = slug[:80]
    # Remove trailing hyphen after truncation
    slug = slug.rstrip('-')
    # Add a short hash suffix for uniqueness
    text_hash = hashlib.md5(text.encode()).hexdigest()[:6]
    return f"issue-{slug}-{text_hash}"


def wcag_name_to_ftl_id(name):
    """Convert a WCAG criterion name to a Fluent message ID."""
    slug = name.lower().strip()
    slug = re.sub(r'[^a-z0-9]+', '-', slug)
    slug = slug.strip('-')
    return f"wcag-{slug}"


# ---------------------------------------------------------------------------
# 1. Load English issue descriptions from issue_descriptions_enhanced.py
# ---------------------------------------------------------------------------
def load_english_issues():
    """Parse issue_descriptions_enhanced.py to extract the descriptions dict."""
    enhanced_path = os.path.join(REPORTING_DIR, 'issue_descriptions_enhanced.py')
    with open(enhanced_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # We need to execute the function to get the descriptions dict.
    # The function uses ImpactScale.HIGH.value etc., so we set up a minimal env.
    namespace = {}
    exec(content, namespace)
    # Call the function with a dummy code to trigger the descriptions dict creation
    # Actually, the descriptions dict is inside the function. Let's extract it differently.

    # Extract the dict literal from the function body
    # Find 'descriptions = {' and read until the matching '}'
    descriptions = {}
    func = namespace.get('get_detailed_issue_description')
    if func:
        # Call with an arbitrary code to get the descriptions populated
        # Actually this won't work - the function returns per-code.
        # Let's parse the raw dict from the source.
        pass

    # Parse the source directly using regex to extract key-value entries
    # Look for patterns like:  'ErrorCode': { ... }
    # This is more reliable than trying to exec the function.
    import ast

    # Find the descriptions = { ... } block
    desc_start = content.find("descriptions = {")
    if desc_start == -1:
        print("ERROR: Could not find 'descriptions = {' in enhanced file")
        sys.exit(1)

    # Find matching closing brace by counting braces
    brace_count = 0
    in_string = False
    string_char = None
    escape_next = False
    i = desc_start + len("descriptions = ")
    start_brace = i

    for i in range(start_brace, len(content)):
        ch = content[i]
        if escape_next:
            escape_next = False
            continue
        if ch == '\\':
            escape_next = True
            continue
        if in_string:
            if ch == string_char:
                in_string = False
            continue
        if ch in ('"', "'"):
            in_string = True
            string_char = ch
            continue
        if ch == '{':
            brace_count += 1
        elif ch == '}':
            brace_count -= 1
            if brace_count == 0:
                end_brace = i
                break

    dict_source = content[start_brace:end_brace + 1]

    # Replace ImpactScale.*.value with string literals so we can eval
    dict_source = dict_source.replace("ImpactScale.HIGH.value", "'High'")
    dict_source = dict_source.replace("ImpactScale.MEDIUM.value", "'Medium'")
    dict_source = dict_source.replace("ImpactScale.LOW.value", "'Low'")
    dict_source = dict_source.replace("ImpactScale.INFO.value", "'Info'")

    try:
        descriptions = eval(dict_source)
    except Exception as e:
        print(f"ERROR parsing descriptions dict: {e}")
        # Try with ast.literal_eval as fallback
        try:
            descriptions = ast.literal_eval(dict_source)
        except Exception as e2:
            print(f"ERROR with ast.literal_eval: {e2}")
            sys.exit(1)

    return descriptions


# ---------------------------------------------------------------------------
# 2. Load French issue descriptions from JSON
# ---------------------------------------------------------------------------
def load_french_issues():
    """Load French issue descriptions from the JSON file."""
    json_path = os.path.join(REPORTING_DIR, 'issue_translations_fr.json')
    with open(json_path, 'r', encoding='utf-8') as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# 3. Load inline issue translations
# ---------------------------------------------------------------------------
def load_inline_translations():
    """Parse issue_translations_inline.py to extract the EN->FR dict."""
    inline_path = os.path.join(REPORTING_DIR, 'issue_translations_inline.py')
    with open(inline_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Execute to get the dict
    namespace = {}
    exec(content, namespace)
    return namespace.get('ISSUE_DESCRIPTION_TRANSLATIONS_FR', {})


# ---------------------------------------------------------------------------
# 4. Load WCAG translations
# ---------------------------------------------------------------------------
def load_wcag_translations():
    """Parse wcag_translations_fr.py to extract the EN->FR dict."""
    wcag_path = os.path.join(REPORTING_DIR, 'wcag_translations_fr.py')
    with open(wcag_path, 'r', encoding='utf-8') as f:
        content = f.read()

    namespace = {}
    exec(content, namespace)
    return namespace.get('WCAG_TRANSLATIONS_FR', {})


# ---------------------------------------------------------------------------
# 5. Generate FTL files for structured issues (by error code)
# ---------------------------------------------------------------------------
ISSUE_FIELDS = ['title', 'what', 'why', 'who', 'remediation', 'what_generic']


def generate_issues_ftl(descriptions_en, descriptions_fr):
    """Generate issues.ftl for EN and FR.

    Each issue code becomes an FTL message with attributes:
        ErrNoAlt =
            .title = Image has no alt attribute
            .what = ...
            .why = ...
            .who = ...
            .remediation = ...
            .what-generic = ...
    """
    en_lines = [
        '# Issue descriptions — English',
        '# Auto-generated by scripts/migrate_issues_to_ftl.py',
        '# Source: auto_a11y/reporting/issue_descriptions_enhanced.py',
        '',
    ]
    fr_lines = [
        '# Issue descriptions — French',
        '# Auto-generated by scripts/migrate_issues_to_ftl.py',
        '# Source: auto_a11y/reporting/issue_translations_fr.json',
        '',
    ]

    # Sort keys for deterministic output
    all_codes = sorted(set(list(descriptions_en.keys()) + [
        k for k in descriptions_fr.keys() if not k.endswith('_what')
    ]))

    en_count = 0
    fr_count = 0

    for code in all_codes:
        en_data = descriptions_en.get(code, {})
        fr_data = descriptions_fr.get(code, {})

        # Build EN entry
        has_en = False
        en_entry = [f'{code} =']
        for field in ISSUE_FIELDS:
            ftl_field = field.replace('_', '-')
            value = en_data.get(field, '')
            if value:
                en_entry.append(f'    .{ftl_field} = {escape_ftl_value(value)}')
                has_en = True

        if has_en:
            en_lines.extend(en_entry)
            en_lines.append('')
            en_count += 1

        # Build FR entry
        has_fr = False
        fr_entry = [f'{code} =']
        for field in ISSUE_FIELDS:
            ftl_field = field.replace('_', '-')
            value = fr_data.get(field, '')
            if value:
                fr_entry.append(f'    .{ftl_field} = {escape_ftl_value(value)}')
                has_fr = True

        # Also check for _what generic translations
        what_key = f'{code}_what'
        if what_key in descriptions_fr:
            generic_val = descriptions_fr[what_key].get('generic', '')
            if generic_val and not fr_data.get('what_generic'):
                fr_entry.append(f'    .what-generic = {escape_ftl_value(generic_val)}')
                has_fr = True

        if has_fr:
            fr_lines.extend(fr_entry)
            fr_lines.append('')
            fr_count += 1

    print(f"  issues.ftl: EN={en_count} messages, FR={fr_count} messages")
    return '\n'.join(en_lines) + '\n', '\n'.join(fr_lines) + '\n'


# ---------------------------------------------------------------------------
# 6. Generate FTL files for inline issue translations (flat EN->FR strings)
# ---------------------------------------------------------------------------
def generate_inline_issues_ftl(inline_translations):
    """Generate inline-issues.ftl for EN and FR.

    These are simple string-to-string translations used by the translate_issue
    filter. Each English string gets a stable FTL ID based on its content.

    Returns (en_content, fr_content, id_map) where id_map maps EN text -> FTL ID.
    """
    en_lines = [
        '# Inline issue description translations — English',
        '# Auto-generated by scripts/migrate_issues_to_ftl.py',
        '# Source: auto_a11y/reporting/issue_translations_inline.py',
        '',
    ]
    fr_lines = [
        '# Inline issue description translations — French',
        '# Auto-generated by scripts/migrate_issues_to_ftl.py',
        '# Source: auto_a11y/reporting/issue_translations_inline.py',
        '',
    ]

    id_map = {}
    count = 0

    for en_text, fr_text in sorted(inline_translations.items()):
        ftl_id = slugify_for_ftl(en_text)
        id_map[en_text] = ftl_id

        en_lines.append(f'{ftl_id} = {escape_ftl_value(en_text)}')
        fr_lines.append(f'{ftl_id} = {escape_ftl_value(fr_text)}')
        count += 1

    print(f"  inline-issues.ftl: {count} messages")
    return '\n'.join(en_lines) + '\n', '\n'.join(fr_lines) + '\n', id_map


# ---------------------------------------------------------------------------
# 7. Generate FTL files for WCAG criterion names
# ---------------------------------------------------------------------------
def generate_wcag_ftl(wcag_translations):
    """Generate wcag.ftl for EN and FR."""
    en_lines = [
        '# WCAG 2.2 Success Criterion names — English',
        '# Auto-generated by scripts/migrate_issues_to_ftl.py',
        '# Source: auto_a11y/reporting/wcag_translations_fr.py',
        '',
    ]
    fr_lines = [
        '# WCAG 2.2 Success Criterion names — French',
        '# Auto-generated by scripts/migrate_issues_to_ftl.py',
        '# Source: auto_a11y/reporting/wcag_translations_fr.py',
        '',
    ]

    count = 0
    for en_name, fr_name in sorted(wcag_translations.items()):
        ftl_id = wcag_name_to_ftl_id(en_name)
        en_lines.append(f'{ftl_id} = {escape_ftl_value(en_name)}')
        fr_lines.append(f'{ftl_id} = {escape_ftl_value(fr_name)}')
        count += 1

    print(f"  wcag.ftl: {count} messages")
    return '\n'.join(en_lines) + '\n', '\n'.join(fr_lines) + '\n'


# ---------------------------------------------------------------------------
# 8. Write FTL files
# ---------------------------------------------------------------------------
def write_ftl(locale, filename, content):
    """Write an FTL file to the translations directory."""
    locale_dir = os.path.join(TRANSLATIONS_DIR, locale)
    os.makedirs(locale_dir, exist_ok=True)
    filepath = os.path.join(locale_dir, filename)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f"  Wrote {filepath}")


# ---------------------------------------------------------------------------
# 9. Write the ID map for inline issues (so code can look up FTL IDs)
# ---------------------------------------------------------------------------
def write_id_map(id_map):
    """Write the inline issue ID map as a JSON file for runtime lookup."""
    map_path = os.path.join(TRANSLATIONS_DIR, 'inline_issue_ids.json')
    with open(map_path, 'w', encoding='utf-8') as f:
        json.dump(id_map, f, ensure_ascii=False, indent=2, sort_keys=True)
    print(f"  Wrote {map_path} ({len(id_map)} entries)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    os.chdir(PROJECT_ROOT)

    print("Loading source data...")
    descriptions_en = load_english_issues()
    print(f"  English issue descriptions: {len(descriptions_en)} codes")

    descriptions_fr = load_french_issues()
    fr_codes = [k for k in descriptions_fr if not k.endswith('_what')]
    print(f"  French issue descriptions: {len(fr_codes)} codes ({len(descriptions_fr)} total entries)")

    inline_translations = load_inline_translations()
    print(f"  Inline issue translations: {len(inline_translations)} pairs")

    wcag_translations = load_wcag_translations()
    print(f"  WCAG translations: {len(wcag_translations)} pairs")

    print("\nGenerating FTL files...")

    # Structured issues (by error code)
    en_issues, fr_issues = generate_issues_ftl(descriptions_en, descriptions_fr)
    write_ftl('en', 'issues.ftl', en_issues)
    write_ftl('fr', 'issues.ftl', fr_issues)

    # Inline issue translations (flat string->string)
    en_inline, fr_inline, id_map = generate_inline_issues_ftl(inline_translations)
    write_ftl('en', 'inline-issues.ftl', en_inline)
    write_ftl('fr', 'inline-issues.ftl', fr_inline)
    write_id_map(id_map)

    # WCAG criterion names
    en_wcag, fr_wcag = generate_wcag_ftl(wcag_translations)
    write_ftl('en', 'wcag.ftl', en_wcag)
    write_ftl('fr', 'wcag.ftl', fr_wcag)

    print("\nDone! Generated 6 FTL files + 1 ID map.")


if __name__ == '__main__':
    main()
