"""App version. Release builds write phosphor/_build_version.py from the git tag
(see build.bat and .github/workflows/release.yml); running from source reports 'dev'."""
try:
    from ._build_version import VERSION
except ImportError:
    VERSION = "dev"
