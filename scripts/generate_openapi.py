"""Generate docs/api/openapi.yaml from the live /api/v1 blueprint.

Usage:

    python scripts/generate_openapi.py             # write the file
    python scripts/generate_openapi.py --check     # exit non-zero if stale

Reads OPENAPI_OUT from the env if set; otherwise writes to docs/api/openapi.yaml.

Architectural note: we build a minimal Flask app that registers only
``api_bp`` (the /api/v1 blueprint) and imports ``v1_openapi`` to attach
the dynamic spec endpoints. We deliberately avoid ``create_app()`` because
that path requires a live MongoDB connection during init — the OpenAPI
spec is statically derivable from route definitions and Pydantic schemas
alone.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# Ensure the repo root is importable when this script is invoked directly
# (e.g. `python scripts/generate_openapi.py`) — without this, the
# ``auto_a11y`` package on the repo root is not on sys.path.
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

# Offline doc generation: never touches the AI integration, but transitive
# imports trigger ``config.validate()`` which otherwise demands CLAUDE_API_KEY.
# Default the flag off so local devs and CI can run the script without a key.
os.environ.setdefault("RUN_AI_ANALYSIS", "False")

import yaml
from flask import Flask
from openapi_spec_validator import validate_spec

DEFAULT_OUT = REPO / "docs" / "api" / "openapi.yaml"


def _build_minimal_app() -> Flask:
    """Construct a Flask app with just the /api/v1 surface registered.

    Importing ``v1_openapi`` has the side effect of attaching the
    ``/openapi.{json,yaml}`` routes to ``api_bp``. Decorated handlers in
    ``api_bp`` populate the @document registry at import time as well.
    """
    from auto_a11y.web.routes.api import api_bp
    from auto_a11y.web.routes import v1_openapi
    from auto_a11y.web.api.openapi.document import register_documented_views

    # Reference the views so the linter doesn't flag the import as unused.
    _ = (v1_openapi.openapi_json, v1_openapi.openapi_yaml)

    app = Flask(__name__)
    app.register_blueprint(api_bp, url_prefix="/api/v1")
    register_documented_views(app)
    return app


def _spec_text() -> str:
    from auto_a11y.web.api.openapi.builder import build_spec
    app = _build_minimal_app()
    spec = build_spec(app)
    validate_spec(spec)
    return yaml.safe_dump(spec, sort_keys=True, default_flow_style=False, allow_unicode=True)


def main() -> int:
    out_path = Path(os.environ.get("OPENAPI_OUT", str(DEFAULT_OUT)))
    check = "--check" in sys.argv[1:]
    new_text = _spec_text()

    if check:
        if not out_path.exists():
            sys.stderr.write(
                f"openapi spec missing at {out_path}; run scripts/generate_openapi.py\n"
            )
            return 2
        current = out_path.read_text()
        if current != new_text:
            sys.stderr.write(
                f"openapi spec at {out_path} is stale (drift detected);"
                + " run scripts/generate_openapi.py to regenerate\n"
            )
            return 2
        return 0

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(new_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
