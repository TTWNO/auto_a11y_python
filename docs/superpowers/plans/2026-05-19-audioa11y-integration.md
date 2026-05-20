# AudioA11y Video-Processing Integration Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port the `pythonAudioA11y` video → issues pipeline into `auto_a11y_python` so an uploaded MP4 produces a Recording (with issues, painpoints, takeaways, assertions) without launching a separate tool.

**Architecture:** New `auto_a11y/audio/` package containing focused modules per pipeline stage (segmenter, transcription, vtt_processor, speaker_identification, analysis, callouts, cost), a sequential `pipeline.py` orchestrator, and a `VideoRunner` wired into the existing `JobManager`. A new `auto_a11y/core/preflight.py` checks startup dependencies; failures route to a new Settings Recovery blueprint instead of crashing.

**Tech Stack:** Python 3.11+, Flask, MongoDB / PyMongo, `anthropic` SDK (already in requirements), `deepgram-sdk` (new), `ffmpeg-python` (new), `pyannote.audio` + `torch` + `torchaudio` + `scikit-learn` (new heavy deps for speaker remapping), `webvtt-py` (new for VTT parsing), system `ffmpeg` + `ffprobe` binaries.

**Spec:** [`docs/superpowers/specs/2026-05-19-audioa11y-integration-design.md`](../specs/2026-05-19-audioa11y-integration-design.md)

**Branch:** `audioA11y-integration` (off `main`; spec already committed at `c7930b5a`, `bf998f8e`, `4a02a4c8`, `0b18ae13`).

---

## Pre-flight (do once, before Phase 0)

- [ ] **P0.1 — Activate venv.** Pre-commit hooks run pyright; pyright needs `.venv` activated to resolve third-party deps. Without it, pyright emits ~1600 spurious errors and every commit blocks.

```bash
cd /home/tait/Documents/cnib/code/auto_a11y_python
source .venv/bin/activate
```

- [ ] **P0.2 — Verify branch and clean tree.**

```bash
git status
# Expected: "On branch audioA11y-integration", "nothing to commit, working tree clean"
git log --oneline main..HEAD
# Expected: four "spec:" commits (c7930b5a, bf998f8e, 4a02a4c8, 0b18ae13)
```

- [ ] **P0.3 — Bring up Mongo for the duration of the work.**

```bash
# If no system Mongo daemon, run an ephemeral one:
mkdir -p /tmp/mongo-audioa11y
mongod --dbpath /tmp/mongo-audioa11y --bind_ip 127.0.0.1 --logpath /tmp/mongo-audioa11y.log --fork
# Verify:
python -c "from pymongo import MongoClient; MongoClient('mongodb://localhost:27017/', serverSelectionTimeoutMS=2000).admin.command('ping'); print('mongo OK')"
# Tear down at the end of the work:
#   mongod --dbpath /tmp/mongo-audioa11y --shutdown && rm -rf /tmp/mongo-audioa11y /tmp/mongo-audioa11y.log
```

- [ ] **P0.4 — Read these reference files** to internalise the patterns:
  - `auto_a11y/pdf/audit/ghostscript.py` — binary-detection + caching pattern (model for `audio/ffmpeg.py`)
  - `auto_a11y/pdf/storage.py` — atomic write-then-rename pattern (model for `audio/storage.py`)
  - `auto_a11y/pdf/pdfmax_runner.py` — async runner wired into `JobManager` (model for `audio/runner.py`)
  - `tests/web/conftest.py` — Flask test fixture pattern from the dictaphone-tester branch (will be referenced for Phase 7+)
  - `pythonAudioA11y/audio_a11y.py` — the source orchestrator (lines 109-371)
  - `pythonAudioA11y/{audio_processor, transcription, vtt_processor, speaker_identification, analysis, video_processor}.py` — the source pipeline modules

---

## Phase 0 — Cleanup: remove cross-page linking

**Goal:** Strip the `page_ids` / `page_urls` / `discovered_page_ids` fields from `Recording` and `RecordingIssue` (and every place that touches them) so the new pipeline never has the option to write the dead linking model. A migration script `$unset`s the fields on existing documents.

**Files:**
- Modify: `auto_a11y/models/recording.py`
- Modify: `auto_a11y/models/recording_issue.py`
- Modify: `auto_a11y/importers/dictaphone_importer.py:237-243` (the propagation block)
- Modify: `auto_a11y/web/routes/recordings.py` (upload + edit form handlers)
- Modify: `auto_a11y/web/templates/recordings/upload.html`
- Modify: `auto_a11y/web/templates/recordings/edit.html`
- Modify: `auto_a11y/web/routes/api/recordings.py` (PATCH schemas)
- Modify: `docs/openapi.yaml` (recording schemas)
- Modify: existing tests under `tests/api/test_recordings.py`, `tests/api/test_recording_content.py`
- Create: `scripts/migrate_remove_recording_page_fields.py`

### Task 0.1 — Drop the fields from the dataclasses

- [ ] **Step 1: Read current model files.**

```bash
grep -n "page_ids\|page_urls\|discovered_page_ids" auto_a11y/models/recording.py auto_a11y/models/recording_issue.py
```

- [ ] **Step 2: Delete the three fields from `Recording`** (in `auto_a11y/models/recording.py`):
  - Remove the `page_ids: list[str] = field(default_factory=lambda: [])` field declaration.
  - Remove the `page_urls: list[str] = field(default_factory=lambda: [])` field declaration.
  - Remove the `discovered_page_ids: list[str] = field(default_factory=lambda: [])` field declaration.
  - Remove `'page_ids'`, `'page_urls'`, `'discovered_page_ids'` keys from `to_dict()`.
  - Remove the same keys from `from_dict()` parsing.

- [ ] **Step 3: Delete the two fields from `RecordingIssue`** (in `auto_a11y/models/recording_issue.py`):
  - Remove `page_ids` and `page_urls` field declarations + serialization.

- [ ] **Step 4: Run mypy + pyright to find dangling references.**

```bash
.venv/bin/python -m mypy auto_a11y/models/ 2>&1 | tail
.venv/bin/python -m pyright auto_a11y/models/ 2>&1 | tail
```

Address every error. Do not suppress.

### Task 0.2 — Remove propagation in the importer

- [ ] **Step 1: Read the propagation block.**

```bash
sed -n '230,250p' auto_a11y/importers/dictaphone_importer.py
```

- [ ] **Step 2: In `auto_a11y/importers/dictaphone_importer.py`, delete only the `parsed_issue.page_ids = ...` and `parsed_issue.page_urls = ...` lines** in the block at 237-243. KEEP every other assignment in that block (`website_ids`, `component_names`, `app_screens`, `device_sections`, `task_description`).

- [ ] **Step 3: Run the importer's tests.**

```bash
pytest tests/api/test_recordings.py tests/api/test_recording_content.py -v
```

Expected: failures only where tests assert on the removed fields. Move on to Task 0.3 to fix the tests; do not yet touch unrelated failures.

### Task 0.3 — Update upload + edit form handlers

- [ ] **Step 1: Read the relevant routes.**

```bash
grep -n "page_urls\|page_ids\|discovered_page_ids" auto_a11y/web/routes/recordings.py
```

- [ ] **Step 2: In `recordings.py` upload handler (around line 263-290)**, remove the form parsing for `page_urls` (multiline textarea) and `discovered_page_ids` (checkboxes). Remove the matching `recording.page_urls = ...` / `recording.page_ids = ...` assignments. Pass them no further into the importer.

- [ ] **Step 3: In the edit handler (around line 500)**, remove the same fields.

- [ ] **Step 4: In `templates/recordings/upload.html`**, remove the page-URL textarea and the discovered-page checkboxes section.

- [ ] **Step 5: In `templates/recordings/edit.html`**, do the same.

- [ ] **Step 6: In `templates/recordings/detail.html`**, remove any rendering that reads from the deleted fields (search for `page_urls`, `page_ids`).

### Task 0.4 — Clean the REST API surface + OpenAPI

- [ ] **Step 1: Find API references.**

```bash
grep -rn "page_ids\|page_urls\|discovered_page_ids" auto_a11y/web/routes/api/ docs/openapi.yaml
```

- [ ] **Step 2: Remove the fields from `auto_a11y/web/routes/api/recordings.py`** PATCH/POST schemas (whatever pydantic/jsonschema validators reference them).

- [ ] **Step 3: Update `docs/openapi.yaml`** Recording + RecordingIssue schema definitions to drop the same properties.

- [ ] **Step 4: Run the contract test:**

```bash
pytest tests/api/test_openapi_contract.py -v
```

Address any drift errors.

### Task 0.5 — Update existing tests

- [ ] **Step 1: Find tests that exercise the removed fields.**

```bash
grep -rn "page_ids\|page_urls\|discovered_page_ids" tests/
```

- [ ] **Step 2: Remove the assertions / fixture-data lines that exercise these fields.** Do NOT delete entire tests — only the field-specific assertions.

- [ ] **Step 3: Run the tests.**

```bash
pytest tests/api/test_recordings.py tests/api/test_recording_content.py -v
```

Expected: all pass (the field assertions are gone; the rest of the recording API still works).

### Task 0.6 — Write the migration script

- [ ] **Step 1: Create `scripts/migrate_remove_recording_page_fields.py`.**

```python
"""One-shot migration: $unset page_ids / page_urls / discovered_page_ids on
every doc in `recordings` and `recording_issues`.

Idempotent. Logs touched-doc counts. Run after deploying the code change;
the code already tolerates absent fields, so running before/after/never
is also fine.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from auto_a11y.core.database import Database

logger = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    uri = os.environ.get("MONGODB_URI", "mongodb://localhost:27017/")
    db_name = os.environ.get("DATABASE_NAME", "auto_a11y")
    db = Database(uri, db_name)
    try:
        unset = {"$unset": {"page_ids": "", "page_urls": "", "discovered_page_ids": ""}}
        r1 = db.recordings.update_many({}, unset)
        r2 = db.recording_issues.update_many({}, {"$unset": {"page_ids": "", "page_urls": ""}})
        logger.info("recordings.update_many: matched=%s modified=%s", r1.matched_count, r1.modified_count)
        logger.info("recording_issues.update_many: matched=%s modified=%s", r2.matched_count, r2.modified_count)
    finally:
        db.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Smoke-test the script** against the local Mongo:

```bash
python scripts/migrate_remove_recording_page_fields.py
# Expected log lines: "recordings.update_many: matched=… modified=…"
# Re-run; second run should report modified=0 (idempotent).
```

### Task 0.7 — Commit Phase 0

- [ ] **Step 1: Stage and commit.**

```bash
source .venv/bin/activate
git add auto_a11y/models/recording.py auto_a11y/models/recording_issue.py \
        auto_a11y/importers/dictaphone_importer.py \
        auto_a11y/web/routes/recordings.py auto_a11y/web/routes/api/recordings.py \
        auto_a11y/web/templates/recordings/upload.html \
        auto_a11y/web/templates/recordings/edit.html \
        auto_a11y/web/templates/recordings/detail.html \
        docs/openapi.yaml \
        tests/api/test_recordings.py tests/api/test_recording_content.py \
        scripts/migrate_remove_recording_page_fields.py
git commit --no-gpg-sign -m "$(cat <<'EOF'
cleanup: remove cross-page linking fields from Recording / RecordingIssue

Recordings are standalone artifacts; the future reporting layer will
merge them with page/website data. Strip the old hooks now so the
upcoming pipeline never has the option to write the dead model.

Removed:
- Recording.page_ids, .page_urls, .discovered_page_ids
- RecordingIssue.page_ids, .page_urls
- Importer propagation of these onto each issue (kept the surrounding
  website_ids / component_names / app_screens / device_sections /
  task_description assignments, those are recording-internal metadata).
- Upload + edit form fields, templates, OpenAPI schemas, REST routes.

Migration:
- scripts/migrate_remove_recording_page_fields.py $unsets the fields
  on existing docs. Idempotent. Code tolerates absent fields, so the
  migration is for tidiness, not correctness.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 2: Confirm pre-commit hooks all green** (mypy / pyright / ty / css-a11y / openapi / translation-coverage). If any fail, fix in a NEW commit. NEVER `--no-verify`, NEVER `--amend`.

---

## Phase 1 — Foundation: package skeleton + preflight

**Goal:** Create the `auto_a11y/audio/` package with config + storage + ffmpeg-detection scaffolding, and `auto_a11y/core/preflight.py` for the startup-check registry. No pipeline logic yet — just the file layout the later phases will fill in.

**Files:**
- Create: `auto_a11y/audio/__init__.py`
- Create: `auto_a11y/audio/config.py`
- Create: `auto_a11y/audio/storage.py`
- Create: `auto_a11y/audio/ffmpeg.py`
- Create: `auto_a11y/audio/errors.py`
- Create: `auto_a11y/core/preflight.py`
- Create: `tests/audio/__init__.py`
- Create: `tests/audio/test_storage.py`
- Create: `tests/audio/test_ffmpeg.py`
- Create: `tests/audio/test_preflight.py`
- Modify: `.gitignore` (add `!tests/audio/test_*.py` and `!tests/web/test_*.py` — the project gitignores `test_*.py` and per-dir allowlists; the existing entries are `!tests/test_*.py`, `!tests/pdf/test_*.py`, `!tests/api/test_*.py`, `!tests/api/schemas/test_*.py`, `!tests/api/openapi/test_*.py`. We add two more: audio for this branch, web for Phase 7+)
- Modify: `pyproject.toml` (extend mypy/pyright/ty include lists to cover `auto_a11y/audio/**`, `tests/audio/**`)

### Task 1.1 — Scaffold + gitignore + pyproject.toml include lists

- [ ] **Step 1: Make directories and empty `__init__.py` files.**

```bash
mkdir -p auto_a11y/audio auto_a11y/audio/prompts tests/audio data/recordings
touch auto_a11y/audio/__init__.py tests/audio/__init__.py
echo '# placeholder; populated in Phase 2' > auto_a11y/audio/prompts/.gitkeep
```

- [ ] **Step 2: Update `.gitignore`.** Add `!tests/audio/test_*.py` and `!tests/web/test_*.py` near the existing test-allowlist block. To find it, search for `!tests/pdf/test_*.py` (currently around line 108) — the new lines go right after the existing block.

- [ ] **Step 3: Update `pyproject.toml`.** Find the `mypy`, `pyright`, and `ty` include lists (search `auto_a11y/**/*.py` or similar). Confirm they already glob `auto_a11y/**/*.py` and `tests/**/*.py` — if so, no change needed. If they're explicit lists, add `auto_a11y/audio/**/*.py` and `tests/audio/**/*.py`.

- [ ] **Step 4: Confirm an empty pyright run** succeeds:

```bash
.venv/bin/python -m pyright auto_a11y/audio 2>&1 | tail -3
# Expected: 0 errors
```

### Task 1.2 — Audio errors + config (TDD)

- [ ] **Step 1: Write `tests/audio/test_storage.py` scaffold** (mirrors `tests/api/test_recordings.py:30-93` Mongo-skip-clean pattern):

```python
"""Tests for the per-Recording filesystem layout helper."""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from auto_a11y.audio.storage import (
    AudioStorage,
    InvalidRecordingId,
)


@pytest.fixture
def storage(tmp_path: Path) -> AudioStorage:
    return AudioStorage(root=tmp_path / "recordings")


def test_allocate_creates_directory_tree(storage: AudioStorage) -> None:
    allocated = storage.allocate("REC-20260519143022-a1b2c3")
    assert allocated.root.is_dir()
    assert (allocated.root / "audio").is_dir()
    assert (allocated.root / "vtt").is_dir()
    assert (allocated.root / "captions").is_dir()
    assert (allocated.root / "json").is_dir()
    assert (allocated.root / "html").is_dir()


def test_allocate_rejects_path_traversal(storage: AudioStorage) -> None:
    for bad in ["../etc/passwd", "REC-../", "x" * 200, ""]:
        with pytest.raises(InvalidRecordingId):
            storage.allocate(bad)


def test_paths_for_known_recording(storage: AudioStorage) -> None:
    a = storage.allocate("REC-20260519143022-a1b2c3")
    assert a.source_mp4 == a.root / "source.mp4"
    assert a.json_path(kind="issues", lang="en") == a.root / "json" / "REC-20260519143022-a1b2c3.issues.json"
    assert a.json_path(kind="painpoints", lang="fr") == a.root / "json" / "REC-20260519143022-a1b2c3.painpoints.fr.json"
    assert a.captions_vtt == a.root / "captions" / "REC-20260519143022-a1b2c3.vtt"
    assert a.callouts_mp4 == a.root / "video" / "REC-20260519143022-a1b2c3.callouts.mp4"
```

- [ ] **Step 2: Run — expect failure (module doesn't exist).**

```bash
pytest tests/audio/test_storage.py -v
# Expected: ERRORS — ModuleNotFoundError
```

- [ ] **Step 3: Create `auto_a11y/audio/errors.py`.**

```python
"""Typed audio-pipeline exceptions."""
from __future__ import annotations


class AudioPipelineError(Exception):
    """Base class for all audioA11y pipeline errors."""


class InvalidRecordingId(AudioPipelineError):
    """Recording id doesn't match the REC-YYYYMMDDHHMMSS-{6 hex} format."""


class FfmpegMissing(AudioPipelineError):
    """ffmpeg (or ffprobe) binary not on PATH and no override given."""

    def __init__(self, searched: list[str]) -> None:
        super().__init__("ffmpeg not found on PATH; searched: " + ", ".join(searched))
        self.searched = searched


class TranscriptionError(AudioPipelineError):
    """Wraps Deepgram failures after retries are exhausted."""


class AnalysisError(AudioPipelineError):
    """Wraps Anthropic failures after retries are exhausted."""


class CalloutsError(AudioPipelineError):
    """Callouts ffmpeg-overlay rendering failed. NEVER fails the whole job."""
```

- [ ] **Step 4: Create `auto_a11y/audio/config.py`.**

```python
"""AudioA11y pipeline configuration.

Reads from auto_a11y's existing config (env / .env / user-settings file).
No subprocess calls; just type-safe accessors.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class AudioConfig:
    deepgram_api_key: str
    anthropic_api_key: str
    claude_model: str          # e.g., "claude-opus-4-7"
    deepgram_model: str        # e.g., "nova-3"
    huggingface_token: str | None  # for pyannote.audio model auth; optional
    data_dir: str              # absolute path; "data/recordings/" lives under this

    @classmethod
    def from_env(cls) -> AudioConfig:
        return cls(
            deepgram_api_key=os.environ.get("DEEPGRAM_API_KEY", ""),
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
            claude_model=os.environ.get("CLAUDE_MODEL", "claude-opus-4-7"),
            deepgram_model=os.environ.get("DEEPGRAM_MODEL", "nova-3"),
            huggingface_token=os.environ.get("HF_TOKEN") or None,
            data_dir=os.environ.get("AUTO_A11Y_DATA_DIR", "data"),
        )
```

### Task 1.3 — Storage (TDD)

- [ ] **Step 1: Create `auto_a11y/audio/storage.py`.**

```python
"""Per-Recording filesystem layout for audio-processing artifacts.

Single source of truth: every pipeline module asks here for paths. No
string concatenation in the pipeline modules.

Mirrors auto_a11y/pdf/storage.py's atomic write-then-rename pattern.
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from auto_a11y.audio.errors import InvalidRecordingId

_RECORDING_ID_PATTERN = re.compile(r"^REC-[0-9]{14}-[a-f0-9]{6}$")


JsonKind = Literal["issues", "painpoints", "takeaways", "assertions"]
Lang = Literal["en", "fr"]


@dataclass(frozen=True)
class AllocatedSlot:
    """A per-Recording directory layout. All paths are absolute."""

    root: Path

    @property
    def source_mp4(self) -> Path:
        return self.root / "source.mp4"

    @property
    def audio_dir(self) -> Path:
        return self.root / "audio"

    @property
    def vtt_dir(self) -> Path:
        return self.root / "vtt"

    @property
    def captions_dir(self) -> Path:
        return self.root / "captions"

    @property
    def captions_vtt(self) -> Path:
        return self.captions_dir / f"{self.root.name}.vtt"

    @property
    def speaker_map(self) -> Path:
        return self.captions_dir / f"{self.root.name}.speaker-map.json"

    @property
    def json_dir(self) -> Path:
        return self.root / "json"

    @property
    def html_dir(self) -> Path:
        return self.root / "html"

    @property
    def video_dir(self) -> Path:
        return self.root / "video"

    @property
    def callouts_mp4(self) -> Path:
        return self.video_dir / f"{self.root.name}.callouts.mp4"

    @property
    def manifest(self) -> Path:
        return self.root / "manifest.json"

    @property
    def job_log(self) -> Path:
        return self.root / "job.log"

    def segment_m4a(self, index: int) -> Path:
        return self.audio_dir / f"segment-{index}.m4a"

    def segment_vtt(self, index: int) -> Path:
        return self.vtt_dir / f"segment-{index}.vtt"

    def segment_words(self, index: int) -> Path:
        return self.vtt_dir / f"segment-{index}.words"

    def json_path(self, *, kind: JsonKind, lang: Lang) -> Path:
        suffix = ".json" if lang == "en" else ".fr.json"
        return self.json_dir / f"{self.root.name}.{kind}{suffix}"

    def html_path(self, *, kind: JsonKind, lang: Lang) -> Path:
        suffix = ".html" if lang == "en" else ".fr.html"
        return self.html_dir / f"{self.root.name}.{kind}{suffix}"


class AudioStorage:
    """Owns the data/recordings/ tree."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _validate(self, recording_id: str) -> None:
        if not _RECORDING_ID_PATTERN.match(recording_id):
            raise InvalidRecordingId(
                f"recording_id {recording_id!r} does not match REC-YYYYMMDDHHMMSS-{{6 hex}}"
            )

    def allocate(self, recording_id: str) -> AllocatedSlot:
        """Create the per-Recording directory tree. Idempotent."""
        self._validate(recording_id)
        slot = AllocatedSlot(root=self.root / recording_id)
        for sub in (slot.root, slot.audio_dir, slot.vtt_dir, slot.captions_dir,
                    slot.json_dir, slot.html_dir, slot.video_dir):
            sub.mkdir(parents=True, exist_ok=True)
        return slot

    def get(self, recording_id: str) -> AllocatedSlot:
        """Return the slot without creating directories. Validates id."""
        self._validate(recording_id)
        return AllocatedSlot(root=self.root / recording_id)

    def delete(self, recording_id: str) -> None:
        """Recursively delete the per-Recording directory. Idempotent."""
        self._validate(recording_id)
        target = self.root / recording_id
        if target.exists():
            shutil.rmtree(target)

    def write_atomic(self, path: Path, content: bytes) -> None:
        """Write-temp-then-rename to avoid half-written files."""
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".write.", dir=str(path.parent))
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(content)
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
```

- [ ] **Step 2: Run the storage tests.**

```bash
pytest tests/audio/test_storage.py -v
# Expected: 3 PASSED
```

### Task 1.4 — ffmpeg detection (TDD)

- [ ] **Step 1: Write `tests/audio/test_ffmpeg.py`** (mocks subprocess to avoid actually running ffmpeg in unit tests):

```python
"""Tests for ffmpeg / ffprobe binary detection."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from auto_a11y.audio.errors import FfmpegMissing
from auto_a11y.audio.ffmpeg import detect_ffmpeg, detect_ffprobe, invalidate_detection_cache


@pytest.fixture(autouse=True)
def reset_cache() -> None:
    invalidate_detection_cache()


def test_detect_ffmpeg_finds_path_via_which() -> None:
    with patch("shutil.which", return_value="/usr/bin/ffmpeg"):
        assert detect_ffmpeg() == "/usr/bin/ffmpeg"


def test_detect_ffmpeg_returns_none_when_missing() -> None:
    with patch("shutil.which", return_value=None):
        assert detect_ffmpeg() is None


def test_detect_ffmpeg_raises_when_required() -> None:
    with patch("shutil.which", return_value=None):
        with pytest.raises(FfmpegMissing):
            detect_ffmpeg(raise_if_missing=True)


def test_override_skips_detection() -> None:
    with patch("shutil.which", return_value=None):
        assert detect_ffmpeg(override="/opt/local/bin/ffmpeg") == "/opt/local/bin/ffmpeg"


def test_detection_cached_within_process() -> None:
    with patch("shutil.which", return_value="/usr/bin/ffmpeg") as which:
        detect_ffmpeg()
        detect_ffmpeg()
        assert which.call_count == 1  # second call hit the cache


def test_invalidate_clears_cache() -> None:
    with patch("shutil.which", return_value="/usr/bin/ffmpeg") as which:
        detect_ffmpeg()
        invalidate_detection_cache()
        detect_ffmpeg()
        assert which.call_count == 2


def test_detect_ffprobe_is_independent_helper() -> None:
    with patch("shutil.which", return_value="/usr/bin/ffprobe"):
        assert detect_ffprobe() == "/usr/bin/ffprobe"
```

- [ ] **Step 2: Run — expect failure.**

```bash
pytest tests/audio/test_ffmpeg.py -v
# Expected: ERRORS — module not importable
```

- [ ] **Step 3: Create `auto_a11y/audio/ffmpeg.py`** modelled directly on `auto_a11y/pdf/audit/ghostscript.py`:

```python
"""ffmpeg / ffprobe detection.

Mirrors auto_a11y/pdf/audit/ghostscript.py: a small caching detector
with an `override` escape hatch and a `raise_if_missing` toggle.
The duration probe and silence-detection wrappers come in Phase 2.
"""
from __future__ import annotations

import logging
import shutil

from auto_a11y.audio.errors import FfmpegMissing

logger = logging.getLogger(__name__)

_FFMPEG_NAMES = ["ffmpeg", "ffmpeg.exe"]
_FFPROBE_NAMES = ["ffprobe", "ffprobe.exe"]

_cached_ffmpeg: str | None = None
_cached_ffprobe: str | None = None
_cache_populated: bool = False


def invalidate_detection_cache() -> None:
    global _cached_ffmpeg, _cached_ffprobe, _cache_populated
    _cached_ffmpeg = None
    _cached_ffprobe = None
    _cache_populated = False


def _detect(names: list[str]) -> str | None:
    for name in names:
        path = shutil.which(name)
        if path:
            return path
    return None


def detect_ffmpeg(*, override: str | None = None, raise_if_missing: bool = False) -> str | None:
    global _cached_ffmpeg, _cache_populated
    if override:
        return override
    if not _cache_populated:
        _populate_cache()
    if _cached_ffmpeg is None and raise_if_missing:
        raise FfmpegMissing(searched=_FFMPEG_NAMES)
    return _cached_ffmpeg


def detect_ffprobe(*, override: str | None = None, raise_if_missing: bool = False) -> str | None:
    global _cached_ffprobe, _cache_populated
    if override:
        return override
    if not _cache_populated:
        _populate_cache()
    if _cached_ffprobe is None and raise_if_missing:
        raise FfmpegMissing(searched=_FFPROBE_NAMES)
    return _cached_ffprobe


def _populate_cache() -> None:
    global _cached_ffmpeg, _cached_ffprobe, _cache_populated
    _cached_ffmpeg = _detect(_FFMPEG_NAMES)
    _cached_ffprobe = _detect(_FFPROBE_NAMES)
    _cache_populated = True
```

- [ ] **Step 4: Run the ffmpeg tests.**

```bash
pytest tests/audio/test_ffmpeg.py -v
# Expected: 7 PASSED
```

### Task 1.5 — Preflight registry (TDD)

- [ ] **Step 1: Write `tests/audio/test_preflight.py`.**

```python
"""Tests for the central preflight check registry."""
from __future__ import annotations

import pytest

from auto_a11y.core.preflight import Check, CheckOutcome, PreflightRegistry


def test_registry_runs_checks_in_registration_order() -> None:
    reg = PreflightRegistry()
    order: list[str] = []

    def first() -> CheckOutcome:
        order.append("first")
        return CheckOutcome.ok()

    def second() -> CheckOutcome:
        order.append("second")
        return CheckOutcome.ok()

    reg.register(Check(name="first", description="first", run=first))
    reg.register(Check(name="second", description="second", run=second))

    result = reg.run_all()
    assert order == ["first", "second"]
    assert result.all_passed


def test_failed_check_is_reported() -> None:
    reg = PreflightRegistry()
    reg.register(Check(
        name="ffmpeg",
        description="ffmpeg must be on PATH",
        run=lambda: CheckOutcome.failed("ffmpeg not found; install it via `apt install ffmpeg`"),
    ))
    result = reg.run_all()
    assert not result.all_passed
    assert result.failures[0].name == "ffmpeg"
    assert "apt install" in result.failures[0].remediation


def test_check_exception_becomes_failure() -> None:
    reg = PreflightRegistry()

    def raises() -> CheckOutcome:
        raise RuntimeError("boom")

    reg.register(Check(name="exploding", description="…", run=raises))
    result = reg.run_all()
    assert not result.all_passed
    assert "boom" in result.failures[0].remediation
```

- [ ] **Step 2: Run — expect failure.**

```bash
pytest tests/audio/test_preflight.py -v
# Expected: ERRORS — module not importable
```

- [ ] **Step 3: Create `auto_a11y/core/preflight.py`.**

```python
"""Central preflight check registry.

Each pipeline subsystem (ffmpeg, Deepgram, Anthropic, Mongo) registers
a Check at import time. App startup calls PreflightRegistry.run_all()
and routes failures to the Settings Recovery blueprint (see Phase 10).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CheckOutcome:
    ok_: bool
    remediation: str = ""

    @classmethod
    def ok(cls) -> CheckOutcome:
        return cls(ok_=True)

    @classmethod
    def failed(cls, remediation: str) -> CheckOutcome:
        return cls(ok_=False, remediation=remediation)


@dataclass(frozen=True)
class Check:
    name: str
    description: str
    run: Callable[[], CheckOutcome]


@dataclass(frozen=True)
class CheckResult:
    name: str
    description: str
    ok_: bool
    remediation: str


@dataclass(frozen=True)
class PreflightResult:
    results: list[CheckResult]

    @property
    def all_passed(self) -> bool:
        return all(r.ok_ for r in self.results)

    @property
    def failures(self) -> list[CheckResult]:
        return [r for r in self.results if not r.ok_]


class PreflightRegistry:
    """Module-level singleton: the registry global lives below."""

    def __init__(self) -> None:
        self._checks: list[Check] = []

    def register(self, check: Check) -> None:
        self._checks.append(check)

    def run_all(self) -> PreflightResult:
        results: list[CheckResult] = []
        for check in self._checks:
            try:
                outcome = check.run()
                results.append(CheckResult(
                    name=check.name,
                    description=check.description,
                    ok_=outcome.ok_,
                    remediation=outcome.remediation,
                ))
            except Exception as e:
                results.append(CheckResult(
                    name=check.name,
                    description=check.description,
                    ok_=False,
                    remediation=f"{type(e).__name__}: {e}",
                ))
        return PreflightResult(results=results)


_registry = PreflightRegistry()


def get_registry() -> PreflightRegistry:
    """Module-level singleton accessor. Pipeline subsystems call this at import."""
    return _registry
```

- [ ] **Step 4: Run the preflight tests.**

```bash
pytest tests/audio/test_preflight.py -v
# Expected: 3 PASSED
```

### Task 1.6 — Register the ffmpeg checks

- [ ] **Step 1: At the bottom of `auto_a11y/audio/ffmpeg.py`, add registration:**

```python
from auto_a11y.core.preflight import Check, CheckOutcome, get_registry


def _ffmpeg_check() -> CheckOutcome:
    path = detect_ffmpeg()
    if path is None:
        return CheckOutcome.failed(
            "ffmpeg not found. Install via your package manager "
            "(e.g., `apt install ffmpeg`, `brew install ffmpeg`, or download from ffmpeg.org)."
        )
    return CheckOutcome.ok()


def _ffprobe_check() -> CheckOutcome:
    path = detect_ffprobe()
    if path is None:
        return CheckOutcome.failed(
            "ffprobe not found. Usually ships with ffmpeg; install ffmpeg via your "
            "package manager."
        )
    return CheckOutcome.ok()


_registry = get_registry()
_registry.register(Check(name="ffmpeg", description="ffmpeg binary on PATH", run=_ffmpeg_check))
_registry.register(Check(name="ffprobe", description="ffprobe binary on PATH", run=_ffprobe_check))
```

- [ ] **Step 2: Run the unit tests** (the registrations should be no-ops at test time since `shutil.which` is mocked):

```bash
pytest tests/audio/ -v
# Expected: all PASS
```

### Task 1.7 — Commit Phase 1

- [ ] **Step 1: Stage and commit.**

```bash
source .venv/bin/activate
git add auto_a11y/audio/ auto_a11y/core/preflight.py tests/audio/ \
        .gitignore pyproject.toml
git commit --no-gpg-sign -m "$(cat <<'EOF'
audio: foundation — storage, ffmpeg detection, preflight registry

New auto_a11y/audio/ package skeleton:
- config.py    AudioConfig.from_env() — typed accessors for keys + models
- errors.py    AudioPipelineError + named subclasses
- storage.py   AudioStorage / AllocatedSlot — atomic write-then-rename;
               per-Recording directory layout under data/recordings/<id>/;
               REC-YYYYMMDDHHMMSS-{6 hex} validation
- ffmpeg.py    detect_ffmpeg / detect_ffprobe, modelled on
               pdf/audit/ghostscript.py's caching detector

New auto_a11y/core/preflight.py:
- Check + CheckOutcome + PreflightRegistry — central registry of
  startup checks. Phase 10 wires this into a Settings Recovery
  blueprint that routes everything to /recovery/ when any check fails.

ffmpeg / ffprobe register checks against the central registry.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase 2 — Audio extraction + segmentation

**Goal:** Port `pythonAudioA11y/audio_processor.py` (especially `split_audio()` and `detect_silence_points()`) into `auto_a11y/audio/segmenter.py`. The function turns a source MP4 into N silence-aware m4a segments (target 600 s; ±30 s search window). One small `.gitignore`-tracked fixture MP4 is downloaded on first run for the integration test.

**Files:**
- Create: `auto_a11y/audio/segmenter.py`
- Create: `tests/audio/test_segmenter.py`
- Create: `tests/audio/fixtures/__init__.py`
- Create: `scripts/fetch_audio_fixtures.py`
- Modify: `requirements.txt` — add `ffmpeg-python==0.2.0` (already used by pythonAudioA11y)

### Task 2.1 — Add ffmpeg-python dependency

- [ ] **Step 1: Confirm whether `ffmpeg-python` already in requirements:**

```bash
grep -i "ffmpeg" requirements.txt
```

- [ ] **Step 2: If absent, add the line** alphabetically near other `f*` packages. Pin the version that `pythonAudioA11y/requirements.txt` uses (`ffmpeg-python>=0.2.0`).

- [ ] **Step 3: Install in venv** (do NOT skip — pyright needs it on disk to resolve imports):

```bash
.venv/bin/pip install ffmpeg-python==0.2.0
```

- [ ] **Step 4: Run pyright** — confirm it can see the new dep:

```bash
.venv/bin/python -m pyright auto_a11y/audio/ 2>&1 | tail -3
# Expected: 0 errors
```

### Task 2.2 — Fixture downloader script

- [ ] **Step 1: Create `scripts/fetch_audio_fixtures.py`.** This downloads a small (~3 MB) test MP4 from a stable URL on first run. The fixture stays out of git (LFS not used here).

```python
"""Downloads the audioA11y test-fixture MP4 to tests/audio/fixtures/.

Idempotent. Skipped if the file already exists. Run once after cloning.
"""
from __future__ import annotations

import hashlib
import os
import sys
import urllib.request
from pathlib import Path

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "tests" / "audio" / "fixtures"
FIXTURE_NAME = "short_audit.mp4"
# A 30-second clip from pythonAudioA11y's tmp/ directory; first commit a
# copy somewhere fetchable and point this URL there. Until then, error.
FIXTURE_URL = os.environ.get(
    "AUDIOA11Y_FIXTURE_URL",
    "",  # blank means "no fixture available; integration tests will skip"
)
EXPECTED_SHA256 = os.environ.get(
    "AUDIOA11Y_FIXTURE_SHA256",
    "",
)


def main() -> int:
    target = FIXTURE_DIR / FIXTURE_NAME
    if target.exists():
        print(f"already present: {target}")
        return 0
    if not FIXTURE_URL:
        print(
            "fixture URL not configured; set AUDIOA11Y_FIXTURE_URL "
            "(and AUDIOA11Y_FIXTURE_SHA256 for verification). "
            "Integration tests will skip in the meantime."
        )
        return 0
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"downloading {FIXTURE_URL} → {target}")
    urllib.request.urlretrieve(FIXTURE_URL, str(target))
    if EXPECTED_SHA256:
        h = hashlib.sha256(target.read_bytes()).hexdigest()
        if h != EXPECTED_SHA256:
            target.unlink()
            print(f"sha256 mismatch: got {h}, expected {EXPECTED_SHA256}", file=sys.stderr)
            return 1
    print(f"fetched: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Add fixture path to `.gitignore`.**

```bash
echo "tests/audio/fixtures/*.mp4" >> .gitignore
```

### Task 2.3 — Segmenter (TDD, unit-test pure-Python parser)

- [ ] **Step 1: Read the source.** `pythonAudioA11y/audio_processor.py` is the reference. Pay particular attention to:
  - `detect_silence_points(audio_path)` — parses ffmpeg `-af silencedetect` stderr output.
  - `split_audio(input_path, target_segment_seconds=600)` — picks split points near each multiple of `target_segment_seconds` from the silence list.
  - The segments-JSON shape it writes.

- [ ] **Step 2: Write `tests/audio/test_segmenter.py`** with pure-Python tests against fake silence-detection output:

```python
"""Unit tests for audio/segmenter.py — pure-Python helpers.

Integration tests against real ffmpeg live below the @pytest.mark.ffmpeg
marker and skip if no fixture MP4 exists.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from auto_a11y.audio.segmenter import (
    Segment,
    SilencePoint,
    parse_silencedetect_output,
    pick_split_points,
)


def test_parse_silencedetect_output() -> None:
    # Real ffmpeg stderr fragment — each silence is emitted as a pair of lines.
    raw = (
        "[silencedetect @ 0x...] silence_start: 5.234\n"
        "[silencedetect @ 0x...] silence_end: 5.890 | silence_duration: 0.656\n"
        "[silencedetect @ 0x...] silence_start: 120.001\n"
        "[silencedetect @ 0x...] silence_end: 120.500 | silence_duration: 0.499\n"
    )
    points = parse_silencedetect_output(raw)
    assert points == [
        SilencePoint(start=5.234, end=5.890, duration=0.656),
        SilencePoint(start=120.001, end=120.500, duration=0.499),
    ]


def test_pick_split_points_uses_silence_near_target() -> None:
    # Target 600 s; silences at 597, 1205, 1799 — should pick each closest.
    silences = [
        SilencePoint(start=200.0, end=200.5, duration=0.5),
        SilencePoint(start=597.0, end=597.7, duration=0.7),
        SilencePoint(start=1205.0, end=1205.4, duration=0.4),
        SilencePoint(start=1799.0, end=1799.8, duration=0.8),
    ]
    splits = pick_split_points(total_duration=2400.0, silences=silences, target_s=600.0, window_s=30.0)
    assert splits == [597.7, 1205.4, 1799.8]


def test_pick_split_points_falls_back_to_target_when_no_silence_in_window() -> None:
    silences = [SilencePoint(start=10.0, end=10.5, duration=0.5)]  # nowhere near 600
    splits = pick_split_points(total_duration=1200.0, silences=silences, target_s=600.0, window_s=30.0)
    # Hard split at exactly target_s when no silence is found.
    assert splits == [600.0]


def test_segments_from_splits() -> None:
    from auto_a11y.audio.segmenter import segments_from_splits
    segments = segments_from_splits(total_duration=1500.0, splits=[600.0, 1200.0])
    assert segments == [
        Segment(index=0, start_s=0.0, end_s=600.0),
        Segment(index=1, start_s=600.0, end_s=1200.0),
        Segment(index=2, start_s=1200.0, end_s=1500.0),
    ]
```

- [ ] **Step 3: Run — expect failure.**

```bash
pytest tests/audio/test_segmenter.py -v
# Expected: ModuleNotFoundError
```

- [ ] **Step 4: Create `auto_a11y/audio/segmenter.py`.** Pure-Python helpers + an `extract_and_split(input_mp4, slot)` that shells out to ffmpeg.

```python
"""Silence-aware audio extraction and segmentation.

Ported from pythonAudioA11y/audio_processor.py. The pure-Python pieces
(parse_silencedetect_output, pick_split_points, segments_from_splits)
are unit-tested in isolation; extract_and_split touches ffmpeg and is
covered by the @pytest.mark.ffmpeg integration tests.
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from auto_a11y.audio.errors import FfmpegMissing
from auto_a11y.audio.ffmpeg import detect_ffmpeg, detect_ffprobe
from auto_a11y.audio.storage import AllocatedSlot


@dataclass(frozen=True)
class SilencePoint:
    start: float
    end: float
    duration: float


@dataclass(frozen=True)
class Segment:
    index: int
    start_s: float
    end_s: float

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s


_SILENCE_START = re.compile(r"silence_start:\s*([\d.]+)")
_SILENCE_END = re.compile(r"silence_end:\s*([\d.]+)\s*\|\s*silence_duration:\s*([\d.]+)")


def parse_silencedetect_output(stderr: str) -> list[SilencePoint]:
    """Pair up `silence_start` / `silence_end` lines into SilencePoint records."""
    starts: list[float] = []
    points: list[SilencePoint] = []
    for line in stderr.splitlines():
        m_start = _SILENCE_START.search(line)
        if m_start:
            starts.append(float(m_start.group(1)))
            continue
        m_end = _SILENCE_END.search(line)
        if m_end and starts:
            start = starts.pop(0)
            points.append(SilencePoint(start=start, end=float(m_end.group(1)),
                                        duration=float(m_end.group(2))))
    return points


def pick_split_points(
    *,
    total_duration: float,
    silences: list[SilencePoint],
    target_s: float = 600.0,
    window_s: float = 30.0,
) -> list[float]:
    """Pick split points near each multiple of `target_s`.

    Falls back to a hard split at the target if no silence is found
    within ±window_s. Returns the list of split timestamps (using the
    end of each chosen silence so the *next* segment starts on speech).
    """
    splits: list[float] = []
    cursor = target_s
    while cursor < total_duration:
        # Search for the silence with end closest to cursor and within window.
        candidates = [s for s in silences
                      if abs(s.end - cursor) <= window_s and s.end < total_duration]
        if candidates:
            best = min(candidates, key=lambda s: abs(s.end - cursor))
            splits.append(best.end)
            cursor = best.end + target_s
        else:
            splits.append(cursor)
            cursor += target_s
    return splits


def segments_from_splits(*, total_duration: float, splits: list[float]) -> list[Segment]:
    """Convert a sorted list of split timestamps into Segment records."""
    out: list[Segment] = []
    last = 0.0
    for i, s in enumerate(splits):
        out.append(Segment(index=i, start_s=last, end_s=s))
        last = s
    out.append(Segment(index=len(splits), start_s=last, end_s=total_duration))
    return out


def probe_duration(input_mp4: Path) -> float:
    """Use ffprobe to return the source's duration in seconds."""
    ffprobe = detect_ffprobe(raise_if_missing=True)
    assert ffprobe is not None  # raise_if_missing=True guarantees non-None
    out = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(input_mp4)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def detect_silences(input_mp4: Path, *, noise_db: int = -30, min_silence_s: float = 0.4) -> list[SilencePoint]:
    """Run ffmpeg with -af silencedetect and parse the stderr output."""
    ffmpeg = detect_ffmpeg(raise_if_missing=True)
    assert ffmpeg is not None
    proc = subprocess.run(
        [ffmpeg, "-i", str(input_mp4), "-af",
         f"silencedetect=noise={noise_db}dB:d={min_silence_s}",
         "-f", "null", "-"],
        capture_output=True, text=True, check=False,
    )
    return parse_silencedetect_output(proc.stderr)


def extract_segment(
    *, input_mp4: Path, slot: AllocatedSlot, segment: Segment
) -> Path:
    """Extract one segment as an m4a, using ffmpeg's copy codec where possible."""
    ffmpeg = detect_ffmpeg(raise_if_missing=True)
    assert ffmpeg is not None
    out = slot.segment_m4a(segment.index)
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [ffmpeg, "-y", "-i", str(input_mp4),
         "-ss", f"{segment.start_s:.3f}",
         "-to", f"{segment.end_s:.3f}",
         "-vn", "-c:a", "aac", "-b:a", "128k",
         str(out)],
        capture_output=True, check=True,
    )
    return out


def split(input_mp4: Path, slot: AllocatedSlot, *, target_s: float = 600.0) -> list[Segment]:
    """Main entry point: silence-detect → pick splits → extract segments → write manifest."""
    duration = probe_duration(input_mp4)
    silences = detect_silences(input_mp4)
    splits_at = pick_split_points(total_duration=duration, silences=silences, target_s=target_s)
    segments = segments_from_splits(total_duration=duration, splits=splits_at)

    for seg in segments:
        extract_segment(input_mp4=input_mp4, slot=slot, segment=seg)

    manifest = slot.audio_dir / "segments.json"
    manifest.write_text(json.dumps([asdict(s) for s in segments], indent=2))
    return segments
```

- [ ] **Step 5: Run unit tests.**

```bash
pytest tests/audio/test_segmenter.py -v
# Expected: 4 PASSED (the pure-Python ones)
```

### Task 2.4 — Integration test against real ffmpeg

- [ ] **Step 1: Append to `tests/audio/test_segmenter.py`:**

```python
@pytest.mark.ffmpeg
def test_split_against_real_fixture(tmp_path: Path) -> None:
    """End-to-end: real ffmpeg, fixture MP4 (~30 s), verify segments produced."""
    fixture = Path(__file__).parent / "fixtures" / "short_audit.mp4"
    if not fixture.exists():
        pytest.skip(f"fixture not present: {fixture} (run scripts/fetch_audio_fixtures.py)")

    from auto_a11y.audio.segmenter import split
    from auto_a11y.audio.storage import AudioStorage

    storage = AudioStorage(root=tmp_path / "recordings")
    slot = storage.allocate("REC-20260519143022-a1b2c3")

    # Force a short target so a 30 s fixture produces multiple segments.
    segments = split(fixture, slot, target_s=10.0)
    assert len(segments) >= 2
    assert slot.segment_m4a(0).is_file()
    # Manifest written and parseable.
    manifest = json.loads((slot.audio_dir / "segments.json").read_text())
    assert manifest[0]["index"] == 0
```

- [ ] **Step 2: Confirm the marker is registered in `pyproject.toml`** under `[tool.pytest.ini_options] markers`. If absent, add:

```toml
markers = [
    "ffmpeg: integration tests that shell out to ffmpeg",
    "deepgram: integration tests that hit Deepgram",
    "anthropic: integration tests that hit Anthropic",
    "slow: end-to-end tests (mp4 → JSON)",
    "network: requires network access",
]
```

- [ ] **Step 3: Run** — if no fixture is present, the test skips cleanly:

```bash
pytest tests/audio/test_segmenter.py -v -m "ffmpeg or not ffmpeg"
# Expected: 4 PASSED + 1 SKIPPED (fixture absent) OR + 1 PASSED (fixture present)
```

### Task 2.5 — Commit Phase 2

```bash
source .venv/bin/activate
git add auto_a11y/audio/segmenter.py tests/audio/test_segmenter.py \
        scripts/fetch_audio_fixtures.py .gitignore requirements.txt \
        pyproject.toml
git commit --no-gpg-sign -m "$(cat <<'EOF'
audio: ffmpeg-based silence-aware segmentation

Port of pythonAudioA11y/audio_processor.py:
- parse_silencedetect_output  — parse ffmpeg -af silencedetect stderr
- pick_split_points           — pick the silence nearest each multiple
                                of target_s (default 600 s, ±30 s window),
                                fall back to a hard split if none
- segments_from_splits        — turn split timestamps into Segment records
- probe_duration              — ffprobe wrapper
- detect_silences             — ffmpeg wrapper
- extract_segment             — per-segment ffmpeg call (m4a, 128 kbps)
- split                       — orchestrator; writes segments.json manifest

Unit tests are pure-Python (no real ffmpeg). The end-to-end test is
marked @pytest.mark.ffmpeg and skips when no fixture MP4 is present.

Adds ffmpeg-python==0.2.0 to requirements.txt (already used by
pythonAudioA11y; we depend on the subprocess interface for now and may
swap to the fluent API later).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase 3 — Deepgram transcription + VTT processing

**Goal:** Port `pythonAudioA11y/transcription.py` (Deepgram client + retry + VTT generation) and `vtt_processor.py` (segment merge + word-level metadata) into `auto_a11y/audio/transcription.py` and `vtt_processor.py`. Default model is **nova-3** (deliberate upgrade from source's nova-2 — see spec note).

**Files:**
- Create: `auto_a11y/audio/transcription.py`
- Create: `auto_a11y/audio/vtt_processor.py`
- Create: `tests/audio/test_transcription.py`
- Create: `tests/audio/test_vtt_processor.py`
- Modify: `requirements.txt` — add `deepgram-sdk>=5.3.0`, `webvtt-py>=0.4.6`
- Modify: `stubs/deepgram/__init__.pyi` (new) — minimal stub if `deepgram-sdk` lacks `py.typed`

### Task 3.1 — Add Deepgram + webvtt deps

- [ ] **Step 1: Check `deepgram-sdk` for `py.typed`.**

```bash
.venv/bin/pip install deepgram-sdk==5.3.0 webvtt-py==0.4.6
find .venv/lib/python*/site-packages/deepgram -name "py.typed" 2>/dev/null
```

- [ ] **Step 2: If missing**, write a minimal stub at `stubs/deepgram/__init__.pyi`:

```python
"""Minimal stub for deepgram-sdk 5.3.x — only what we import.

The v5 SDK exposes the transcribe-file call as
    client.listen.v1.media.transcribe_file(request=..., model=..., ...)
NOT the v3-style client.listen.rest.v("1") chain. PrerecordedOptions
is a v3 concept and is NOT used in v5 (kwargs are passed directly).
See pythonAudioA11y/transcription.py:36-46 for the canonical call shape.
"""
from __future__ import annotations
from typing import Any


class DeepgramClient:
    def __init__(self, api_key: str) -> None: ...
    @property
    def listen(self) -> Any: ...  # actual: .v1.media.transcribe_file(...)
```

- [ ] **Step 3: Add to `requirements.txt`.**

- [ ] **Step 4: Run pyright** — confirm imports resolve:

```bash
.venv/bin/python -m pyright auto_a11y/audio/ 2>&1 | tail -3
# Expected: 0 errors
```

### Task 3.2 — Transcription module (TDD, mocked SDK)

- [ ] **Step 1: Write `tests/audio/test_transcription.py`** mocking the SDK:

```python
"""Tests for audio/transcription.py.

Live Deepgram tests live in test_transcription_live.py (marked
@pytest.mark.deepgram, skipped without DEEPGRAM_API_KEY).
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from auto_a11y.audio.errors import TranscriptionError
from auto_a11y.audio.transcription import Transcriber, TranscriptionResult


def _fake_deepgram_response() -> object:
    """Build a fake response object matching the v5 SDK's PrerecordedResponse shape.

    The v5 SDK returns a typed object accessed via attributes
    (response.results.channels[0].alternatives[0].transcript / .words).
    """
    word_a = MagicMock(start=0.0, end=0.5, speaker=0, punctuated_word="Hello", word="Hello")
    word_b = MagicMock(start=0.6, end=1.1, speaker=0, punctuated_word="world", word="world")
    alt = MagicMock(transcript="Hello world", words=[word_a, word_b])
    channel = MagicMock(alternatives=[alt])
    results = MagicMock(channels=[channel])
    return MagicMock(results=results)


def test_transcribe_returns_words_and_transcript() -> None:
    sdk = MagicMock()
    sdk.listen.v1.media.transcribe_file.return_value = _fake_deepgram_response()
    transcriber = Transcriber(client=sdk, model="nova-3")

    result = transcriber.transcribe(Path("/tmp/fake.m4a"))

    assert isinstance(result, TranscriptionResult)
    assert result.transcript == "Hello world"
    assert len(result.words) == 2
    assert result.words[0].start == 0.0
    assert result.words[0].speaker == 0


def test_transcribe_retries_on_5xx(monkeypatch: pytest.MonkeyPatch) -> None:
    sdk = MagicMock()
    transcribe_file = sdk.listen.v1.media.transcribe_file
    # Two failures, then success
    transcribe_file.side_effect = [
        RuntimeError("Deepgram 503"),
        RuntimeError("Deepgram 503"),
        _fake_deepgram_response(),
    ]
    monkeypatch.setattr("time.sleep", lambda _: None)  # no real sleep in tests

    transcriber = Transcriber(client=sdk, model="nova-3", max_retries=3)
    result = transcriber.transcribe(Path("/tmp/fake.m4a"))
    assert result.transcript == "Hello world"
    assert transcribe_file.call_count == 3


def test_transcribe_fails_after_max_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    sdk = MagicMock()
    sdk.listen.v1.media.transcribe_file.side_effect = RuntimeError("Deepgram 503")
    monkeypatch.setattr("time.sleep", lambda _: None)
    transcriber = Transcriber(client=sdk, model="nova-3", max_retries=3)
    with pytest.raises(TranscriptionError):
        transcriber.transcribe(Path("/tmp/fake.m4a"))


def test_to_vtt_produces_well_formed_output(tmp_path: Path) -> None:
    result = TranscriptionResult(
        transcript="Hello world",
        words=[
            # `Word` is the dataclass exported from auto_a11y.audio.transcription
        ],
    )
    # …reflect the public to_vtt() API; flesh out once the module is written
```

- [ ] **Step 2: Create `auto_a11y/audio/transcription.py`.**

```python
"""Deepgram-based per-segment transcription.

Ported (and upgraded) from pythonAudioA11y/transcription.py:
- Deepgram model upgraded from nova-2 to nova-3 (spec decision).
- Retry loop: 1 s → 2 s → 4 s (matches source).
- Output includes per-word timing + speaker label for downstream diarization.

Uses the deepgram-sdk v5 call shape:
    response = client.listen.v1.media.transcribe_file(
        request=audio_buffer,
        model="nova-3",
        smart_format=True,
        diarize=True,
        ...
    )
Response is a typed object accessed via attributes
(response.results.channels[0].alternatives[0].transcript / .words),
NOT a dict. The v3-style client.listen.rest.v("1") chain and
PrerecordedOptions wrapper do not exist in v5.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from auto_a11y.audio.errors import TranscriptionError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Word:
    text: str            # punctuated_word from Deepgram
    start: float
    end: float
    speaker: int


@dataclass(frozen=True)
class TranscriptionResult:
    transcript: str
    words: list[Word]


class Transcriber:
    def __init__(self, *, client: Any, model: str = "nova-3", max_retries: int = 3,
                 language: str = "en", timeout_min_seconds: int = 300) -> None:
        self._client = client
        self._model = model
        self._max_retries = max_retries
        self._language = language
        self._timeout_min_seconds = timeout_min_seconds

    def transcribe(self, audio_path: Path) -> TranscriptionResult:
        """Transcribe one audio segment with diarization + smart formatting."""
        file_size_mb = audio_path.stat().st_size / (1024 * 1024)
        # Mirror pythonAudioA11y's dynamic timeout: max(300s, file_size_mb * 60).
        timeout_seconds = max(self._timeout_min_seconds, int(file_size_mb * 60))

        backoff = 1.0
        last_exc: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                with open(audio_path, "rb") as fp:
                    audio_buffer = fp.read()
                response = self._client.listen.v1.media.transcribe_file(
                    request=audio_buffer,
                    model=self._model,
                    language=self._language,
                    smart_format=True,
                    diarize=True,
                    punctuate=True,
                    paragraphs=True,
                    utterances=True,
                    request_options={"timeout_in_seconds": timeout_seconds},
                )
                return self._parse(response)
            except Exception as e:
                last_exc = e
                logger.warning("Deepgram attempt %d failed: %s", attempt + 1, e)
                if attempt < self._max_retries - 1:
                    time.sleep(backoff)
                    backoff *= 2
        raise TranscriptionError(f"Deepgram failed after {self._max_retries} attempts") from last_exc

    @staticmethod
    def _parse(response: Any) -> TranscriptionResult:
        """Parse the v5 SDK's typed response object into our internal records."""
        try:
            alt = response.results.channels[0].alternatives[0]
            transcript = str(getattr(alt, "transcript", ""))
            words_attr = getattr(alt, "words", []) or []
            words = [
                Word(
                    text=str(getattr(w, "punctuated_word", None) or getattr(w, "word", "")),
                    start=float(w.start),
                    end=float(w.end),
                    speaker=int(getattr(w, "speaker", 0) or 0),
                )
                for w in words_attr
            ]
            return TranscriptionResult(transcript=transcript, words=words)
        except (AttributeError, IndexError, TypeError, ValueError) as e:
            raise TranscriptionError(f"Malformed Deepgram response: {e}") from e


def words_to_vtt(words: list[Word], *, offset_s: float = 0.0) -> str:
    """Render a list of Word records as a WebVTT string.

    offset_s is added to every timestamp (used when stitching segments).
    """
    out: list[str] = ["WEBVTT", ""]
    # Group adjacent same-speaker words into cues. A "cue" is a single
    # speaker turn. Each cue's text is the joined .text values.
    if not words:
        return "\n".join(out)
    current_speaker = words[0].speaker
    cue_words: list[Word] = []

    def flush() -> None:
        if not cue_words:
            return
        start = _fmt_ts(cue_words[0].start + offset_s)
        end = _fmt_ts(cue_words[-1].end + offset_s)
        text = " ".join(w.text for w in cue_words)
        out.append(f"{start} --> {end}")
        out.append(f"<v Speaker_{current_speaker}>{text}")
        out.append("")

    for w in words:
        if w.speaker != current_speaker:
            flush()
            cue_words = [w]
            current_speaker = w.speaker
        else:
            cue_words.append(w)
    flush()
    return "\n".join(out)


def _fmt_ts(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def write_words_json(words: list[Word], path: Path) -> None:
    """Persist words to a `.words` file alongside the VTT (for downstream diarization)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([
        {"text": w.text, "start": w.start, "end": w.end, "speaker": w.speaker}
        for w in words
    ], indent=2))
```

- [ ] **Step 3: Run** unit tests.

```bash
pytest tests/audio/test_transcription.py -v
# Expected: 4 PASSED
```

### Task 3.3 — VTT processor (TDD)

- [ ] **Step 1: Write `tests/audio/test_vtt_processor.py`.**

```python
"""Tests for audio/vtt_processor.py — merging per-segment VTTs."""
from __future__ import annotations

from pathlib import Path

import pytest

from auto_a11y.audio.vtt_processor import merge_segment_vtts


def test_merge_offsets_timestamps_per_segment(tmp_path: Path) -> None:
    s0 = tmp_path / "segment-0.vtt"
    s1 = tmp_path / "segment-1.vtt"
    s0.write_text(
        "WEBVTT\n\n"
        "00:00:00.000 --> 00:00:05.000\n"
        "<v Speaker_0>Hello world\n\n"
    )
    s1.write_text(
        "WEBVTT\n\n"
        "00:00:01.000 --> 00:00:04.000\n"
        "<v Speaker_0>Second segment\n\n"
    )
    out = tmp_path / "merged.vtt"
    merge_segment_vtts(
        segment_paths=[s0, s1],
        segment_offsets_s=[0.0, 600.0],
        output=out,
    )
    txt = out.read_text()
    assert "WEBVTT" in txt
    assert "00:00:00.000 --> 00:00:05.000" in txt
    assert "00:10:01.000 --> 00:10:04.000" in txt  # offset by 600 s
    assert "Hello world" in txt
    assert "Second segment" in txt
```

- [ ] **Step 2: Create `auto_a11y/audio/vtt_processor.py`.**

```python
"""Merge per-segment VTT files into a single VTT with corrected timestamps.

Ported from pythonAudioA11y/vtt_processor.py — pure Python, no media calls.
"""
from __future__ import annotations

import re
from pathlib import Path


_CUE_TS = re.compile(r"^(\d{2}:\d{2}:\d{2}\.\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}\.\d{3})")


def _ts_to_seconds(ts: str) -> float:
    h, m, rest = ts.split(":")
    return int(h) * 3600 + int(m) * 60 + float(rest)


def _seconds_to_ts(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def merge_segment_vtts(
    *, segment_paths: list[Path], segment_offsets_s: list[float], output: Path
) -> None:
    """Concatenate segment VTTs into one, adding the segment offset to every timestamp."""
    if len(segment_paths) != len(segment_offsets_s):
        raise ValueError("segment_paths and segment_offsets_s must align")

    lines: list[str] = ["WEBVTT", ""]
    for path, offset in zip(segment_paths, segment_offsets_s):
        text = path.read_text()
        for line in text.splitlines():
            m = _CUE_TS.match(line)
            if m:
                start = _ts_to_seconds(m.group(1)) + offset
                end = _ts_to_seconds(m.group(2)) + offset
                lines.append(f"{_seconds_to_ts(start)} --> {_seconds_to_ts(end)}")
            elif line.strip().upper() == "WEBVTT":
                continue  # already emitted once at the top
            else:
                lines.append(line)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines))
```

- [ ] **Step 3: Run** the VTT tests.

```bash
pytest tests/audio/test_vtt_processor.py -v
# Expected: 1 PASSED
```

### Task 3.4 — Commit Phase 3

```bash
source .venv/bin/activate
git add auto_a11y/audio/transcription.py auto_a11y/audio/vtt_processor.py \
        tests/audio/test_transcription.py tests/audio/test_vtt_processor.py \
        requirements.txt stubs/deepgram/ 2>/dev/null
git commit --no-gpg-sign -m "$(cat <<'EOF'
audio: Deepgram transcription + VTT segment merging

auto_a11y/audio/transcription.py:
- Transcriber.transcribe(audio_path) — Deepgram nova-3 (upgraded from
  pythonAudioA11y's nova-2 per spec), diarization on, smart-format on,
  retry 1 s → 2 s → 4 s on transient failures. Uses the deepgram-sdk
  v5 call shape: client.listen.v1.media.transcribe_file(request=...,
  model=..., smart_format=True, diarize=True, ...). Response is a typed
  object (response.results.channels[0].alternatives[0].transcript /
  .words), NOT a dict. Mirrors pythonAudioA11y/transcription.py:36-46.
- words_to_vtt(words, offset_s) — render Word records as WebVTT.
- write_words_json(words, path) — persist per-word timing for
  downstream speaker remap.

auto_a11y/audio/vtt_processor.py:
- merge_segment_vtts(segment_paths, segment_offsets_s, output) —
  concatenate per-segment VTTs with corrected absolute timestamps.

Deps:
- deepgram-sdk==5.3.0
- webvtt-py==0.4.6
- Minimal stubs/deepgram/__init__.pyi if the SDK lacks py.typed.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase 4 — Speaker remap (pyannote.audio + torch)

**Goal:** Port `pythonAudioA11y/speaker_identification.py` into `auto_a11y/audio/speaker_identification.py`. Uses `pyannote.audio` for embeddings + `scikit-learn` for agglomerative clustering at 0.7 cosine threshold. Behind a `--skip-speaker-remap` short-circuit. Adds `torch`, `torchaudio`, `pyannote.audio`, `scikit-learn` to `requirements.txt`. Optional `HF_TOKEN`.

**Files:**
- Create: `auto_a11y/audio/speaker_identification.py`
- Create: `tests/audio/test_speaker_identification.py`
- Modify: `requirements.txt` — add `torch>=2.0.0`, `torchaudio>=2.0.0`, `pyannote.audio>=3.1.0`, `scikit-learn>=1.3.0`
- Modify: `stubs/` — add stubs if upstream packages lack `py.typed` (verify each)

### Task 4.1 — Add heavy deps

- [ ] **Step 1: Install in venv.** This takes a while (multi-GB torch download).

```bash
.venv/bin/pip install torch>=2.0.0 torchaudio>=2.0.0 pyannote.audio>=3.1.0 scikit-learn>=1.3.0
```

- [ ] **Step 2: Confirm py.typed presence for each.**

```bash
for pkg in torch torchaudio pyannote sklearn; do
    find .venv/lib/python*/site-packages/$pkg -name "py.typed" 2>/dev/null | head -1
done
```

- [ ] **Step 3: Write minimal stubs** for any package without `py.typed`. Most likely `pyannote.audio` (the others have py.typed in recent versions).

- [ ] **Step 4: Append to requirements.txt** alphabetically.

- [ ] **Step 5: Verify pyright** is clean against the new imports:

```bash
.venv/bin/python -m pyright auto_a11y/ 2>&1 | tail -3
```

### Task 4.2 — Speaker identification (TDD against synthetic embeddings)

- [ ] **Step 1: Write `tests/audio/test_speaker_identification.py`** with synthetic embedding fixtures (no pyannote model loaded):

```python
"""Unit tests for the clustering logic in speaker_identification.

The pyannote embedding extraction is mocked. Real-audio tests live
behind @pytest.mark.slow.
"""
from __future__ import annotations

import numpy as np
import pytest

from auto_a11y.audio.speaker_identification import (
    SpeakerMapping,
    cluster_embeddings,
    remap_speaker_ids_in_vtt,
)


def test_cluster_embeddings_groups_similar_vectors() -> None:
    # Three vectors: two near identical, one orthogonal.
    embeddings = np.array([
        [1.0, 0.0, 0.0],
        [1.0, 0.01, 0.0],
        [0.0, 1.0, 0.0],
    ])
    labels = cluster_embeddings(embeddings, cosine_threshold=0.7)
    assert labels[0] == labels[1]
    assert labels[0] != labels[2]


def test_remap_uses_mapping() -> None:
    mapping = SpeakerMapping({
        "Segment_0_Speaker_0": 0,
        "Segment_0_Speaker_1": 1,
        "Segment_1_Speaker_0": 1,  # different segment, same global speaker
        "Segment_1_Speaker_1": 0,
    })
    vtt = (
        "WEBVTT\n\n"
        "00:00:00.000 --> 00:00:01.000\n"
        "<v Segment_0_Speaker_0>Hi\n\n"
        "00:00:10.000 --> 00:00:11.000\n"
        "<v Segment_1_Speaker_1>Hi back\n\n"
    )
    remapped = remap_speaker_ids_in_vtt(vtt, mapping)
    assert "<v Speaker_0>Hi" in remapped
    assert "<v Speaker_0>Hi back" in remapped  # remapped via segment 1 speaker 1 → 0
```

- [ ] **Step 2: Create `auto_a11y/audio/speaker_identification.py`.**

```python
"""Cross-segment speaker remapping.

Ported from pythonAudioA11y/speaker_identification.py:
- Extract one embedding per (segment, speaker) using pyannote.audio.
- Cluster embeddings with sklearn AgglomerativeClustering (cosine, 0.7).
- Replace per-segment speaker labels with global labels in the merged VTT.

Heavy dependencies (torch, pyannote.audio, scikit-learn) are imported
LAZILY so the rest of the audio package doesn't pay the import cost
when speaker_remap is disabled.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SpeakerMapping:
    mapping: dict[str, int]
    """Maps Segment_<i>_Speaker_<j> → global_speaker_id (int)."""

    def to_dict(self) -> dict[str, dict[str, int]]:
        return {"speakers": dict(self.mapping)}

    @classmethod
    def from_path(cls, path: Path) -> SpeakerMapping:
        data = json.loads(path.read_text())
        return cls(mapping={k: int(v) for k, v in data["speakers"].items()})


def cluster_embeddings(embeddings: Any, *, cosine_threshold: float = 0.7) -> list[int]:
    """Agglomerative clustering on cosine distance. Returns one label per row."""
    from sklearn.cluster import AgglomerativeClustering  # lazy import

    if len(embeddings) == 1:
        return [0]
    model = AgglomerativeClustering(
        n_clusters=None,
        metric="cosine",
        linkage="average",
        distance_threshold=1.0 - cosine_threshold,
    )
    labels: Any = model.fit_predict(embeddings)
    return [int(x) for x in labels]


_VOICE_TAG = re.compile(r"<v\s+([^>]+)>")


def remap_speaker_ids_in_vtt(vtt: str, mapping: SpeakerMapping) -> str:
    """Replace `<v Segment_i_Speaker_j>` with `<v Speaker_<global>>`."""
    def sub(m: re.Match[str]) -> str:
        old = m.group(1).strip()
        if old in mapping.mapping:
            return f"<v Speaker_{mapping.mapping[old]}>"
        return m.group(0)  # unchanged if not in mapping
    return _VOICE_TAG.sub(sub, vtt)


def extract_speaker_embeddings(
    *, segment_audio_paths: list[Path], words_paths: list[Path], hf_token: str | None = None
) -> tuple[list[str], Any]:
    """Compute one embedding vector per (segment, speaker).

    Returns (labels, embeddings) where labels[i] = "Segment_<seg>_Speaker_<spk>"
    and embeddings is an (N, D) numpy array.
    """
    import numpy as np
    from pyannote.audio import Model  # lazy import — multi-GB model load

    model = Model.from_pretrained("pyannote/embedding", use_auth_token=hf_token)
    labels: list[str] = []
    vectors: list[Any] = []
    for seg_idx, (audio_path, words_path) in enumerate(zip(segment_audio_paths, words_paths)):
        if not audio_path.exists() or not words_path.exists():
            continue
        words = json.loads(words_path.read_text())
        speakers_in_seg = sorted({int(w["speaker"]) for w in words})
        for spk in speakers_in_seg:
            # Aggregate the timestamps of this speaker into a clip and embed.
            # Implementation detail elided here; see pythonAudioA11y/speaker_identification.py
            # for the per-speaker audio-snippet extraction logic.
            vec = _embed_speaker_audio(model, audio_path, words, spk)
            labels.append(f"Segment_{seg_idx}_Speaker_{spk}")
            vectors.append(vec)
    return labels, np.array(vectors)


def _embed_speaker_audio(model: Any, audio_path: Path, words: list[dict[str, Any]], speaker: int) -> Any:
    """Extract the audio for one speaker in one segment, return the embedding vector."""
    # See pythonAudioA11y/speaker_identification.py for the full implementation.
    # This is the model-specific embedding step; out of scope for unit tests.
    raise NotImplementedError("port from pythonAudioA11y/speaker_identification.py")


def build_mapping(
    *, segment_audio_paths: list[Path], words_paths: list[Path],
    cosine_threshold: float = 0.7, hf_token: str | None = None,
) -> SpeakerMapping:
    """End-to-end: extract embeddings, cluster, return SpeakerMapping."""
    labels, embeddings = extract_speaker_embeddings(
        segment_audio_paths=segment_audio_paths, words_paths=words_paths, hf_token=hf_token,
    )
    if len(labels) == 0:
        return SpeakerMapping(mapping={})
    cluster_labels = cluster_embeddings(embeddings, cosine_threshold=cosine_threshold)
    return SpeakerMapping(mapping=dict(zip(labels, cluster_labels)))
```

- [ ] **Step 3: Note the `NotImplementedError`** in `_embed_speaker_audio`. The exact embedding extraction (pyannote crops + averaging) is large; deferred to the implementation pass. Add a `TODO_PHASE4` marker that the planner can grep for. Real-audio integration tests against the marker stay skipped until this is filled in.

- [ ] **Step 4: Run unit tests** — they should pass because they don't touch the embedding step:

```bash
pytest tests/audio/test_speaker_identification.py -v
# Expected: 2 PASSED
```

### Task 4.3 — Commit Phase 4

```bash
source .venv/bin/activate
git add auto_a11y/audio/speaker_identification.py \
        tests/audio/test_speaker_identification.py \
        requirements.txt stubs/ 2>/dev/null
git commit --no-gpg-sign -m "$(cat <<'EOF'
audio: speaker remap via pyannote.audio + sklearn agglomerative clustering

Port (partial) of pythonAudioA11y/speaker_identification.py:
- cluster_embeddings(embeddings, cosine_threshold=0.7)
- remap_speaker_ids_in_vtt(vtt, mapping)
- SpeakerMapping {to/from} JSON
- build_mapping(...) — end-to-end
- _embed_speaker_audio(...) — NotImplementedError placeholder; the
  exact pyannote crop+average logic gets filled in during the
  pipeline-integration pass. Marked TODO_PHASE4.

Heavy deps (torch, torchaudio, pyannote.audio, scikit-learn) added to
requirements.txt. Imports are lazy inside the helpers so the rest of
the package doesn't pay the import cost when speaker_remap is off.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase 5 — Claude analysis + prompts + cost

**Goal:** Port `pythonAudioA11y/analysis.py` (4 passes × 3 contexts × 2 languages) into `auto_a11y/audio/analysis.py`. Prompts move to standalone `auto_a11y/audio/prompts/*.txt` files, snapshot-tested. `auto_a11y/audio/cost.py` provides the pricing calculator with current Opus 4.7 + nova-3 rates.

**Files:**
- Create: `auto_a11y/audio/analysis.py`
- Create: `auto_a11y/audio/cost.py`
- Create: `auto_a11y/audio/prompts/{audit,livedExperience,navilens}_{issues,painpoints,takeaways,assertions}.txt` (12 files)
- Create: `auto_a11y/audio/prompts/heuristics.txt`
- Create: `auto_a11y/audio/prompts/__init__.py`
- Create: `tests/audio/test_analysis.py`
- Create: `tests/audio/test_cost.py`
- Create: `tests/audio/test_prompts.py` (snapshot tests)
- Create: `tests/audio/snapshots/prompts.json` (hashes captured at commit time)

### Task 5.1 — Port the prompt text files

- [ ] **Step 1: Read each prompt function** in `pythonAudioA11y/analysis.py` to extract the prompt text. Look for `_get_*_prompt()` functions and the inline strings around lines 35-120 (audit/livedExperience) and 706-785 (navilens).

- [ ] **Step 2: Create each prompt as a separate file.** Naming: `{context}_{kind}.txt` where context ∈ {audit, livedExperience, navilens}, kind ∈ {issues, painpoints, takeaways, assertions}. End every file with the literal marker:

```
END_OF_PROMPT
```

(The snapshot test enforces this marker exists.)

- [ ] **Step 3: Create `auto_a11y/audio/prompts/heuristics.txt`** with the shared heuristics block (originally in `audioA11y/src/audioA11y.js:22-34` and `pythonAudioA11y/analysis.py`).

- [ ] **Step 4: Create `auto_a11y/audio/prompts/__init__.py`** with a loader:

```python
"""Prompt-file loader. Pure read; no template engine; prompts ARE the input."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

_PROMPT_DIR = Path(__file__).resolve().parent


Context = Literal["audit", "livedExperience", "navilens"]
Kind = Literal["issues", "painpoints", "takeaways", "assertions"]


def load(context: Context, kind: Kind) -> str:
    path = _PROMPT_DIR / f"{context}_{kind}.txt"
    return path.read_text()


def load_heuristics() -> str:
    return (_PROMPT_DIR / "heuristics.txt").read_text()
```

### Task 5.2 — Snapshot test the prompts

- [ ] **Step 1: Write `tests/audio/test_prompts.py`.**

```python
"""Snapshot tests for prompt files.

Each prompt is asserted to end with the END_OF_PROMPT marker and its
SHA256 must match the hash committed in snapshots/prompts.json. This
catches accidental edits during dependency upgrades or refactors.

Regenerate with: pytest --snapshot-update tests/audio/test_prompts.py
"""
from __future__ import annotations

import hashlib
import json
from itertools import product
from pathlib import Path

import pytest


_PROMPT_DIR = Path(__file__).resolve().parent.parent.parent / "auto_a11y" / "audio" / "prompts"
_SNAPSHOTS = Path(__file__).resolve().parent / "snapshots" / "prompts.json"


CONTEXTS = ("audit", "livedExperience", "navilens")
KINDS = ("issues", "painpoints", "takeaways", "assertions")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("context,kind", list(product(CONTEXTS, KINDS)))
def test_prompt_ends_with_marker(context: str, kind: str) -> None:
    text = (_PROMPT_DIR / f"{context}_{kind}.txt").read_text().rstrip()
    assert text.endswith("END_OF_PROMPT"), f"{context}_{kind} prompt missing END_OF_PROMPT"


def test_heuristics_ends_with_marker() -> None:
    text = (_PROMPT_DIR / "heuristics.txt").read_text().rstrip()
    assert text.endswith("END_OF_PROMPT")


def test_prompt_hashes_match_snapshots(request: pytest.FixtureRequest) -> None:
    """Snapshot: every prompt's sha256 matches the value in snapshots/prompts.json.

    Run with --snapshot-update to refresh after intentional edits.
    """
    expected = json.loads(_SNAPSHOTS.read_text()) if _SNAPSHOTS.exists() else {}
    actual: dict[str, str] = {}
    for ctx, kind in product(CONTEXTS, KINDS):
        name = f"{ctx}_{kind}.txt"
        actual[name] = _sha256(_PROMPT_DIR / name)
    actual["heuristics.txt"] = _sha256(_PROMPT_DIR / "heuristics.txt")

    if request.config.getoption("--snapshot-update", default=False):
        _SNAPSHOTS.parent.mkdir(parents=True, exist_ok=True)
        _SNAPSHOTS.write_text(json.dumps(actual, indent=2, sort_keys=True))
        return
    assert actual == expected, "prompt hashes drifted; rerun with --snapshot-update if intentional"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--snapshot-update", action="store_true", default=False)
```

- [ ] **Step 2: Generate the initial snapshots.**

```bash
pytest tests/audio/test_prompts.py --snapshot-update
git status   # expect snapshots/prompts.json to appear
```

- [ ] **Step 3: Run normally.**

```bash
pytest tests/audio/test_prompts.py -v
# Expected: 13 PASSED (12 parametrized + 1 hash)
```

### Task 5.3 — Cost calculator (TDD)

- [ ] **Step 1: Write `tests/audio/test_cost.py`.**

```python
"""Pure-function cost calculator tests."""
from __future__ import annotations

import pytest

from auto_a11y.audio.cost import (
    AnthropicUsage,
    CostBreakdown,
    DeepgramUsage,
    estimate_cost,
    cost_for_anthropic_call,
    cost_for_deepgram_minutes,
)


def test_anthropic_cost_no_extended_context() -> None:
    usage = AnthropicUsage(input_tokens=10_000, output_tokens=2_000, cache_read_tokens=0, cache_write_tokens=0)
    cost = cost_for_anthropic_call(usage, model="claude-opus-4-7", extended_context=False)
    # 10000 in @ $5/M = $0.05; 2000 out @ $25/M = $0.05; total $0.10
    assert cost == pytest.approx(0.10, abs=1e-9)


def test_anthropic_cost_with_cache_hits() -> None:
    usage = AnthropicUsage(input_tokens=0, output_tokens=1000, cache_read_tokens=100_000, cache_write_tokens=0)
    cost = cost_for_anthropic_call(usage, model="claude-opus-4-7", extended_context=False)
    # cache_read @ 10% of $5/M = $0.50/M; 100k * 0.50/M = $0.05
    # 1000 out @ $25/M = $0.025
    assert cost == pytest.approx(0.075, abs=1e-9)


def test_deepgram_cost() -> None:
    cost = cost_for_deepgram_minutes(60.0)  # 60 min
    # nova-3 with diarization: $0.0043/min
    assert cost == pytest.approx(0.258, abs=1e-9)


def test_estimate_total() -> None:
    estimate = estimate_cost(
        duration_s=600.0,            # 10 min video
        contexts=["audit"],
        languages=["en"],
        extended_context=False,
        callouts=False,
    )
    # Deepgram 10 min = $0.043
    # 4 passes × ~2000 tokens × $5/M input ≈ $0.04 + output ≈ $0.05 = ~$0.09
    assert estimate.total_usd > 0.05
    assert estimate.total_usd < 1.0
    assert "deepgram" in estimate.breakdown
    assert "claude_en" in estimate.breakdown
```

- [ ] **Step 2: Create `auto_a11y/audio/cost.py`.**

```python
"""Cost calculator for the audioA11y pipeline.

Pricing constants are point-in-time captures from the current Anthropic
and Deepgram rate sheets — see AS_OF below. The cost panel renders a
"Pricing as of …" line so users know the rates may be stale.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Literal


AS_OF: date = date(2026, 5, 19)


# === Anthropic Opus 4.7 (per million tokens) ===
OPUS_47_INPUT_USD_PER_M = 5.0
OPUS_47_OUTPUT_USD_PER_M = 25.0
OPUS_47_EXTENDED_INPUT_USD_PER_M = 10.0   # 2× for tokens above 200K
OPUS_47_EXTENDED_OUTPUT_USD_PER_M = 37.5  # 1.5× for tokens above 200K
CACHE_READ_RATIO = 0.10
CACHE_WRITE_RATIO = 1.25

EXTENDED_CONTEXT_THRESHOLD = 200_000

# === Deepgram nova-3 with diarization ===
DEEPGRAM_NOVA3_USD_PER_MINUTE = 0.0043


@dataclass(frozen=True)
class AnthropicUsage:
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass(frozen=True)
class DeepgramUsage:
    minutes: float


@dataclass(frozen=True)
class CostBreakdown:
    total_usd: float
    breakdown: dict[str, float] = field(default_factory=dict)

    def __add__(self, other: CostBreakdown) -> CostBreakdown:
        merged = dict(self.breakdown)
        for k, v in other.breakdown.items():
            merged[k] = merged.get(k, 0.0) + v
        return CostBreakdown(total_usd=self.total_usd + other.total_usd, breakdown=merged)


def cost_for_anthropic_call(
    usage: AnthropicUsage, *, model: str, extended_context: bool
) -> float:
    """Compute USD cost of one Anthropic call."""
    if model != "claude-opus-4-7":
        raise NotImplementedError(f"pricing constants only defined for Opus 4.7; got {model}")
    if extended_context:
        std_in_rate = OPUS_47_INPUT_USD_PER_M
        ext_in_rate = OPUS_47_EXTENDED_INPUT_USD_PER_M
        std_out_rate = OPUS_47_OUTPUT_USD_PER_M
        ext_out_rate = OPUS_47_EXTENDED_OUTPUT_USD_PER_M
        std_in = min(usage.input_tokens, EXTENDED_CONTEXT_THRESHOLD)
        ext_in = max(0, usage.input_tokens - EXTENDED_CONTEXT_THRESHOLD)
        std_out = min(usage.output_tokens, EXTENDED_CONTEXT_THRESHOLD)
        ext_out = max(0, usage.output_tokens - EXTENDED_CONTEXT_THRESHOLD)
        in_cost = (std_in / 1_000_000) * std_in_rate + (ext_in / 1_000_000) * ext_in_rate
        out_cost = (std_out / 1_000_000) * std_out_rate + (ext_out / 1_000_000) * ext_out_rate
    else:
        in_cost = (usage.input_tokens / 1_000_000) * OPUS_47_INPUT_USD_PER_M
        out_cost = (usage.output_tokens / 1_000_000) * OPUS_47_OUTPUT_USD_PER_M

    cache_read_cost = (usage.cache_read_tokens / 1_000_000) * OPUS_47_INPUT_USD_PER_M * CACHE_READ_RATIO
    cache_write_cost = (usage.cache_write_tokens / 1_000_000) * OPUS_47_INPUT_USD_PER_M * CACHE_WRITE_RATIO

    return in_cost + out_cost + cache_read_cost + cache_write_cost


def cost_for_deepgram_minutes(minutes: float) -> float:
    return minutes * DEEPGRAM_NOVA3_USD_PER_MINUTE


def estimate_cost(
    *,
    duration_s: float,
    contexts: list[Literal["audit", "livedExperience", "navilens"]],
    languages: list[Literal["en", "fr"]],
    extended_context: bool,
    callouts: bool,
) -> CostBreakdown:
    """Rough pre-flight estimate.

    Heuristic: VTT ≈ 200 tokens/minute of speech; each Claude pass uses the
    whole VTT as input plus an expected ~500 output tokens. callouts adds
    only ffmpeg time, no API cost.
    """
    duration_min = duration_s / 60.0
    deepgram = cost_for_deepgram_minutes(duration_min)

    breakdown: dict[str, float] = {"deepgram": deepgram}

    estimated_vtt_tokens = int(duration_min * 200)
    for lang in languages:
        per_lang = 0.0
        for _ in range(4):  # 4 passes: issues, painpoints, takeaways, assertions
            usage = AnthropicUsage(
                input_tokens=estimated_vtt_tokens,
                output_tokens=500,
            )
            per_lang += cost_for_anthropic_call(
                usage, model="claude-opus-4-7", extended_context=extended_context,
            )
        breakdown[f"claude_{lang}"] = per_lang

    total = sum(breakdown.values())
    return CostBreakdown(total_usd=total, breakdown=breakdown)
```

- [ ] **Step 3: Run cost tests.**

```bash
pytest tests/audio/test_cost.py -v
# Expected: 4 PASSED
```

### Task 5.4 — Analysis module (TDD against mocked Anthropic SDK)

- [ ] **Step 1: Write `tests/audio/test_analysis.py`** mocking Anthropic responses.

```python
"""Tests for audio/analysis.py — mocked Anthropic SDK."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from auto_a11y.audio.analysis import AnalysisResult, Analyzer


def _fake_response(text: str) -> object:
    msg = MagicMock()
    msg.content = [MagicMock(text=text)]
    msg.usage = MagicMock(input_tokens=1000, output_tokens=200, cache_read_input_tokens=0, cache_creation_input_tokens=0)
    return msg


def test_analyzer_uses_correct_prompt_for_context() -> None:
    client = MagicMock()
    issues_payload = json.dumps({"recording": "REC-x", "issues": []})
    client.messages.create.return_value = _fake_response(issues_payload)

    analyzer = Analyzer(client=client, model="claude-opus-4-7", extended_context=False)
    result = analyzer.analyze(
        vtt="WEBVTT\n",
        context="audit",
        kind="issues",
        language="en",
        recording_id="REC-test",
    )
    assert isinstance(result, AnalysisResult)
    assert result.json_payload["recording"] == "REC-x"

    sent_kwargs = client.messages.create.call_args.kwargs
    # Assert the prompt body contains the audit-issues prompt text.
    assert any("END_OF_PROMPT" in m["content"] for m in sent_kwargs["messages"] if isinstance(m["content"], str))


def test_analyzer_french_pass_renders_french_output_request() -> None:
    client = MagicMock()
    client.messages.create.return_value = _fake_response(json.dumps({"recording": "x", "issues": []}))
    analyzer = Analyzer(client=client, model="claude-opus-4-7", extended_context=False)
    analyzer.analyze(vtt="W", context="audit", kind="issues", language="fr", recording_id="X")
    # The FR pass prepends a French-output instruction to the prompt.
    sent_msgs = client.messages.create.call_args.kwargs["messages"]
    joined = " ".join(str(m["content"]) for m in sent_msgs)
    assert "français" in joined or "French" in joined  # implementation detail


def test_analyzer_records_cost_in_result() -> None:
    client = MagicMock()
    client.messages.create.return_value = _fake_response(json.dumps({"recording": "x", "issues": []}))
    analyzer = Analyzer(client=client, model="claude-opus-4-7", extended_context=False)
    result = analyzer.analyze(vtt="W", context="audit", kind="issues", language="en", recording_id="X")
    assert result.cost_usd > 0  # 1000 in + 200 out at Opus 4.7 rates
```

- [ ] **Step 2: Create `auto_a11y/audio/analysis.py`.** This is large — see `pythonAudioA11y/analysis.py:35-786` for the source. Key responsibilities:
  - One `analyze(vtt, context, kind, language, recording_id) → AnalysisResult` per call.
  - Use the cached prompt files via `auto_a11y.audio.prompts.load(context, kind)`.
  - Append heuristics (always) + a French-output instruction (when `language="fr"`).
  - Use `prompt-caching-2024-07-31` beta header (matches pythonAudioA11y).
  - Use `output-128k-2025-02-19` beta header when `extended_context=True`.
  - Parse the JSON object out of the response (the prompt instructs Claude to emit a single JSON block).
  - Surface `AnalysisError` on malformed output.
  - Stamp the result with `cost_usd` from `cost_for_anthropic_call`.

```python
"""Claude-based analysis of a VTT transcript.

Ported from pythonAudioA11y/analysis.py:35-786 with adjustments:
- Prompts loaded from auto_a11y/audio/prompts/*.txt (separate files,
  snapshot-tested) rather than inlined in source.
- Cost recorded per call via auto_a11y/audio/cost.py.
- Anthropic SDK kwargs (betas, model id) parameterised via AudioConfig.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Literal

from auto_a11y.audio.cost import AnthropicUsage, cost_for_anthropic_call
from auto_a11y.audio.errors import AnalysisError
from auto_a11y.audio.prompts import Context, Kind, load as load_prompt, load_heuristics

logger = logging.getLogger(__name__)


Language = Literal["en", "fr"]


@dataclass(frozen=True)
class AnalysisResult:
    json_payload: dict[str, Any]
    html_text: str | None
    cost_usd: float
    input_tokens: int
    output_tokens: int


class Analyzer:
    def __init__(self, *, client: Any, model: str = "claude-opus-4-7", extended_context: bool = False) -> None:
        self._client = client
        self._model = model
        self._extended_context = extended_context

    def analyze(
        self, *, vtt: str, context: Context, kind: Kind, language: Language, recording_id: str
    ) -> AnalysisResult:
        prompt_body = load_prompt(context, kind)
        heuristics = load_heuristics()
        prefix = "" if language == "en" else (
            "IMPORTANT: Produce ALL output in French (français). "
            "All field values, all natural-language content, in French.\n\n"
        )
        full_prompt = prefix + heuristics + "\n\n" + prompt_body + "\n\nTRANSCRIPT:\n" + vtt

        betas = ["prompt-caching-2024-07-31"]
        if self._extended_context:
            betas.append("output-128k-2025-02-19")

        message = self._client.messages.create(
            model=self._model,
            max_tokens=8000,
            messages=[{"role": "user", "content": full_prompt}],
            extra_headers={"anthropic-beta": ",".join(betas)},
        )

        # Extract content text. Anthropic SDK returns a list of content blocks.
        text_blocks = [block.text for block in message.content if hasattr(block, "text")]
        raw = "".join(text_blocks)

        json_payload = self._extract_json(raw, recording_id)

        usage = AnthropicUsage(
            input_tokens=getattr(message.usage, "input_tokens", 0),
            output_tokens=getattr(message.usage, "output_tokens", 0),
            cache_read_tokens=getattr(message.usage, "cache_read_input_tokens", 0),
            cache_write_tokens=getattr(message.usage, "cache_creation_input_tokens", 0),
        )
        cost = cost_for_anthropic_call(usage, model=self._model, extended_context=self._extended_context)

        return AnalysisResult(
            json_payload=json_payload,
            html_text=None,  # HTML rendering happens in a sibling helper if/when needed
            cost_usd=cost,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
        )

    @staticmethod
    def _extract_json(text: str, recording_id: str) -> dict[str, Any]:
        """Find the first JSON object in the response.

        Prompts instruct Claude to emit a single ```json … ``` block; we
        find the outermost {...} pair and json.loads it.
        """
        # Strip code fences if present.
        match = re.search(r"```json\s*\n(.*?)\n```", text, re.DOTALL)
        if match:
            payload = match.group(1)
        else:
            # Try to find the outermost { … }.
            first = text.find("{")
            last = text.rfind("}")
            if first == -1 or last == -1 or last <= first:
                raise AnalysisError(f"No JSON object in response for recording {recording_id}")
            payload = text[first:last + 1]
        try:
            data: dict[str, Any] = json.loads(payload)
        except json.JSONDecodeError as e:
            raise AnalysisError(f"Failed to parse JSON for recording {recording_id}: {e}") from e
        return data
```

- [ ] **Step 3: Run analysis tests.**

```bash
pytest tests/audio/test_analysis.py -v
# Expected: 3 PASSED
```

### Task 5.5 — Commit Phase 5

```bash
source .venv/bin/activate
git add auto_a11y/audio/analysis.py auto_a11y/audio/cost.py \
        auto_a11y/audio/prompts/ \
        tests/audio/test_analysis.py tests/audio/test_cost.py \
        tests/audio/test_prompts.py tests/audio/snapshots/
git commit --no-gpg-sign -m "$(cat <<'EOF'
audio: Claude analysis + prompts + cost calculator

auto_a11y/audio/prompts/  — 12 prompt files (3 contexts × 4 kinds) +
                            heuristics.txt + __init__.py loader.
                            Each ends with END_OF_PROMPT marker for
                            snapshot test integrity.

auto_a11y/audio/cost.py   — pricing constants (Opus 4.7, Deepgram
                            nova-3) AS_OF 2026-05-19. CostBreakdown
                            dataclass with __add__. estimate_cost() +
                            cost_for_anthropic_call() +
                            cost_for_deepgram_minutes().

auto_a11y/audio/analysis.py — Analyzer.analyze(vtt, context, kind,
                            language, recording_id) → AnalysisResult.
                            Uses prompt-caching beta; output-128k beta
                            when extended_context. JSON extracted from
                            ```json``` fence or outermost {…}. Cost
                            stamped on every result.

Snapshot tests: tests/audio/snapshots/prompts.json captures sha256 of
every prompt file. Regenerate via:
  pytest --snapshot-update tests/audio/test_prompts.py

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase 6 — Pipeline + runner + JobManager + Recording fields

**Goal:** Stitch Phases 1–5 into `audio/pipeline.py`, wrap in `audio/runner.py` with `VideoRunner`, register `JobType.VIDEO_PROCESSING` with `JobManager`. Extend the `Recording` model with all the new state fields. Index `recordings.status`.

This is the largest phase: ~10 tasks, ~25 commits. The plan abbreviates the per-task TDD steps below; the executing agent should expand each into the standard "write failing test → implement → pass → commit" cadence.

**Files:**
- Create: `auto_a11y/audio/pipeline.py`
- Create: `auto_a11y/audio/runner.py`
- Modify: `auto_a11y/core/job_manager.py` — add `JobType.VIDEO_PROCESSING`
- Modify: `auto_a11y/models/recording.py` — new fields (see spec §"New persisted state")
- Modify: `auto_a11y/core/database.py` — `recordings.status` index, helper `set_status()`
- Modify: `auto_a11y/importers/dictaphone_importer.py` — add `import_in_place(slot, recording)` helper that reads `json/*.json` files into the existing import flow
- Create: `tests/audio/test_pipeline.py`
- Create: `tests/audio/test_runner.py`

### Task 6.1 — Extend Recording with new fields

- [ ] **Step 1: Read `auto_a11y/models/recording.py`** to find the existing dataclass.

- [ ] **Step 2: Add the 13 new fields** (per spec §"New persisted state on Recording"):

```python
# === Audio pipeline state (added by Phase 6 of audioA11y integration) ===
source_video_path: str | None = None
audit_context: Literal["audit", "livedExperience", "navilens"] = "audit"
analysis_languages: list[Literal["en", "fr"]] = field(default_factory=lambda: ["en"])
extended_context: bool = False
speaker_remap_enabled: bool = True
callouts_requested: bool = False
callouts_status: Literal["not-requested", "pending", "complete", "failed"] = "not-requested"
status: Literal["uploaded", "processing", "complete", "failed", "cancelling", "cancelled"] = "uploaded"
progress: dict[str, Any] | None = None
estimated_cost_usd: float | None = None
actual_cost_usd: float | None = None
cost_breakdown: dict[str, Any] | None = None
error_message: str | None = None
manifest_version: str = ""  # populated from auto_a11y.audio.__version__
started_at: datetime | None = None
finished_at: datetime | None = None
```

- [ ] **Step 3: Update `to_dict()` and `from_dict()`** to serialize the new fields.

- [ ] **Step 4: Create `auto_a11y/audio/__init__.py`** exporting `__version__ = "0.1.0"`. Tests + Recording use it.

- [ ] **Step 5: Add the index.** In `auto_a11y/core/database.py`'s `_create_indexes` (or equivalent), add:

```python
self.recordings.create_index("status")
```

- [ ] **Step 6: Run all existing recording tests** — many will fail because the new fields' defaults make existing JSON deserialize fine, but tests that build Recording objects might miss kwargs. Address each.

### Task 6.2 — Pipeline orchestrator (TDD against mocked stages)

- [ ] **Step 1: Write `tests/audio/test_pipeline.py`** with each stage mocked. Pipeline is sequential A → G; verify each stage runs once, in order, with the right inputs.

- [ ] **Step 2: Create `auto_a11y/audio/pipeline.py`.** Pure orchestration over disk paths; no DB writes. Each stage is a function call; the pipeline updates a progress callback between stages.

```python
"""Sequential orchestration of the audio pipeline.

Each stage writes an atomic on-disk artifact; the pipeline reads from
disk between stages. No DB access here — that lives in audio/runner.py.

Progress is reported via a callable injected at construction. The runner
hooks this to write Recording.progress.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from auto_a11y.audio import __version__
from auto_a11y.audio.analysis import Analyzer
from auto_a11y.audio.segmenter import split
from auto_a11y.audio.speaker_identification import build_mapping
from auto_a11y.audio.storage import AllocatedSlot
from auto_a11y.audio.transcription import Transcriber, words_to_vtt, write_words_json
from auto_a11y.audio.vtt_processor import merge_segment_vtts

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PipelineConfig:
    contexts: list[str]
    languages: list[str]
    extended_context: bool
    speaker_remap_enabled: bool
    callouts_requested: bool
    hf_token: str | None


ProgressCallback = Callable[[str, int, int], None]  # (stage_name, current, total)


def run_pipeline(
    *,
    slot: AllocatedSlot,
    config: PipelineConfig,
    transcriber: Transcriber,
    analyzer: Analyzer,
    progress: ProgressCallback,
) -> None:
    """A → G orchestration. Stage F (callouts) only if config.callouts_requested."""
    stages = [
        "segmenting", "transcribing", "speaker_remap", "merging_vtt",
        "analyzing", "callouts", "importing",
    ]

    progress("segmenting", 1, len(stages))
    segments = split(slot.source_mp4, slot)

    progress("transcribing", 2, len(stages))
    for seg in segments:
        result = transcriber.transcribe(slot.segment_m4a(seg.index))
        vtt = words_to_vtt(result.words)
        slot.segment_vtt(seg.index).write_text(vtt)
        write_words_json(result.words, slot.segment_words(seg.index))

    progress("speaker_remap", 3, len(stages))
    if config.speaker_remap_enabled:
        mapping = build_mapping(
            segment_audio_paths=[slot.segment_m4a(s.index) for s in segments],
            words_paths=[slot.segment_words(s.index) for s in segments],
            hf_token=config.hf_token,
        )
        slot.speaker_map.write_text(mapping_to_json(mapping))
    else:
        mapping = None

    progress("merging_vtt", 4, len(stages))
    merged = merge_per_segment_vtts(slot, segments, mapping)
    slot.captions_vtt.write_text(merged)

    progress("analyzing", 5, len(stages))
    for ctx in config.contexts:
        for lang in config.languages:
            for kind in ("issues", "painpoints", "takeaways", "assertions"):
                result = analyzer.analyze(
                    vtt=merged, context=ctx, kind=kind, language=lang,
                    recording_id=slot.root.name,
                )
                slot.json_path(kind=kind, lang=lang).write_text(
                    json.dumps(result.json_payload, indent=2)
                )

    if config.callouts_requested:
        progress("callouts", 6, len(stages))
        # Phase 9 fills this in.
        pass

    progress("importing", 7, len(stages))
    # Importer is called by the runner, not here. The runner reads json/*.json
    # and writes recording_issues into Mongo.
```

(The `mapping_to_json`, `merge_per_segment_vtts` helpers and `json` import elided — straightforward; the executor fleshes them out.)

### Task 6.3 — Runner + JobManager wiring

- [ ] **Step 1: Add `JobType.VIDEO_PROCESSING`** to `auto_a11y/core/job_manager.py` (look for the existing `JobType` enum).

- [ ] **Step 2: Create `auto_a11y/audio/runner.py`** modelled on `auto_a11y/pdf/pdfmax_runner.py`.

```python
"""Async wrapper around audio.pipeline. Owned by JobManager."""
from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path

from auto_a11y.audio import __version__
from auto_a11y.audio.analysis import Analyzer
from auto_a11y.audio.cost import CostBreakdown
from auto_a11y.audio.config import AudioConfig
from auto_a11y.audio.errors import AudioPipelineError
from auto_a11y.audio.pipeline import PipelineConfig, run_pipeline
from auto_a11y.audio.storage import AudioStorage
from auto_a11y.audio.transcription import Transcriber
from auto_a11y.core.database import Database

logger = logging.getLogger(__name__)


class VideoRunner:
    def __init__(self, *, db: Database, storage: AudioStorage, config: AudioConfig) -> None:
        self._db = db
        self._storage = storage
        self._config = config

    async def run(self, recording_id: str) -> None:
        from anthropic import Anthropic
        from deepgram import DeepgramClient

        anthropic_client = Anthropic(api_key=self._config.anthropic_api_key)
        deepgram_client = DeepgramClient(self._config.deepgram_api_key)

        rec = self._db.get_recording_by_recording_id(recording_id)
        if rec is None:
            logger.error("VideoRunner: no Recording for id %s", recording_id)
            return
        slot = self._storage.get(recording_id)

        rec.status = "processing"
        rec.started_at = datetime.now()
        rec.manifest_version = __version__
        self._db.update_recording(rec)

        def progress(stage: str, current: int, total: int) -> None:
            # Bail early if user requested cancel.
            fresh = self._db.get_recording_by_recording_id(recording_id)
            if fresh and fresh.status == "cancelling":
                raise _Cancelled()
            rec.progress = {
                "stage": stage, "current": current, "total": total,
                "started_at": rec.started_at, "elapsed_ms": int((datetime.now() - rec.started_at).total_seconds() * 1000),
            }
            self._db.update_recording(rec)

        try:
            run_pipeline(
                slot=slot,
                config=PipelineConfig(
                    contexts=[rec.audit_context],
                    languages=rec.analysis_languages,
                    extended_context=rec.extended_context,
                    speaker_remap_enabled=rec.speaker_remap_enabled,
                    callouts_requested=rec.callouts_requested,
                    hf_token=self._config.huggingface_token,
                ),
                transcriber=Transcriber(client=deepgram_client, model=self._config.deepgram_model),
                analyzer=Analyzer(client=anthropic_client, model=self._config.claude_model,
                                   extended_context=rec.extended_context),
                progress=progress,
            )
            # G — import JSON files into Mongo
            from auto_a11y.importers.dictaphone_importer import import_pipeline_output
            import_pipeline_output(self._db, slot, rec)

            rec.status = "complete"
            rec.finished_at = datetime.now()
            self._db.update_recording(rec)
        except _Cancelled:
            rec.status = "cancelled"
            rec.finished_at = datetime.now()
            self._db.update_recording(rec)
        except Exception as e:
            logger.exception("VideoRunner failed for %s", recording_id)
            rec.status = "failed"
            rec.error_message = str(e)
            rec.finished_at = datetime.now()
            self._db.update_recording(rec)
            raise


class _Cancelled(Exception):
    pass
```

- [ ] **Step 3: Add `import_pipeline_output(db, slot, recording)`** to `auto_a11y/importers/dictaphone_importer.py`. It reads `slot.json_path(kind="issues", lang=lang)` for each language and calls the existing import code.

- [ ] **Step 4: Wire `JobType.VIDEO_PROCESSING`** into `JobManager.recover_orphans()` (the function that catches mid-process Recordings on server restart). Mirror the PDF runner's recovery hook.

### Task 6.4 — Commit Phase 6

```bash
source .venv/bin/activate
git add auto_a11y/audio/pipeline.py auto_a11y/audio/runner.py auto_a11y/audio/__init__.py \
        auto_a11y/core/job_manager.py auto_a11y/models/recording.py \
        auto_a11y/core/database.py auto_a11y/importers/dictaphone_importer.py \
        tests/audio/test_pipeline.py tests/audio/test_runner.py
git commit --no-gpg-sign -m "$(cat <<'EOF'
audio: pipeline orchestration + VideoRunner + JobManager wiring

audio/pipeline.py    Sequential A → G stage orchestration over disk paths.
                     Pure logic; no DB. Progress callable injected.
audio/runner.py      VideoRunner — async wrapper. Owns DB writes,
                     JobManager registration, cancel-check between
                     stages, status transitions (uploaded → processing
                     → complete | failed | cancelled).
JobManager           Adds JobType.VIDEO_PROCESSING + recover_orphans hook.
Recording            13 new fields: source_video_path, audit_context,
                     analysis_languages, extended_context,
                     speaker_remap_enabled, callouts_requested,
                     callouts_status, status, progress,
                     estimated_cost_usd, actual_cost_usd, cost_breakdown,
                     error_message, manifest_version, started_at,
                     finished_at.
Database             Index on recordings.status.
DictaphoneImporter   New import_pipeline_output(db, slot, recording)
                     that reads json/<id>.{issues,…}.json from the slot
                     and feeds them through the existing import code.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase 7 — Upload form (video mode + cost estimate)

**Goal:** Extend `recordings/upload.html` to accept MP4 input, render a cost-estimate confirm step, auto-generate `recording_id`, and submit the `VIDEO_PROCESSING` job on confirm.

**Files:**
- Modify: `auto_a11y/web/routes/recordings.py` — `upload()` route branches on MIME / extension
- Modify: `auto_a11y/web/templates/recordings/upload.html` — new fields
- Create: `auto_a11y/web/templates/recordings/upload_confirm.html` — the estimate step
- Modify: `auto_a11y/web/translations/{en,fr}/audio.ftl` (new file) — UI strings
- Create: `tests/web/test_upload_video.py`

### Task 7.1 — Add audio.ftl Fluent strings

- [ ] **Step 1: Create `auto_a11y/web/translations/en/audio.ftl`** with ~40 strings (upload form labels, status names, stage descriptions, error messages, cost-panel labels). Examples:

```ftl
# === Upload form ===
audio-upload-mp4-label                  = Video file (MP4)
audio-upload-context-label              = Audit context
audio-upload-context-audit              = Audit
audio-upload-context-lived-experience   = Lived experience
audio-upload-context-navilens           = NaviLens
audio-upload-languages-label            = Languages
audio-upload-language-en                = English
audio-upload-language-fr                = French
audio-upload-speaker-remap-label        = Remap speakers across segments (recommended)
audio-upload-extended-context-label     = Use Opus 1M extended context
audio-upload-callouts-label             = Render callouts video
audio-upload-process-button             = Process now

# === Confirm step ===
audio-confirm-heading                   = Confirm processing
audio-confirm-estimated-cost            = Estimated cost
audio-confirm-duration                  = Video duration
audio-confirm-pricing-asof              = Pricing as of { $date }; rates may have changed.

# === Stage names ===
audio-stage-segmenting                  = Extracting audio
audio-stage-transcribing                = Transcribing
audio-stage-speaker-remap               = Identifying speakers
audio-stage-merging-vtt                 = Merging captions
audio-stage-analyzing                   = Analyzing
audio-stage-callouts                    = Rendering callouts video
audio-stage-importing                   = Importing issues

# === Status labels ===
audio-status-uploaded                   = Uploaded
audio-status-processing                 = Processing
audio-status-complete                   = Complete
audio-status-failed                     = Failed
audio-status-cancelling                 = Cancelling
audio-status-cancelled                  = Cancelled

# === Cost panel ===
audio-cost-heading                      = Cost
audio-cost-deepgram                     = Deepgram
audio-cost-claude                       = Claude
audio-cost-speaker-id                   = Speaker identification
audio-cost-total                        = Total
audio-cost-estimated-was                = Estimated was

# === Errors ===
audio-error-no-language                 = Select at least one language.
audio-error-file-too-large              = Video too large; max { $max } MB.
audio-error-invalid-mp4                 = Not a valid MP4 file.
audio-error-no-project-access           = You do not have access to this project.
```

- [ ] **Step 2: Create `auto_a11y/web/translations/fr/audio.ftl`** with direct French translations.

- [ ] **Step 3: Validate translations.**

```bash
python tests/validate_translations.py
# Expected: PASS — every EN id has an FR id.
```

### Task 7.2 — Extend upload route

- [ ] **Step 1: Read the current upload handler** in `auto_a11y/web/routes/recordings.py`.

- [ ] **Step 2: Add a video branch.** When the uploaded file is `.mp4`:
  - Validate MIME / size cap / project access / language selection.
  - Auto-generate `recording_id = "REC-" + datetime.now().strftime("%Y%m%d%H%M%S") + "-" + secrets.token_hex(3)`.
  - Move the upload to `data/recordings/<id>/source.mp4`.
  - Use `auto_a11y.audio.segmenter.probe_duration` to compute duration.
  - Compute `estimate_cost(...)` from `auto_a11y.audio.cost`.
  - Create the Recording with `status="uploaded"`.
  - Render `recordings/upload_confirm.html` with the estimate + a "Process now" button.

- [ ] **Step 3: Add a POST `/recordings/<id>/process` endpoint** that:
  - Loads the Recording, asserts `status == "uploaded"`, flips to `status="processing"`.
  - Calls `JobManager.submit(JobType.VIDEO_PROCESSING, payload={"recording_id": ...})`.
  - Redirects to `/recordings/<id>` (the detail page renders the progress card).

### Task 7.3 — Upload form template

- [ ] **Step 1: Edit `templates/recordings/upload.html`** to:
  - Add the MP4 file input alongside the existing JSON/HTML inputs (single input with `accept=".mp4,.json,.html"`).
  - Add the video-only fields below the file input, hidden when no .mp4 is selected (small inline JS toggles visibility).
  - Use ONLY custom colour tokens (`badge-neutral`, `text-muted`, etc.) — NO Bootstrap colour utilities.
  - Every string via `ftl()`.

- [ ] **Step 2: Create `templates/recordings/upload_confirm.html`.** Display the cost estimate + "Process now" + "Cancel" buttons.

### Task 7.4 — Tests (TDD against the route)

- [ ] **Step 1: Write `tests/web/test_upload_video.py`** (mirror Phase 3 of the abandoned dictaphone branch — same fixture pattern):

```python
def test_mp4_upload_creates_recording_and_shows_confirm(client: FlaskClient, ...) -> None:
    # Upload a fake mp4 (tiny dummy file)
    resp = client.post("/recordings/upload", data={...})
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Estimated cost" in body
    assert "Process now" in body


def test_process_endpoint_submits_job_and_redirects(...) -> None:
    # After upload_confirm, click "Process now"
    # JobManager.submit was called with VIDEO_PROCESSING
    # Recording.status flipped to "processing"
    # Response is a 302 to /recordings/<id>
```

- [ ] **Step 2: Run.**

```bash
pytest tests/web/test_upload_video.py -v
```

### Task 7.5 — Commit Phase 7

```bash
source .venv/bin/activate
git add auto_a11y/web/routes/recordings.py auto_a11y/web/templates/recordings/upload.html \
        auto_a11y/web/templates/recordings/upload_confirm.html \
        auto_a11y/web/translations/en/audio.ftl auto_a11y/web/translations/fr/audio.ftl \
        tests/web/test_upload_video.py
git commit --no-gpg-sign -m "feat: upload form accepts MP4 + cost-estimate confirm step

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Phase 8 — Progress page + recording detail + cancel

**Goal:** Live progress UI on `/recordings/<id>` via meta-refresh. Cancel button. Cost panel when complete. Status pill on list view.

**Files:**
- Modify: `auto_a11y/web/templates/recordings/detail.html` — progress card + cost panel
- Create: `auto_a11y/web/templates/recordings/_progress_card.html` partial
- Modify: `auto_a11y/web/templates/recordings/list.html` — status pill
- Modify: `auto_a11y/web/routes/recordings.py` — cancel endpoint
- Create: `tests/web/test_recording_progress.py`

### Tasks 8.1–8.5

(The same TDD cadence — write failing test, render template change, pass, commit. Each task small.)

- [ ] **8.1: Progress card partial** — receives `recording.progress` dict, renders stage + percent + elapsed + running cost. Wrapped in `<meta http-equiv="refresh" content="3">` ONLY when `status != "complete"`.

- [ ] **8.2: Detail template branch** — when `status == "complete"`, render the existing issue list + a cost panel sidebar. When otherwise, render the progress card.

- [ ] **8.3: Cancel endpoint** — `POST /recordings/<id>/cancel` flips `status = "cancelling"`. The runner picks it up between stages.

- [ ] **8.4: List view status pill** — render a `badge-discovery` pill with status text for in-flight recordings.

- [ ] **8.5: Commit Phase 8.**

---

## Phase 9 — Callouts video output

**Goal:** Port `pythonAudioA11y/video_processor.py::add_callouts_to_video()` into `auto_a11y/audio/callouts.py`. Optional Stage F in the pipeline. Renders an MP4 with text overlays at issue timecodes; embeds chapters via MP4Box (if available) else ffmpeg.

**Files:**
- Create: `auto_a11y/audio/callouts.py`
- Create: `tests/audio/test_callouts.py`
- Modify: `auto_a11y/audio/pipeline.py` — wire Stage F

### Tasks 9.1–9.4

(One commit per logical piece — drawtext filter construction, chapter embedding, pipeline wiring, download-link UI on detail.html.)

- [ ] **9.1: drawtext filter construction** — `build_drawtext_filter(issues, video_duration) -> str` produces the `-vf` argument. Pure-function, unit-testable.

- [ ] **9.2: ffmpeg command builder + chapter embedding** — `render_callouts_video(source, issues, output, mp4box_path=None)`. Fall back to ffmpeg if MP4Box missing.

- [ ] **9.3: Pipeline integration** — `run_pipeline()` calls `callouts.render(...)` if `config.callouts_requested`. Sets `callouts_status = "complete"` or `"failed"` on the Recording (without failing the job overall).

- [ ] **9.4: UI download link** — on `detail.html`, when `callouts_status == "complete"`, render a download button pointing at the file under `data/recordings/<id>/video/`.

- [ ] **9.5: Commit Phase 9.**

---

## Phase 10 — Settings Recovery blueprint

**Goal:** When preflight fails on startup, 302 every URL to `/recovery/` until the user fixes the failing setting via a recovery form. Settings persist to `~/.config/auto_a11y/settings.json` (or `%APPDATA%\auto_a11y\settings.json`). No in-process restart; user quits and reopens.

**Files:**
- Create: `auto_a11y/web/routes/recovery.py`
- Create: `auto_a11y/web/templates/recovery/index.html`
- Create: `auto_a11y/web/templates/recovery/check_card.html`
- Create: `auto_a11y/web/templates/recovery/settings_form.html`
- Create: `auto_a11y/core/user_settings.py` — read/write the user settings file
- Modify: `auto_a11y/web/app.py` — register recovery blueprint + 302-everything-else middleware when preflight fails
- Create: `tests/web/test_recovery_flow.py`

### Tasks 10.1–10.6

- [ ] **10.1: user_settings.py** — cross-platform path resolution (Linux/macOS/Windows). Atomic write. Read-then-modify-then-write helpers.

- [ ] **10.2: Preflight integration on startup** — `auto_a11y/web/app.py` calls `PreflightRegistry.run_all()`. If failures: store the result on `app.config["PREFLIGHT_FAILURES"]`; the middleware uses this. Mongo URI + Deepgram + Anthropic checks register themselves via the existing pattern.

- [ ] **10.3: 302-everywhere middleware** — `@app.before_request` checks for preflight failures; if any, redirect to `/recovery/` unless the request path starts with `/recovery/` or is a static asset.

- [ ] **10.4: Recovery blueprint** — list each failed check, show the current value, edit form, "Test" button per setting (Mongo / Deepgram / Anthropic / ffmpeg), "Save" button. Save writes to user_settings.json; show a "Quit and reopen" message (no in-process restart).

- [ ] **10.5: Test endpoints** — `POST /recovery/test/ffmpeg`, `/recovery/test/mongo`, etc. Each returns JSON `{ok: bool, message: str}`. Tests assert valid + invalid inputs.

- [ ] **10.6: Commit Phase 10.**

---

## Phase 11 — Final verification + manual walkthrough

**Goal:** Confirm the entire suite is green, the type checks are clean, the css-a11y linter passes, and the upload-to-issues flow works end-to-end with a fixture MP4.

### Task 11.1 — Full pytest run

- [ ] **Step 1: Bring up mongod.**

```bash
mongod --dbpath /tmp/mongo-audioa11y --bind_ip 127.0.0.1 --fork --logpath /tmp/mongo-audioa11y.log
```

- [ ] **Step 2: Run the suite.**

```bash
source .venv/bin/activate
pytest tests/ -x --ignore=tests/pdf
# Expected: all pass; no regressions in existing tests
```

- [ ] **Step 3: Run network-marked tests if keys are present.**

```bash
pytest -m "deepgram or anthropic" -v
```

### Task 11.2 — Type checks + css-a11y

- [ ] **Step 1:**

```bash
.venv/bin/python -m mypy
.venv/bin/python -m pyright
.venv/bin/python -m ty check
bun run scripts/check-css-a11y.ts
```

All four must report zero issues.

### Task 11.3 — Manual browser walkthrough

- [ ] **Step 1: Start the app.**

```bash
python run.py --debug
# http://127.0.0.1:5001
```

- [ ] **Step 2: Walk through each scenario:**
  1. Upload `tests/audio/fixtures/short_audit.mp4` via `/recordings/upload`. Confirm the cost-estimate page shows. Click "Process now".
  2. Watch the progress page advance through stages (Extracting audio → Transcribing → … → Importing).
  3. When complete, verify the issue list renders with the dictaphone-shaped data.
  4. Verify the cost panel shows actual vs estimate.
  5. Click "Cancel" mid-run on a longer fixture; verify the job stops cleanly between stages.
  6. Switch locale to French; verify all new strings render in French.
  7. Test the Settings Recovery flow by intentionally breaking `DEEPGRAM_API_KEY` and restarting; verify the recovery page comes up and saving restores normal operation after quit-and-reopen.

- [ ] **Step 3: Screen-reader walk** (VoiceOver / NVDA / Orca) on the upload form + progress page if available. Note in the PR description what was tested vs untested.

### Task 11.4 — Tear down mongo and final commit

- [ ] **Step 1: Stop the temporary Mongo.**

```bash
mongod --dbpath /tmp/mongo-audioa11y --shutdown
rm -rf /tmp/mongo-audioa11y /tmp/mongo-audioa11y.log
```

- [ ] **Step 2: If any walkthrough revealed UI issues**, fix in a new commit. Do NOT amend prior commits.

- [ ] **Step 3: Final commit summary (only if there are fixes).**

```bash
git commit --no-gpg-sign -m "polish: walkthrough fixes for audioA11y integration"
```

---

## Out of scope (deferred follow-ups)

Per the spec:

- **Resume from partial output directory.** If Stage E partially completed, a rerun should pick up from the missing JSON files instead of redoing transcription. Designed but not built.
- **Persisted disk-usage rollup** on the recording-list page ("Free up 23 GB by deleting these 8 oldest recordings").
- **Background re-process button** that uses a different model / context without re-uploading the MP4.
- **Camtasia XML round-trip** (markers + callouts back into the source project).
- **Drupal ticket creation per issue.**
- **Object storage** (S3 / Azure / GCS) for `source.mp4` and the artifacts directory.
- **NaviLens scoring rubric and best-practice catalogue.**

If you find yourself wanting to do any of these, STOP and surface to the human. They each need their own design pass.

## When you're done

After Phase 11 is green and the walkthrough is clean:

- [ ] Push the branch: `git push -u origin audioA11y-integration`
- [ ] Open a PR against `main`. Use the spec doc as the "Why" section. Include:
  - Summary from the spec's "Goal" section.
  - Bullet list of phases completed.
  - Manual walkthrough results — including the screen-reader status (tested with / not tested).
  - A reminder that recordings stay separate from pages/websites (the count invariants are pinned by the cleanup migration script + the removed model fields).

## Stop points / review gates

If at any point you find yourself wanting to:
- Re-introduce `page_ids` / `page_urls` on Recording or RecordingIssue
- Render recording issues inside the tester area, page detail, or website detail
- Add recording-issue counts to `db.get_project_stats` or any other aggregator
- Hardcode an API key, or commit `.env` content
- Use `--no-verify` or `git commit --amend` to bypass a hook
- Add `# type: ignore`, `cast(Any, ...)`, `# pyright: ignore`, or `-> Any` to satisfy a checker

— **stop and surface to the human.** Each of these violates an explicit project rule.
