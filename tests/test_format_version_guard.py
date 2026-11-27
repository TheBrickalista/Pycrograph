# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
"""PROJECT_FORMAT_VERSION must change whenever the project.json schema does.

A fingerprint of the Project model's JSON schema is recorded per format
version. Older Pycrograph versions refuse a project whose format_version is
newer than theirs, and newer ones migrate older files (migrations.py): both
depend on the version being bumped when the schema changes."""

from __future__ import annotations

import hashlib
import json

from pycrograph.models import PROJECT_FORMAT_VERSION, Project

# format_version -> sha256 of the normalised JSON schema of Project.
FINGERPRINTS = {
    5: "aa0c66e881d9188bd3ecdbac5863b70c6761c14fb18d6b5ce37637756ab55075",
}


def _normalise(x):
    """Remove what differs between pydantic releases for the same model (CI
    runs the oldest supported pydantic and the locked one): 0.0 vs 0, an
    explicit additionalProperties: true, "type" next to "const" / "enum"."""
    if isinstance(x, dict):
        d = {k: _normalise(v) for k, v in x.items()}
        if d.get("additionalProperties") is True:
            del d["additionalProperties"]
        if ("const" in d or "enum" in d) and "type" in d:
            del d["type"]
        return d
    if isinstance(x, list):
        return [_normalise(v) for v in x]
    if isinstance(x, float) and x.is_integer():
        return int(x)
    return x


def schema_fingerprint() -> str:
    text = json.dumps(_normalise(Project.model_json_schema()), sort_keys=True,
                      separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(text.encode("ascii")).hexdigest()


def test_schema_matches_the_recorded_format_version():
    got = schema_fingerprint()
    recorded = FINGERPRINTS.get(PROJECT_FORMAT_VERSION)
    assert recorded is not None, (
        f"No fingerprint recorded for PROJECT_FORMAT_VERSION {PROJECT_FORMAT_VERSION}. "
        f"Add  {PROJECT_FORMAT_VERSION}: \"{got}\"  to FINGERPRINTS in this file "
        "(and keep the older entries).")
    assert got == recorded, (
        "The Project model's schema (project.json) changed but PROJECT_FORMAT_VERSION "
        f"is still {PROJECT_FORMAT_VERSION}.\n"
        "To fix it:\n"
        f"  1. bump PROJECT_FORMAT_VERSION in pycrograph/models.py to {PROJECT_FORMAT_VERSION + 1} "
        "(and its comment);\n"
        "  2. add a migration in pycrograph/migrations.py from the previous version "
        "(even a no-op for a field with a default), with a test in tests/test_migrations.py;\n"
        f"  3. record  {PROJECT_FORMAT_VERSION + 1}: \"{got}\"  in FINGERPRINTS here.\n"
        "If the change cannot affect project.json (e.g. only a docstring or a pydantic "
        "upgrade changed the schema's wording), check the schema diff and update the "
        "recorded value for the current version instead.")


def test_versions_only_grow():
    assert max(FINGERPRINTS) == PROJECT_FORMAT_VERSION
    assert len(set(FINGERPRINTS.values())) == len(FINGERPRINTS)
