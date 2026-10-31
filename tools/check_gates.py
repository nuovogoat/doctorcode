#!/usr/bin/env python3
"""Repository gates for DoctorCode.

The script is intentionally dependency-free and headless: it runs in CI before
any GUI or Android tooling is available.  Each gate is a small, named check so
a failure tells the reviewer exactly which contract was broken.

Usage:
    python tools/check_gates.py
    python tools/check_gates.py --commit-msg .git/COMMIT_EDITMSG
    python tools/check_gates.py --message "Short subject"
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
import tomllib
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from string import Formatter

ROOT = Path(__file__).resolve().parents[1]
COMMIT_SUBJECT_LIMIT = 72
ANDROID_XML_NAMESPACE = "{http://schemas.android.com/apk/res/android}"

CYRILLIC = re.compile(r"[А-Яа-яЁё]")
LINK = re.compile(r"!?\[[^\]]*]\(([^)]+)\)")
TEST_REF = re.compile(r"`(tests/[A-Za-z0-9_./-]+\.py)::([A-Za-z0-9_]+)`")
REGRESSION_ROW = re.compile(r"^\|\s*(R-\d{3})\s*\|")
VERSION_ASSIGNMENT = re.compile(r'__version__\s*=\s*"([^"]+)"')
PROPERTY_VERSION = re.compile(r'PROPERTY_VERSION\s*=\s*b"([^"]+)"')
ANDROID_VERSION = re.compile(r'versionName\s*=\s*"([^"]+)"')
ANDROID_PLACEHOLDER = re.compile(r"%(?:\d+\$)?[-#+ 0,(]*\d*(?:\.\d+)?([a-zA-Z])")

# v2.9 §0.10 / §8.2: canonical spellings of the product's own name.
# Product name (UI, runtime, docs prose): "DoctorCode" (camel-case, no separators).
# Package / distribution / CLI prefix: "doctorcode" (all-lowercase, no separators).
# Anything else is a typo the gate must catch.
PRODUCT_NAME_CANON = "DoctorCode"
PACKAGE_NAME_CANON = "doctorcode"

# Tokens that legitimately contain "doctor" + "code" with separators or in a
# different case. These are NOT the product's own identifiers and must not be
# flagged by the proper-name gate.
PROPER_NAME_ALLOWED_TOKENS = (
    "doctor_code_ai_connect",
    "doctor-code-ai-connect",
    "DoctorCode AI Connect",
)


@dataclass
class Gate:
    name: str
    failures: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def _relative(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _placeholder_names(template: str) -> set[str]:
    names: set[str] = set()
    try:
        for _literal, name, _spec, _conversion in Formatter().parse(template):
            if name:
                names.add(name)
    except ValueError:
        names.add("<malformed>")
    return names


def check_i18n(root: Path) -> Gate:
    """ru/en/de packs must have identical keys, placeholders, and flags."""
    gate = Gate("i18n")
    sys.path.insert(0, str(root))
    try:
        from doctorcode.i18n import PACKS
    except Exception as exc:  # pragma: no cover - import failure is the failure
        gate.failures.append(f"cannot import doctorcode.i18n: {exc}")
        return gate

    base_language = "ru"
    base = PACKS.get(base_language, {})
    if not base:
        gate.failures.append(f"{base_language} pack is missing or empty")
        return gate
    for language, messages in PACKS.items():
        missing = sorted(set(base) - set(messages))
        extra = sorted(set(messages) - set(base))
        if missing:
            gate.failures.append(f"{language}: missing keys: {', '.join(missing)}")
        if extra:
            gate.failures.append(f"{language}: extra keys: {', '.join(extra)}")
        empty = sorted(key for key, value in messages.items() if not str(value).strip())
        if empty:
            gate.failures.append(f"{language}: empty values: {', '.join(empty)}")
        if f"language.flag.{language}" not in messages:
            gate.failures.append(f"{language}: language.flag.{language} is missing")

    for key in sorted(set(base)):
        expected = _placeholder_names(str(base[key]))
        for language, messages in PACKS.items():
            actual = _placeholder_names(str(messages.get(key, "")))
            if actual != expected:
                gate.failures.append(
                    f"{language}: placeholders for {key} are {sorted(actual)}, "
                    f"expected {sorted(expected)}"
                )
    return gate


def _android_strings(root: Path, language: str) -> dict[str, str]:
    qualifier = "" if language == "en" else f"-{language}"
    path = root / "android" / "app" / "src" / "main" / "res" / f"values{qualifier}" / "strings.xml"
    if not path.is_file():
        raise FileNotFoundError(path)
    tree = ET.parse(path)
    values: dict[str, str] = {}
    for node in tree.getroot().findall("string"):
        name = node.get("name")
        if name:
            values[name] = "".join(node.itertext())
    return values


def _android_placeholders(template: str) -> list[str]:
    return sorted(ANDROID_PLACEHOLDER.findall(template))


def check_android_strings(root: Path) -> Gate:
    """The companion keeps the same ru/en/de key and format contract."""
    gate = Gate("android-i18n")
    try:
        packs = {language: _android_strings(root, language) for language in ("en", "ru", "de")}
    except (OSError, ET.ParseError) as exc:
        gate.failures.append(f"cannot read Android strings: {exc}")
        return gate
    base = packs["en"]
    if not base:
        gate.failures.append("English Android strings are empty")
        return gate
    for language, messages in packs.items():
        missing = sorted(set(base) - set(messages))
        extra = sorted(set(messages) - set(base))
        if missing:
            gate.failures.append(f"{language}: missing keys: {', '.join(missing)}")
        if extra:
            gate.failures.append(f"{language}: extra keys: {', '.join(extra)}")
        empty = sorted(key for key, value in messages.items() if not value.strip())
        if empty:
            gate.failures.append(f"{language}: empty values: {', '.join(empty)}")
    for key in sorted(base):
        expected = _android_placeholders(base[key])
        for language in ("ru", "de"):
            actual = _android_placeholders(packs[language].get(key, ""))
            if actual != expected:
                gate.failures.append(
                    f"{language}: format specifiers for {key} are {actual}, expected {expected}"
                )
    # Brand names and technical hints may stay identical; a Cyrillic Russian
    # value copied verbatim into German is a real missing translation.
    untranslated = sorted(
        key
        for key, value in packs["ru"].items()
        if CYRILLIC.search(value) and packs["de"].get(key) == value
    )
    if untranslated:
        gate.failures.append(f"German copy is untranslated for: {', '.join(untranslated)}")
    return gate


def check_launchers(root: Path) -> Gate:
    """Batch launchers must be relocatable, UTF-8, and pause on failure."""
    gate = Gate("launchers")
    launchers = sorted(root.glob("*.bat"))
    if not launchers:
        gate.failures.append("no .bat launchers found")
        return gate
    if not (root / "doctorcode_start.bat").is_file():
        gate.failures.append("doctorcode_start.bat is required")
    for path in launchers:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        lowered = text.casefold()
        for token in ("%~dp0", "%*", "chcp 65001", "pause"):
            if token not in lowered:
                gate.failures.append(f"{path.name}: missing {token}")
        if re.search(r"[A-Za-z]:\\", text):
            gate.failures.append(f"{path.name}: contains an absolute Windows path")
    return gate


def check_version_parity(root: Path) -> Gate:
    """One product version in Python metadata, mDNS, and the Android app."""
    gate = Gate("version")
    pyproject = root / "pyproject.toml"
    package = root / "doctorcode" / "__init__.py"
    discovery = root / "doctorcode" / "server" / "discovery.py"
    android = root / "android" / "app" / "build.gradle.kts"
    try:
        project_version = tomllib.loads(_text(pyproject))["project"]["version"]
        package_version = VERSION_ASSIGNMENT.search(_text(package))
        property_version = PROPERTY_VERSION.search(_text(discovery))
        android_version = ANDROID_VERSION.search(_text(android))
    except (OSError, KeyError, tomllib.TOMLDecodeError) as exc:
        gate.failures.append(f"cannot read versions: {exc}")
        return gate
    values = {
        "pyproject.toml": project_version,
        "doctorcode/__init__.py": package_version.group(1) if package_version else "",
        "doctorcode/server/discovery.py": property_version.group(1) if property_version else "",
        "android/app/build.gradle.kts": android_version.group(1) if android_version else "",
    }
    for source, value in values.items():
        if not value:
            gate.failures.append(f"{source}: version not found")
    if len(set(values.values())) > 1:
        gate.failures.append(
            "versions differ: " + ", ".join(f"{source}={value}" for source, value in values.items())
        )
    return gate


def check_regressions(root: Path) -> Gate:
    """Every REGRESSIONS.md row must point at an existing test function."""
    gate = Gate("regressions")
    path = root / "REGRESSIONS.md"
    if not path.is_file():
        gate.failures.append("REGRESSIONS.md is missing")
        return gate
    seen: set[str] = set()
    for line in _text(path).splitlines():
        match = REGRESSION_ROW.match(line)
        if not match:
            continue
        identifier = match.group(1)
        if identifier in seen:
            gate.failures.append(f"{identifier}: duplicate entry")
        seen.add(identifier)
        references = TEST_REF.findall(line)
        if not references:
            gate.failures.append(f"{identifier}: no `tests/file.py::test_name` reference")
            continue
        for relative, function in references:
            test_path = root / relative
            if not test_path.is_file():
                gate.failures.append(f"{identifier}: missing test file {relative}")
                continue
            try:
                tree = ast.parse(_text(test_path))
            except SyntaxError as exc:
                gate.failures.append(f"{identifier}: cannot parse {relative}: {exc}")
                continue
            functions = {
                node.name
                for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            if function not in functions:
                gate.failures.append(f"{identifier}: {relative} has no {function}()")
    if not seen:
        gate.failures.append("no R-xxx rows found")
    return gate


def check_bom(root: Path) -> Gate:
    """PowerShell files containing Cyrillic must be UTF-8 with BOM."""
    gate = Gate("bom")
    for path in sorted(root.rglob("*.ps1")):
        if any(part in {".git", ".venv", "build", "dist"} for part in path.parts):
            continue
        raw = path.read_bytes()
        if CYRILLIC.search(raw.decode("utf-8", errors="replace")) and not raw.startswith(
            b"\xef\xbb\xbf"
        ):
            gate.failures.append(f"{_relative(root, path)}: Cyrillic without UTF-8 BOM")
    return gate


def check_docs_links(root: Path) -> Gate:
    """Relative links in README must resolve, and the required docs must exist."""
    gate = Gate("docs")
    readme = root / "README.md"
    if not readme.is_file():
        gate.failures.append("README.md is missing")
        return gate
    for target in (
        "docs/ARCHITECTURE.md",
        "docs/SECURITY.md",
        "android/README.md",
        "STANDARDS.md",
        "DECISIONS.md",
        "REGRESSIONS.md",
    ):
        if not (root / target).is_file():
            gate.failures.append(f"required document is missing: {target}")
    for raw_target in LINK.findall(_text(readme)):
        target = raw_target.split("#", 1)[0].strip()
        if not target or "://" in target or target.startswith(("mailto:", "#")):
            continue
        if not (root / target).exists():
            gate.failures.append(f"README link does not resolve: {target}")
    return gate


def check_android_manifest(root: Path) -> Gate:
    """The companion must be a paired, non-backup, LAN-only cleartext client."""
    gate = Gate("android-manifest")
    path = root / "android" / "app" / "src" / "main" / "AndroidManifest.xml"
    if not path.is_file():
        gate.failures.append("AndroidManifest.xml is missing")
        return gate
    try:
        tree = ET.parse(path)
    except ET.ParseError as exc:
        gate.failures.append(f"cannot parse AndroidManifest.xml: {exc}")
        return gate
    manifest = tree.getroot()
    permissions = {
        node.get(f"{ANDROID_XML_NAMESPACE}name")
        for node in manifest.findall("uses-permission")
    }
    for permission in (
        "android.permission.INTERNET",
        "android.permission.RECORD_AUDIO",
    ):
        if permission not in permissions:
            gate.failures.append(f"missing permission {permission}")
    application = manifest.find("application")
    if application is None:
        gate.failures.append("missing <application>")
        return gate
    if application.get(f"{ANDROID_XML_NAMESPACE}allowBackup") != "false":
        gate.failures.append("allowBackup must be false")
    if application.get(f"{ANDROID_XML_NAMESPACE}usesCleartextTraffic") != "true":
        gate.failures.append("usesCleartextTraffic must be true for the LAN bridge")
    if not application.get(f"{ANDROID_XML_NAMESPACE}networkSecurityConfig"):
        gate.failures.append("networkSecurityConfig must be declared")
    activity = application.find("activity")
    if activity is None:
        gate.failures.append("missing <activity>")
    elif activity.get(f"{ANDROID_XML_NAMESPACE}exported") != "true":
        gate.failures.append("launcher activity must be exported")
    return gate


def check_commit_subject(message: str) -> Gate:
    """Commit subjects are capped at 72 characters (v2.8 §8.2)."""
    gate = Gate("commit-msg")
    lines = [line.strip() for line in message.splitlines() if line.strip()]
    if not lines:
        gate.failures.append("commit message is empty")
        return gate
    subject = lines[0]
    if subject.startswith("#"):
        gate.failures.append("commit subject starts with a comment marker")
    if len(subject) > COMMIT_SUBJECT_LIMIT:
        gate.failures.append(
            f"subject is {len(subject)} characters (limit {COMMIT_SUBJECT_LIMIT}): {subject}"
        )
    return gate


def check_proper_name(root: Path) -> Gate:
    """v2.9 §0.10/§8.2: product identifiers match the canonical spelling.

    Scans source, config, docs and tests for near-miss variants of the
    product's own name. Legitimate tokens (dependency names, historical
    citations) are allow-listed; everything else that looks like a typo
    of ``DoctorCode`` / ``doctorcode`` is flagged.
    """
    gate = Gate("proper-name")
    # Patterns that look like a misspelling of the product/package name.
    # Each tuple: (regex matching the suspect token, human label).
    suspects = [
        # Product name typos: wrong case, missing/extra char, separator inserted.
        (re.compile(r"\bDoct[oer]+[-_ ]?Code\b"), "product-name variant"),
        (re.compile(r"\bDoctor[-_ ]?[Cc]ode\b"), "product-name variant"),
        # Package/dist typos: lowercase with separator or wrong letter.
        (re.compile(r"\bdoct[oer]+[-_]code\b"), "package-name variant"),
        (re.compile(r"\bdoctor[-_]cod[e]?\b"), "package-name variant"),
    ]
    skip_dirs = {".git", ".venv", "venv", "__pycache__", ".pytest_cache",
                 ".ruff_cache", "node_modules", "build", "dist", ".egg-info"}
    skip_suffixes = {".pyc", ".pyo", ".png", ".jpg", ".jpeg", ".gif",
                     ".ico", ".woff", ".woff2", ".ttf", ".eot"}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in skip_dirs for part in path.parts):
            continue
        if path.suffix.lower() in skip_suffixes:
            continue
        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        rel = _relative(root, path)
        for pattern, label in suspects:
            for match in pattern.finditer(text):
                token = match.group(0)
                # Allow-listed tokens are legitimate (dependency, historical).
                if any(allowed in token for allowed in PROPER_NAME_ALLOWED_TOKENS):
                    continue
                # Exact canonical spellings are fine.
                if token in (PRODUCT_NAME_CANON, PACKAGE_NAME_CANON):
                    continue
                # Historical DECISIONS citations may reference old names;
                # only flag if the token is NOT inside a backtick-quoted
                # historical reference (rough heuristic).
                line_start = text.rfind("\n", 0, match.start()) + 1
                line_end = text.find("\n", match.end())
                if line_end == -1:
                    line_end = len(text)
                line = text[line_start:line_end]
                if f"`{token}`" in line and "DECISIONS.md" in rel:
                    continue
                gate.failures.append(f"{rel}: {label} '{token}' (expected "
                                     f"'{PRODUCT_NAME_CANON}' or '{PACKAGE_NAME_CANON}')")
    return gate


def run(root: Path) -> list[Gate]:
    return [
        check_i18n(root),
        check_android_strings(root),
        check_launchers(root),
        check_version_parity(root),
        check_regressions(root),
        check_bom(root),
        check_docs_links(root),
        check_android_manifest(root),
        check_proper_name(root),
    ]


def _print(gates: list[Gate]) -> None:
    for gate in gates:
        print(f"{'PASS' if gate.ok else 'FAIL'}  {gate.name}")
        for failure in gate.failures:
            print(f"      - {failure}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run DoctorCode repository gates.")
    parser.add_argument("--root", type=Path, default=ROOT, help="repository root")
    parser.add_argument("--commit-msg", type=Path, help="file containing a commit message")
    parser.add_argument("--message", help="commit message subject to check")
    args = parser.parse_args(argv)

    if args.commit_msg is not None:
        try:
            message = args.commit_msg.read_text(encoding="utf-8-sig")
        except OSError as exc:
            print(f"FAIL  commit-msg\n      - cannot read {args.commit_msg}: {exc}")
            return 1
        gates = [check_commit_subject(message)]
    elif args.message is not None:
        gates = [check_commit_subject(args.message)]
    else:
        gates = run(args.root)

    _print(gates)
    return 0 if all(gate.ok for gate in gates) else 1


if __name__ == "__main__":
    raise SystemExit(main())
