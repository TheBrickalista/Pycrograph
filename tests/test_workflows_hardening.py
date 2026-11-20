# Pycrograph
# SHARED-CORE candidate (from PysternBlot: tests/test_workflows_hardening.py) — adapted:
# Pycrograph workflows, plus release-specific guards (no pull_request_target,
# pre-releases not published to PyPI, signing secrets, entitlements, macOS floor).
# SPDX-License-Identifier: GPL-3.0-only
"""Guard the GitHub Actions hardening (least-privilege tokens, no persisted
checkout credentials, SHA-pinned actions, pinned runner images, no event data
spliced into shell, no pull_request_target) and the release pipeline.

PyYAML (YAML 1.1) parses the bare key `on:` as the boolean True, so the
trigger block is read as data[True].
"""

import plistlib
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = ROOT / ".github" / "workflows"
WORKFLOWS = sorted(WORKFLOW_DIR.glob("*.yml")) + sorted(WORKFLOW_DIR.glob("*.yaml"))

pytestmark = pytest.mark.skipif(
    not WORKFLOWS,
    reason="source checkout only (workflows are not shipped in the wheel)",
)

# The only job-level token widenings allowed, keyed by (workflow file, job id).
ALLOWED_JOB_PERMISSIONS = {
    ("codeql.yml", "analyze"): {"security-events": "write", "contents": "read"},
    ("publish.yml", "publish"): {"id-token": "write"},
    ("publish.yml", "build-macos"): {"contents": "write"},
    ("publish.yml", "build-windows"): {"contents": "write"},
    ("build-macos.yml", "build-macos"): {"contents": "write"},
    ("build-windows.yml", "build-windows"): {"contents": "write"},
}
MACOS_SECRETS = {"MACOS_CERTIFICATE", "MACOS_CERTIFICATE_PWD", "KEYCHAIN_PWD",
                 "APPLE_API_KEY", "APPLE_API_KEY_ID", "APPLE_API_ISSUER_ID"}

SHA_PINNED = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")
EVENT_EXPR = re.compile(r"\$\{\{[^}]*\bgithub\.event\.")


def _load(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _jobs(path):
    return _load(path)["jobs"].items()


def _steps(path):
    for job_id, job in _jobs(path):
        for step in job.get("steps", []):
            yield job_id, step


@pytest.fixture(params=WORKFLOWS, ids=lambda p: p.name)
def workflow(request):
    return request.param


def test_workflows_found():
    names = {p.name for p in WORKFLOWS}
    assert {"publish.yml", "pytest.yml", "codeql.yml", "build-macos.yml",
            "build-windows.yml"} <= names


def test_top_level_permissions_read_only(workflow):
    data = _load(workflow)
    assert True in data, "PyYAML should read the `on:` key as True"
    assert data.get("permissions") == {"contents": "read"}


def test_no_pull_request_target(workflow):
    """pull_request_target runs untrusted PR code with the base repo's
    secrets and a write token."""
    on = _load(workflow)[True]
    triggers = on if isinstance(on, (dict, list)) else [on]
    assert "pull_request_target" not in triggers
    assert "pull_request_target" not in workflow.read_text(encoding="utf-8")


def test_job_permissions_only_from_allowlist(workflow):
    for job_id, job in _jobs(workflow):
        if "permissions" not in job:
            continue
        key = (workflow.name, job_id)
        assert key in ALLOWED_JOB_PERMISSIONS, (
            f"{workflow.name}:{job_id} widens permissions without an allowlist entry"
        )
        assert job["permissions"] == ALLOWED_JOB_PERMISSIONS[key], (
            f"{workflow.name}:{job_id} permissions differ from the allowlist"
        )


def test_allowlist_entries_exist():
    """A stale allowlist entry would silently permit a future widening."""
    for name, job_id in ALLOWED_JOB_PERMISSIONS:
        jobs = dict(_jobs(WORKFLOW_DIR / name))
        assert "permissions" in jobs.get(job_id, {}), f"stale entry {name}:{job_id}"


def test_checkout_does_not_persist_credentials(workflow):
    for job_id, step in _steps(workflow):
        if str(step.get("uses", "")).startswith("actions/checkout@"):
            assert (step.get("with") or {}).get("persist-credentials") is False, (
                f"{workflow.name}:{job_id} checkout must set persist-credentials: false"
            )


def test_actions_pinned_to_full_sha(workflow):
    refs = [step["uses"] for _, step in _steps(workflow) if "uses" in step]
    refs += [
        job["uses"]
        for _, job in _jobs(workflow)
        if "uses" in job and not job["uses"].startswith("./")
    ]
    for ref in refs:
        assert SHA_PINNED.match(ref), f"{workflow.name}: {ref!r} is not pinned to a 40-char SHA"


def test_runner_images_pinned(workflow):
    """A *-latest label moves to a new OS under us; upgrades must be deliberate."""
    for job_id, job in _jobs(workflow):
        labels = job.get("runs-on")
        if labels is None:  # reusable-workflow call; the callee is checked
            continue
        if isinstance(labels, str) and labels.startswith("${{"):
            labels = (job.get("strategy") or {}).get("matrix", {}).get("os", [])
        for label in [labels] if isinstance(labels, str) else labels:
            assert not str(label).endswith("-latest"), (
                f"{workflow.name}:{job_id} runs-on {label!r}; pin a versioned image"
            )


def test_no_event_expressions_in_run_blocks(workflow):
    """Event data (tag names, titles, ...) must reach shell only through env."""
    for job_id, step in _steps(workflow):
        run = step.get("run")
        if run is not None:
            assert not EVENT_EXPR.search(run), (
                f"{workflow.name}:{job_id} step {step.get('name')!r} expands "
                f"github.event.* inside run:; pass it through env instead"
            )


EXACT_VERSION = re.compile(r"^\d+\.\d+\.\d+$")


def _uv_floor():
    import tomllib

    spec = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "tool"]["uv"]["required-version"]
    m = re.fullmatch(r">=\s*(\d+\.\d+\.\d+)", spec.strip())
    assert m, f"required-version {spec!r} must be a floor only (>=x.y.z)"
    return tuple(int(x) for x in m.group(1).split("."))


def _setup_uv_versions():
    for wf in WORKFLOWS:
        for job_id, step in _steps(wf):
            if str(step.get("uses", "")).startswith("astral-sh/setup-uv@"):
                yield wf.name, job_id, (step.get("with") or {}).get("version")


def test_setup_uv_pins_one_exact_version_at_or_above_floor():
    found = list(_setup_uv_versions())
    assert found, "no setup-uv steps found"
    for name, job_id, version in found:
        assert version is not None and EXACT_VERSION.match(str(version)), (
            f"{name}:{job_id} setup-uv needs an exact version: x.y.z (got {version!r})"
        )
    versions = {str(v) for _, _, v in found}
    assert len(versions) == 1, f"setup-uv steps pin different uv versions: {sorted(versions)}"
    pinned = tuple(int(x) for x in versions.pop().split("."))
    assert pinned >= _uv_floor(), "CI uv pin is below [tool.uv] required-version"


# ------------------------------------------------------------ release pipeline
def test_publish_order_and_prerelease_gate():
    jobs = dict(_jobs(WORKFLOW_DIR / "publish.yml"))
    assert set(jobs["build"]["needs"] if isinstance(jobs["build"]["needs"], list)
               else [jobs["build"]["needs"]]) == {"test"}
    for b in ("build-macos", "build-windows"):
        assert jobs[b]["needs"] == "test" and jobs[b]["uses"] == f"./.github/workflows/{b}.yml"
    pub = jobs["publish"]
    assert set(pub["needs"]) == {"build", "build-macos", "build-windows"}
    assert pub["environment"] == "pypi"
    cond = pub["if"].replace(" ", "")
    assert "github.event_name=='release'" in cond and "!github.event.release.prerelease" in cond
    uses = [s["uses"] for s in pub["steps"] if "uses" in s]
    assert any(u.startswith("pypa/gh-action-pypi-publish@") for u in uses)
    build_runs = " ".join(s.get("run", "") for s in jobs["build"]["steps"])
    assert "--build-constraints ci/build-constraints.txt --require-hashes" in build_runs
    assert "twine check dist/*" in build_runs
    assert set(jobs["build-macos"]["secrets"]) == MACOS_SECRETS
    on = _load(WORKFLOW_DIR / "publish.yml")[True]
    assert on["release"]["types"] == ["published"] and "workflow_dispatch" in on


def test_release_tag_checked_against_full_version():
    text = (WORKFLOW_DIR / "publish.yml").read_text(encoding="utf-8")
    assert "full_version('pyproject.toml')" in text and '"v$V"' in text


@pytest.mark.parametrize("name,asset", [("build-macos.yml", "-macOS.zip"),
                                        ("build-windows.yml", "-Windows.exe")])
def test_app_builds(name, asset):
    data = _load(WORKFLOW_DIR / name)
    on = data[True]
    assert "workflow_call" in on and "workflow_dispatch" in on
    text = (WORKFLOW_DIR / name).read_text(encoding="utf-8")
    assert f"Pycrograph-${{{{ steps.version.outputs.tag }}}}{asset}" in text
    assert "--copy-metadata pycrograph" in text and "pycrograph/resources" in text
    assert "--selfcheck" in text and "--collect-data certifi" not in text   # offline: no TLS
    # App icon (.icns / .ico) from the PG mark, never the logo with its wordmark.
    assert "pycrograph/resources/pycrograph_icon.png" in text
    assert "pycrograph_logo.png" not in text
    for job_id, step in _steps(WORKFLOW_DIR / name):
        if str(step.get("uses", "")).startswith("softprops/action-gh-release@"):
            assert step["if"] == "github.event_name == 'release'"


def test_macos_signing_and_bundle():
    data = _load(WORKFLOW_DIR / "build-macos.yml")
    assert set(data[True]["workflow_call"]["secrets"]) == MACOS_SECRETS
    job = data["jobs"]["build-macos"]
    assert job["runs-on"] == "macos-26" and job["env"]["MACOS_MIN_VERSION"] == "15.0"
    text = (WORKFLOW_DIR / "build-macos.yml").read_text(encoding="utf-8")
    for needle in ('--osx-bundle-identifier "io.github.thebrickalista.pycrograph"',
                   "--options runtime", "--entitlements .github/macos/entitlements.plist",
                   "xcrun notarytool submit", "xcrun stapler staple", "xcrun stapler validate",
                   "find \"$APP\" -type l", "codesign --verify --deep --strict",
                   "LSMinimumSystemVersion", "iconutil -c icns"):
        assert needle in text, needle
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "macOS 15" in readme                                # keep README in step


def test_entitlements_minimal():
    ent = plistlib.loads((ROOT / ".github" / "macos" / "entitlements.plist").read_bytes())
    assert ent == {"com.apple.security.cs.allow-jit": True,
                   "com.apple.security.cs.allow-unsigned-executable-memory": True,
                   "com.apple.security.cs.disable-library-validation": True}


def test_build_constraints_match_lock():
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    m = re.search(r'name = "setuptools"\nversion = "([^"]+)"', lock)
    pinned = (ROOT / "ci" / "build-constraints.txt").read_text(encoding="utf-8")
    assert m and f"setuptools=={m.group(1)} \\" in pinned
    assert pinned.count("--hash=sha256:") >= 1
