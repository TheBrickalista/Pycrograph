# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
"""CITATION.cff: valid against the rules of the Citation File Format 1.2.0
schema that this file uses (required keys, allowed keys, ORCID / DOI / date
formats, reference objects), and in step with the credits shown in the app
(pycrograph/credits.py) and the package metadata.

No extra dependency: the rules are checked with PyYAML (already a dev
dependency) rather than cffconvert / jsonschema. `uvx cffconvert --validate`
gives the full check by hand."""

from __future__ import annotations

import datetime as dt
import re
import tomllib
from pathlib import Path

import pytest
import yaml

from pycrograph import credits

ROOT = Path(__file__).resolve().parents[1]

# From the CFF 1.2.0 JSON schema (definitions "orcid", "doi", "date", and the
# property lists of the root, "person" and "reference" objects).
ORCID = re.compile(r"^https://orcid\.org/[0-9]{4}-[0-9]{4}-[0-9]{4}-[0-9]{3}[0-9X]$")
DOI = re.compile(r"^10\.\d{4,9}(\.\d+)?/[A-Za-z0-9:/_;\-\.\(\)\[\]\\]+$")
ROOT_KEYS = {"abstract", "authors", "cff-version", "commit", "contact", "date-released", "doi",
             "identifiers", "keywords", "license", "license-url", "message",
             "preferred-citation", "references", "repository", "repository-artifact",
             "repository-code", "title", "type", "url", "version"}
ROOT_REQUIRED = {"authors", "cff-version", "message", "title"}
PERSON_KEYS = {"address", "affiliation", "alias", "city", "country", "email", "family-names",
               "fax", "given-names", "name-particle", "name-suffix", "orcid", "post-code",
               "region", "tel", "website"}
REFERENCE_REQUIRED = {"authors", "title", "type"}
AFFILIATION = "Université Côte d'Azur, CNRS, Inserm, IRCAN, Nice, France"


@pytest.fixture(scope="module")
def cff():
    # BaseLoader: every scalar stays a string, as the CFF schema expects
    # (a bare 10.5281/... or 2026-11-20 must not become a number / date).
    return yaml.load((ROOT / "CITATION.cff").read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def check_person(p: dict) -> None:
    assert set(p) <= PERSON_KEYS, set(p) - PERSON_KEYS
    assert p["family-names"] and p["given-names"]
    assert ORCID.match(p["orcid"]), p["orcid"]


def test_cff_schema_rules(cff):
    assert ROOT_REQUIRED <= set(cff) and set(cff) <= ROOT_KEYS, set(cff) ^ ROOT_KEYS
    assert cff["cff-version"] == "1.2.0" and cff["type"] in ("software", "dataset")
    dt.date.fromisoformat(cff["date-released"])
    assert cff["license"] == "GPL-3.0-only"
    assert len(cff["keywords"]) == len(set(cff["keywords"]))
    assert cff["authors"]
    for p in cff["authors"]:
        check_person(p)
    for ref in cff.get("references", []):
        assert REFERENCE_REQUIRED <= set(ref)
        for p in ref["authors"]:
            check_person(p)
        if "doi" in ref:
            assert DOI.match(ref["doi"]), ref["doi"]


def test_authors_and_pysternblot_reference(cff):
    people = [(p["given-names"], p["family-names"], p["orcid"], p["affiliation"])
              for p in cff["authors"]]
    expected = [("Etienne", "Boulter", "https://orcid.org/0000-0001-6597-9086", AFFILIATION),
                ("Chloé C.", "Féral", "https://orcid.org/0000-0001-9756-2082", AFFILIATION)]
    assert people == expected                                   # PysternBlot's, same order
    [pb] = cff["references"]
    assert (pb["type"], pb["title"], pb["doi"], pb["repository-code"]) == (
        "software", "PysternBlot", "10.5281/zenodo.20185279",
        "https://github.com/TheBrickalista/PysternBlot")
    assert [(p["given-names"], p["family-names"]) for p in pb["authors"]] == \
        [(g, f) for g, f, _o, _a in expected]
    assert cff["doi"] == "10.5281/zenodo.24404763"              # Zenodo concept DOI
    assert DOI.match(cff["doi"])


def test_app_credits_and_metadata_match_citation(cff):
    names = tuple(f"{p['given-names']} {p['family-names']}" for p in cff["authors"])
    assert credits.AUTHORS == names
    assert credits.COPYRIGHT == ("Copyright © 2026 Etienne Boulter, Chloé C. Féral — "
                                 "Université Côte d'Azur, CNRS, Inserm, IRCAN")
    assert credits.INSTITUTION in AFFILIATION
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert tuple(a["name"] for a in pyproject["project"]["authors"]) == names
    conf = (ROOT / "docs" / "conf.py").read_text(encoding="utf-8")
    assert 'copyright = "2026, Etienne Boulter and Chloé C. Féral"' in conf
    readme = " ".join((ROOT / "README.md").read_text(encoding="utf-8").split())
    assert credits.COPYRIGHT in readme
    assert "maintained by Etienne Boulter and Chloé C. Féral (IRCAN)" in \
        (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")


def test_copyright_html_is_ascii_entities():
    h = credits.copyright_html()
    assert h.isascii()
    for entity in ("&#169;", "&#233;", "&#8212;", "&#x27;"):     # ©, é, —, '
        assert entity in h, entity
