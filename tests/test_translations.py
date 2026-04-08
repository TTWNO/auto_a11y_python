"""Tests for translation catalog quality."""
import os
import re

PO_FILE = os.path.join(
    os.path.dirname(__file__), os.pardir,
    'auto_a11y', 'web', 'translations', 'fr', 'LC_MESSAGES', 'messages.po',
)


def parse_po_entries(path):
    """Parse a .po file into a list of (msgid, msgstr, line_number, refs) tuples.

    Only returns entries that have a non-empty msgid (skips the header).
    Handles multiline msgid/msgstr continuation strings.
    """
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()

    entries = []
    for block in re.split(r'\n\n+', content):
        lines = block.strip().split('\n')

        # Skip header block
        if any('Project-Id-Version' in l for l in lines):
            continue
        # Skip obsolete entries
        if any(l.startswith('#~') for l in lines):
            continue

        refs = [l for l in lines if l.startswith('#:')]
        is_fuzzy = any('#, fuzzy' in l for l in lines)

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
            })

    return entries


class TestTranslationQuality:
    """Checks that catch common .po file problems before they reach users."""

    def _entries(self):
        return parse_po_entries(PO_FILE)

    def test_no_duplicated_text_in_msgstr(self):
        """msgstr must not contain the same substring repeated 3+ times.

        This catches a common AI-translation bug where the same phrase is
        concatenated multiple times instead of appearing once.
        """
        failures = []
        for entry in self._entries():
            text = entry['msgstr']
            if len(text) < 60:
                continue
            # Try progressively longer prefixes to find a repeated chunk
            for chunk_len in range(20, len(text) // 2 + 1):
                chunk = text[:chunk_len]
                if text.count(chunk) >= 3:
                    ref = entry['refs'][0] if entry['refs'] else '?'
                    failures.append(
                        f"  msgid: {entry['msgid'][:70]}...\n"
                        f"  repeated {text.count(chunk)}x: {chunk[:50]}...\n"
                        f"  {ref}"
                    )
                    break

        assert not failures, (
            f"{len(failures)} msgstr entries contain repeated text:\n\n"
            + "\n\n".join(failures)
        )

    def test_no_empty_msgstr(self):
        """Every non-fuzzy entry with a msgid must have a msgstr."""
        failures = []
        for entry in self._entries():
            if entry['fuzzy']:
                continue
            if not entry['msgstr']:
                ref = entry['refs'][0] if entry['refs'] else '?'
                failures.append(
                    f"  msgid: {entry['msgid'][:70]}\n  {ref}"
                )

        assert not failures, (
            f"{len(failures)} entries have empty msgstr:\n\n"
            + "\n\n".join(failures)
        )

    def test_format_strings_preserved(self):
        """%(name)s placeholders in msgid must also appear in msgstr."""
        failures = []
        for entry in self._entries():
            if not entry['msgstr']:
                continue
            placeholders = set(re.findall(r'%\([^)]+\)s', entry['msgid']))
            if not placeholders:
                continue
            msgstr_placeholders = set(
                re.findall(r'%\([^)]+\)s', entry['msgstr'])
            )
            if placeholders != msgstr_placeholders:
                ref = entry['refs'][0] if entry['refs'] else '?'
                failures.append(
                    f"  msgid: {entry['msgid'][:70]}\n"
                    f"  expected: {placeholders}\n"
                    f"  got:      {msgstr_placeholders}\n"
                    f"  {ref}"
                )

        assert not failures, (
            f"{len(failures)} entries have mismatched format placeholders:\n\n"
            + "\n\n".join(failures)
        )
