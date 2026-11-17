"""Which driver a ``runtimes.<name>`` block selects: one ``kind`` → one class."""

from __future__ import annotations

from collections.abc import Callable

from grove.core.config import (
    AcaFunctionsEpisodicRuntime,
    AcaJobRuntime,
    CloudRunEpisodicRuntime,
    CloudRunJobRuntime,
    EpisodicRuntimeConfig,
    LambdaEpisodicRuntime,
    LambdaMicrovmRuntime,
    OpenFaasRuntime,
    OpenSandboxRuntime,
    ResidentRuntimeConfig,
)
from grove.core.runtimes import EpisodicDriver, ServerlessDriver
from grove.core.runtimes.aca import AcaJobDriver
from grove.core.runtimes.aca_functions_episodic import AcaFunctionsEpisodicDriver
from grove.core.runtimes.cloud_run import CloudRunJobDriver
from grove.core.runtimes.cloud_run_episodic import CloudRunEpisodicDriver
from grove.core.runtimes.lambda_episodic import LambdaEpisodicDriver
from grove.core.runtimes.lambda_microvm import LambdaMicrovmDriver
from grove.core.runtimes.openfaas import OpenFaasDriver
from grove.core.runtimes.opensandbox import OpenSandboxDriver

#: Builds the driver for one named ``runtimes.<name>`` block.
type DriverFactory = Callable[[str, ResidentRuntimeConfig], ServerlessDriver]


def build_driver(name: str, cfg: ResidentRuntimeConfig) -> ServerlessDriver:
    """The resident driver class each config ``kind`` selects."""
    match cfg:
        case CloudRunJobRuntime():
            return CloudRunJobDriver(name, cfg)
        case AcaJobRuntime():
            return AcaJobDriver(name, cfg)
        case OpenFaasRuntime():
            return OpenFaasDriver(name, cfg)
        case LambdaMicrovmRuntime():
            return LambdaMicrovmDriver(name, cfg)
        case OpenSandboxRuntime():
            return OpenSandboxDriver(name, cfg)


def build_episodic_driver(name: str, cfg: EpisodicRuntimeConfig) -> EpisodicDriver:
    """The episodic driver class each config ``kind`` selects."""
    match cfg:
        case CloudRunEpisodicRuntime():
            return CloudRunEpisodicDriver(name, cfg)
        case LambdaEpisodicRuntime():
            return LambdaEpisodicDriver(name, cfg)
        case AcaFunctionsEpisodicRuntime():
            return AcaFunctionsEpisodicDriver(name, cfg)


__all__ = ["DriverFactory", "build_driver", "build_episodic_driver"]
