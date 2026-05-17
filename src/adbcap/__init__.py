"""ADB Capture — Screenshot and screen recording tool for ADB-connected Android devices."""

from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("adbcap")
except PackageNotFoundError:
    __version__ = "0.0.0"
