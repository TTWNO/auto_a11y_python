"""Tests for streaming interface on report formatters."""

import json
import csv
import os
import tempfile
import shutil
import pytest

from auto_a11y.reporting.formatters import BaseFormatter, CSVFormatter, JSONFormatter


# ---------------------------------------------------------------------------
# Task 2 – BaseFormatter streaming interface
# ---------------------------------------------------------------------------

class TestBaseFormatterStreaming:
    """Verify the abstract streaming methods on BaseFormatter."""

    def setup_method(self):
        self.formatter = BaseFormatter(config={})

    def test_begin_raises(self):
        with pytest.raises(NotImplementedError):
            self.formatter.begin("out.txt", {})

    def test_append_page_raises(self):
        with pytest.raises(NotImplementedError):
            self.formatter.append_page("out.txt", {})

    def test_finalize_raises(self):
        with pytest.raises(NotImplementedError):
            self.formatter.finalize("out.txt", {})

    def test_cleanup_is_noop(self):
        # Should not raise and return None
        result = self.formatter.cleanup()
        assert result is None
