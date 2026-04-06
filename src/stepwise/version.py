"""Semver version matching for kit includes.

Supports npm-style constraints (^, ~) translated to PEP 440 ranges,
plus standard PEP 440 specifiers (>=, ==, ~=, etc.).

Examples:
    ^1.2.3  → >=1.2.3,<2.0.0   (caret: compatible with major)
    ^0.2.3  → >=0.2.3,<0.3.0   (caret: leftmost non-zero)
    ^0.0.3  → >=0.0.3,<0.0.4
    ~1.2.3  → >=1.2.3,<1.3.0   (tilde: compatible with minor)
    ~1.2    → >=1.2.0,<1.3.0
    1.2.3   → ==1.2.3           (exact match)
    >=1.0   → >=1.0             (PEP 440 passthrough)
    *       → any version
"""

from packaging.specifiers import SpecifierSet, InvalidSpecifier
from packaging.version import Version, InvalidVersion


class VersionError(Exception):
    """Invalid version or constraint."""


def parse_version(version_str: str) -> Version:
    """Parse a version string into a Version object."""
    try:
        return Version(version_str)
    except InvalidVersion:
        raise VersionError(f"Invalid version: {version_str!r}")


def parse_constraint(constraint: str) -> SpecifierSet:
    """Parse a version constraint string into a SpecifierSet.

    Supports npm-style ^/~ operators and PEP 440 specifiers.
    """
    constraint = constraint.strip()

    if not constraint or constraint == "*":
        return SpecifierSet()  # matches everything

    # Caret: ^X.Y.Z
    if constraint.startswith("^"):
        return _caret_to_specifier(constraint[1:])

    # Tilde: ~X.Y.Z
    if constraint.startswith("~") and not constraint.startswith("~="):
        return _tilde_to_specifier(constraint[1:])

    # Exact version (bare number like "1.2.3")
    if constraint[0].isdigit():
        try:
            Version(constraint)
            return SpecifierSet(f"=={constraint}")
        except InvalidVersion:
            pass

    # PEP 440 passthrough (>=, ==, ~=, !=, <, >, etc.)
    try:
        return SpecifierSet(constraint)
    except InvalidSpecifier:
        raise VersionError(f"Invalid version constraint: {constraint!r}")


def version_matches(version_str: str, constraint: str) -> bool:
    """Check if a version string satisfies a constraint."""
    spec = parse_constraint(constraint)
    try:
        ver = Version(version_str)
    except InvalidVersion:
        return False
    return ver in spec


def _parse_parts(version_str: str) -> tuple[int, int, int]:
    """Parse X.Y.Z parts from a version string, defaulting missing parts to 0."""
    parts = version_str.split(".")
    try:
        major = int(parts[0]) if len(parts) > 0 else 0
        minor = int(parts[1]) if len(parts) > 1 else 0
        patch = int(parts[2]) if len(parts) > 2 else 0
    except ValueError:
        raise VersionError(f"Invalid version in constraint: {version_str!r}")
    return major, minor, patch


def _caret_to_specifier(version_str: str) -> SpecifierSet:
    """Convert ^X.Y.Z to a PEP 440 range.

    Caret allows changes that don't modify the leftmost non-zero digit.
    """
    major, minor, patch = _parse_parts(version_str)

    if major > 0:
        upper = f"{major + 1}.0.0"
    elif minor > 0:
        upper = f"0.{minor + 1}.0"
    else:
        upper = f"0.0.{patch + 1}"

    lower = f"{major}.{minor}.{patch}"
    return SpecifierSet(f">={lower},<{upper}")


def _tilde_to_specifier(version_str: str) -> SpecifierSet:
    """Convert ~X.Y.Z to a PEP 440 range.

    Tilde allows patch-level changes (>=X.Y.Z, <X.(Y+1).0).
    """
    major, minor, patch = _parse_parts(version_str)
    lower = f"{major}.{minor}.{patch}"
    upper = f"{major}.{minor + 1}.0"
    return SpecifierSet(f">={lower},<{upper}")
