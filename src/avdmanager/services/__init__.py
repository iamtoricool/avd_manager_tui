"""Service layer.

Services orchestrate: they call the runner, hand work to the backends, and
parse results off the event loop. They never touch the filesystem or spawn
processes directly, and they never import from :mod:`avdmanager.ui`.
"""

from avdmanager.services.avd import AvdService, CreateRequest, validate_avd_name
from avdmanager.services.emulator import EmulatorService, EmulatorState
from avdmanager.services.sdk import PackageLoad, SdkService

__all__ = [
    "AvdService",
    "CreateRequest",
    "EmulatorService",
    "EmulatorState",
    "PackageLoad",
    "SdkService",
    "validate_avd_name",
]