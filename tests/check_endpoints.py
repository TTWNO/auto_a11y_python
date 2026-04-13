#!/usr/bin/env python3
"""
Check all HTML GET endpoints for Flask error pages.

Reads CI_USERNAME / CI_PASSWORD from .env (or environment), fetches real
entity IDs from MongoDB, then visits every GET endpoint and checks for
error indicators.
"""

import os
import re
import sys
import requests
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()

BASE_URL = "http://127.0.0.1:5001"

# --- Error detection patterns ---
ERROR_PATTERNS = [
    r"Traceback \(most recent call last\)",
    r"Internal Server Error",
    r"<title>500",
    r"jinja2\.exceptions\.",
    r"UndefinedError",
    r"TemplateSyntaxError",
    r"TypeError:",
    r"AttributeError:",
    r"KeyError:",
    r"NameError:",
    r"ValueError:",
    r"ImportError:",
    r"ModuleNotFoundError:",
    r'class="traceback"',
    r'class="debugger"',
    r"The debugger caught an exception",
    r"werkzeug\.exceptions",
]


def get_ids_from_db():
    """Fetch real entity IDs from MongoDB so parameterised routes can be tested."""
    mongo_uri = os.getenv('MONGODB_URI', 'mongodb://127.0.0.1:27017/')
    db_name = os.getenv('DATABASE_NAME', os.getenv('MONGODB_DATABASE', 'auto_a11y'))
    client = MongoClient(mongo_uri)
    db = client[db_name]

    ids = {}

    # Project
    proj = db.projects.find_one()
    ids["project_id"] = str(proj["_id"]) if proj else None

    # Website
    ws = db.websites.find_one()
    ids["website_id"] = str(ws["_id"]) if ws else None

    # Page
    pg = db.pages.find_one()
    ids["page_id"] = str(pg["_id"]) if pg else None

    # App user (not self)
    user = db.app_users.find_one()
    ids["user_id"] = str(user["_id"]) if user else None

    # Recording
    rec = db.recordings.find_one() if "recordings" in db.list_collection_names() else None
    ids["recording_id"] = str(rec["_id"]) if rec else None

    # Script
    scr = db.custom_scripts.find_one() if "custom_scripts" in db.list_collection_names() else None
    ids["script_id"] = str(scr["_id"]) if scr else None

    # Test result
    tr = db.test_results.find_one()
    ids["result_id"] = str(tr["_id"]) if tr else None

    # Group
    grp = db.groups.find_one() if "groups" in db.list_collection_names() else None
    ids["group_id"] = str(grp["_id"]) if grp else None

    # Schedule
    sch = db.scheduled_tests.find_one() if "scheduled_tests" in db.list_collection_names() else None
    ids["schedule_id"] = str(sch["_id"]) if sch else None

    # Discovery run
    disc = db.discovery_runs.find_one() if "discovery_runs" in db.list_collection_names() else None
    ids["discovery_run_id"] = str(disc["_id"]) if disc else None

    # Tester / supervisor (project participants)
    part = db.project_participants.find_one({"role": "tester"}) if "project_participants" in db.list_collection_names() else None
    ids["tester_id"] = str(part["_id"]) if part else None
    sup = db.project_participants.find_one({"role": "supervisor"}) if "project_participants" in db.list_collection_names() else None
    ids["supervisor_id"] = str(sup["_id"]) if sup else None

    client.close()
    return ids


def build_endpoint_list(ids):
    """Return list of (label, url) tuples for every GET endpoint."""
    pid = ids.get("project_id")
    wid = ids.get("website_id")
    pgid = ids.get("page_id")
    uid = ids.get("user_id")
    rid = ids.get("recording_id")
    sid = ids.get("script_id")
    tid = ids.get("result_id")
    gid = ids.get("group_id")
    schid = ids.get("schedule_id")
    drid = ids.get("discovery_run_id")
    testerid = ids.get("tester_id")
    supid = ids.get("supervisor_id")

    # Static routes (no params needed)
    endpoints = [
        ("index", "/"),
        ("dashboard", "/dashboard"),
        ("help", "/help"),
        ("auth/login", "/auth/login"),
        ("auth/register", "/auth/register"),
        ("auth/forgot-password", "/auth/forgot-password"),
        ("auth/profile", "/auth/profile"),
        ("auth/users", "/auth/users"),
        ("auth/users/create", "/auth/users/create"),
        ("auth/contact", "/auth/contact"),
        ("projects/", "/projects/"),
        ("projects/create", "/projects/create"),
        ("testing/dashboard", "/testing/dashboard"),
        ("testing/fixture-status", "/testing/fixture-status"),
        ("testing/configure", "/testing/configure"),
        ("testing/trends", "/testing/trends"),
        ("reports/dashboard", "/reports/dashboard"),
        ("recordings/", "/recordings/"),
        ("recordings/upload", "/recordings/upload"),
        ("schedules", "/schedules"),
        ("groups/", "/groups/"),
        ("groups/create", "/groups/create"),
        ("drupal/audits/list", "/drupal/audits/list"),
    ]

    # Project-scoped routes
    if pid:
        endpoints += [
            ("projects/<pid>", f"/projects/{pid}"),
            ("projects/<pid>/edit", f"/projects/{pid}/edit"),
            ("projects/<pid>/report", f"/projects/{pid}/report"),
            ("drupal/<pid>/sync", f"/drupal/projects/{pid}/sync"),
            ("drupal/<pid>/sync/status", f"/drupal/projects/{pid}/sync/status"),
            ("drupal/<pid>/discovered-pages", f"/drupal/projects/{pid}/discovered-pages"),
            ("drupal/<pid>/recordings", f"/drupal/projects/{pid}/recordings"),
            ("drupal/<pid>/issues", f"/drupal/projects/{pid}/issues"),
            ("automated_tests/<pid>", f"/automated_tests/projects/{pid}"),
            ("participants/<pid>", f"/projects/{pid}/participants"),
            ("participants/<pid>/testers/create", f"/projects/{pid}/participants/testers/create"),
            ("participants/<pid>/supervisors/create", f"/projects/{pid}/participants/supervisors/create"),
            ("project_users/<pid>", f"/projects/{pid}/users"),
            ("project_users/<pid>/create", f"/projects/{pid}/users/create"),
            ("reports/project/<pid>/summary", f"/reports/project/{pid}/summary"),
            ("recordings/combined/<pid>", f"/recordings/combined/{pid}"),
        ]

    # Website-scoped routes
    if wid:
        endpoints += [
            ("websites/<wid>", f"/websites/{wid}"),
            ("websites/<wid>/edit", f"/websites/{wid}/edit"),
            ("websites/<wid>/documents", f"/websites/{wid}/documents"),
            ("websites/<wid>/discovery-status", f"/websites/{wid}/discovery-status"),
            ("websites/<wid>/test-status", f"/websites/{wid}/test-status"),
            ("websites/<wid>/discovery-history", f"/websites/{wid}/discovery-history"),
            ("scripts/website/<wid>", f"/scripts/website/{wid}/scripts"),
            ("scripts/website/<wid>/create", f"/scripts/website/{wid}/scripts/create"),
            ("schedules/<wid>", f"/websites/{wid}/schedules"),
            ("schedules/<wid>/create", f"/websites/{wid}/schedules/create"),
        ]

    # Page-scoped routes
    if pgid:
        endpoints += [
            ("pages/<pgid>", f"/pages/{pgid}"),
            ("pages/<pgid>/edit", f"/pages/{pgid}/edit"),
            ("pages/<pgid>/violations", f"/pages/{pgid}/violations"),
            ("pages/<pgid>/matrix", f"/pages/{pgid}/matrix"),
            ("pages/<pgid>/test-status", f"/pages/{pgid}/test-status"),
            ("scripts/page/<pgid>", f"/scripts/page/{pgid}/scripts"),
            ("scripts/page/<pgid>/create", f"/scripts/page/{pgid}/scripts/create"),
        ]

    # User-scoped routes
    if uid:
        endpoints += [
            ("auth/users/<uid>/edit", f"/auth/users/{uid}/edit"),
            ("project_users/<uid>", f"/projects/users/{uid}"),
            ("project_users/<uid>/edit", f"/projects/users/{uid}/edit"),
        ]

    # Other entity routes
    if rid:
        endpoints += [
            ("recordings/<rid>", f"/recordings/{rid}"),
        ]

    if sid:
        endpoints += [
            ("scripts/<sid>", f"/scripts/{sid}"),
            ("scripts/<sid>/edit", f"/scripts/{sid}/edit"),
        ]

    if tid:
        endpoints += [
            ("testing/result/<tid>", f"/testing/result/{tid}"),
        ]

    if gid:
        endpoints += [
            ("groups/<gid>/edit", f"/groups/{gid}/edit"),
        ]

    if schid and wid:
        endpoints += [
            ("schedules/<wid>/<schid>", f"/websites/{wid}/schedules/{schid}"),
            ("schedules/<wid>/<schid>/edit", f"/websites/{wid}/schedules/{schid}/edit"),
        ]

    if drid and wid:
        endpoints += [
            ("discovery/<wid>/<drid>", f"/websites/{wid}/discovery/{drid}"),
        ]

    if testerid and pid:
        endpoints += [
            ("tester/<pid>/<tid>/edit", f"/projects/{pid}/participants/testers/{testerid}/edit"),
        ]

    if supid and pid:
        endpoints += [
            ("supervisor/<pid>/<sid>/edit", f"/projects/{pid}/participants/supervisors/{supid}/edit"),
        ]

    return endpoints


def login(session, creds):
    """Log in via the auth form and return True on success."""
    # First GET login page (for CSRF token)
    resp = session.get(f"{BASE_URL}/auth/login")
    # Extract CSRF token from form
    match = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', resp.text)
    if not match:
        # Try alternate pattern
        match = re.search(r'id="csrf_token"[^>]*value="([^"]+)"', resp.text)
    if not match:
        match = re.search(r'type="hidden"[^>]*value="([^"]+)"', resp.text)
    csrf = match.group(1) if match else ""

    if not csrf:
        print("  WARNING: No CSRF token found on login page")

    resp = session.post(
        f"{BASE_URL}/auth/login",
        data={
            "email": creds["USERNAME"],
            "password": creds["PASSWORD"],
            "csrf_token": csrf,
        },
        allow_redirects=True,
    )

    # Debug: show what happened
    print(f"  Login POST status: {resp.status_code}, URL: {resp.url}")

    # Check we're logged in (should redirect to dashboard, not back to login)
    if "/dashboard" in resp.url or resp.url == f"{BASE_URL}/":
        return True
    # Check if page contains dashboard content
    if "dashboard" in resp.text.lower() or "projects" in resp.text.lower():
        return True

    # Login failed — show flash messages for diagnosis
    flash_matches = re.findall(r'class="alert[^"]*"[^>]*>(.*?)</div>', resp.text, re.DOTALL)
    if flash_matches:
        for msg in flash_matches:
            clean = re.sub(r'<[^>]+>', '', msg).strip()
            if clean:
                print(f"  Flash message: {clean}")

    return False


def check_for_errors(text, status_code):
    """Return list of matched error patterns in response text."""
    if status_code == 500:
        return ["HTTP 500"]
    found = []
    for pat in ERROR_PATTERNS:
        if re.search(pat, text, re.IGNORECASE):
            found.append(pat)
    return found


def main():
    print("=" * 70)
    print("AUTO A11Y - Endpoint Health Check")
    print("=" * 70)

    # Load credentials from environment / .env
    username = os.getenv('CI_USERNAME')
    password = os.getenv('CI_PASSWORD')
    if not username or not password:
        print("SKIP: CI_USERNAME / CI_PASSWORD not set in environment or .env")
        sys.exit(0)
    creds = {'USERNAME': username, 'PASSWORD': password}
    print(f"\nLoaded credentials for: {creds['USERNAME']}")

    # Get entity IDs from MongoDB
    print("Fetching entity IDs from MongoDB...")
    ids = get_ids_from_db()
    for k, v in ids.items():
        print(f"  {k}: {v or '(none)'}")

    # Build endpoint list
    endpoints = build_endpoint_list(ids)
    print(f"\n{len(endpoints)} endpoints to check\n")

    # Login
    session = requests.Session()
    print("Logging in...")
    if not login(session, creds):
        print("ERROR: Login failed!")
        sys.exit(1)
    print("Login successful\n")

    # Check each endpoint
    failures = []
    for label, url in endpoints:
        full_url = f"{BASE_URL}{url}"
        try:
            resp = session.get(full_url, allow_redirects=True, timeout=120)
            errors = check_for_errors(resp.text, resp.status_code)
            status = resp.status_code

            if errors:
                print(f"  FAIL  {status}  {url}")
                for e in errors:
                    print(f"         -> {e}")
                failures.append((label, url, status, errors, resp.text))
            else:
                print(f"  OK    {status}  {url}")
        except Exception as e:
            print(f"  ERR        {url}  ({e})")
            failures.append((label, url, 0, [str(e)], ""))

    # Summary
    print("\n" + "=" * 70)
    print(f"Results: {len(endpoints) - len(failures)}/{len(endpoints)} passed")
    if failures:
        print(f"\nFAILED ENDPOINTS ({len(failures)}):")
        for label, url, status, errors, text in failures:
            print(f"\n  [{status}] {url}  ({label})")
            for e in errors:
                print(f"    -> {e}")
            # Print a snippet of the error for diagnosis
            for line in text.split("\n"):
                line = line.strip()
                if any(re.search(p, line, re.IGNORECASE) for p in ERROR_PATTERNS):
                    # Trim to 200 chars
                    print(f"    | {line[:200]}")
        sys.exit(1)
    else:
        print("\nAll endpoints OK!")
        sys.exit(0)


if __name__ == "__main__":
    main()
