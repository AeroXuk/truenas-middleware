"""
Base provider interface for TrueCloud backup operations.

Each provider adapter translates Cloud Sync credentials and task attributes
into the runtime configuration required to drive restic.  This abstraction
keeps provider-specific logic out of the job orchestration layer and makes
it straightforward to add new providers in the future.

To add a new provider:
1. Subclass ``TrueCloudProvider`` and implement ``validate`` and
   ``get_restic_config``.
2. Register the subclass in ``plugins/cloud_backup/providers/__init__.py``
   under the matching Cloud Sync credential provider type string.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ResticRepoConfig:
    """Runtime configuration for a single restic repository connection.

    Attributes:
        url:  Repository URL passed to ``restic -r``, e.g.
              ``s3:s3.amazonaws.com/bucket/prefix``.
        env:  Environment variables required by the restic backend, e.g.
              ``AWS_ACCESS_KEY_ID`` / ``AWS_SECRET_ACCESS_KEY``.
    """

    url: str
    env: dict[str, str]


class TrueCloudProvider(ABC):
    """Abstract base class for TrueCloud backup provider adapters.

    Subclasses are responsible for two things:

    * ``validate`` – perform provider-specific connectivity / access checks
      before a job is created or executed so that errors surface early with
      actionable messages.
    * ``get_restic_config`` – translate credential and task attributes into a
      :class:`ResticRepoConfig` that the job orchestrator can pass directly to
      restic.
    """

    def __init__(self, middleware):
        self.middleware = middleware

    @abstractmethod
    def validate(self, cloud_backup: dict) -> None:
        """Validate provider connectivity and bucket access.

        Args:
            cloud_backup: Fully-extended cloud backup task dict (credentials
                are already resolved to their full dict form).

        Raises:
            :class:`middlewared.service.CallError`: with an actionable message
                describing the specific failure (auth, endpoint unreachable,
                bucket not found, etc.).
        """
        ...

    @abstractmethod
    def get_restic_config(self, cloud_backup: dict) -> ResticRepoConfig:
        """Return the restic repository URL and environment for this provider.

        Args:
            cloud_backup: Fully-extended cloud backup task dict.

        Returns:
            :class:`ResticRepoConfig` containing the ``-r`` URL and any
            environment variables required by the restic S3/B2/… backend.
        """
        ...
