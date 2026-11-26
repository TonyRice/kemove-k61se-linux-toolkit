"""Shared host-side code for KEMOVE and AULA keyboards."""

from .devices import (
    AULA_VID,
    CDRV3632,
    G5KB,
    KEMOVE_VID,
    UNKNOWN,
    DeviceProfile,
    PROFILES,
    describe,
    identify,
    profile_for_key,
)

__all__ = [
    "AULA_VID",
    "CDRV3632",
    "G5KB",
    "KEMOVE_VID",
    "UNKNOWN",
    "DeviceProfile",
    "PROFILES",
    "describe",
    "identify",
    "profile_for_key",
]
