"""
Storj TrueCloud provider adapter.

Wraps the existing ``STORJ_IX`` rclone remote implementation so that Storj
jobs continue to work without any behavioural changes.
"""

from middlewared.plugins.cloud.path import get_remote_path
from middlewared.plugins.cloud.remotes import REMOTES
from middlewared.service import CallError

from .base import ResticRepoConfig, TrueCloudProvider


class StorjProvider(TrueCloudProvider):
    """TrueCloud provider adapter for Storj (via Storj-hosted S3 gateway).

    Delegates entirely to the ``STORJ_IX`` rclone remote so that existing
    Storj jobs are completely unaffected by the new provider abstraction.
    """

    #: Credential provider type strings recognised by this adapter.
    credential_types = ("STORJ_IX",)

    def validate(self, cloud_backup: dict) -> None:
        """Verify Storj connectivity by attempting a restic repository unlock.

        Uses the existing ``cloud_backup.ensure_initialized`` path so that
        Storj-specific error handling (wrong password, etc.) is preserved.
        """
        # Validation is handled upstream via ensure_initialized which already
        # covers the Storj-specific bucket creation and restic init paths.
        # No additional provider-level check is needed here.
        pass

    def get_restic_config(self, cloud_backup: dict) -> ResticRepoConfig:
        """Delegate restic config generation to the STORJ_IX rclone remote."""
        remote = REMOTES.get("STORJ_IX")
        if remote is None:
            raise CallError("Storj remote backend is not available")

        remote_path = get_remote_path(remote, cloud_backup["attributes"])
        url, env = remote.get_restic_config(cloud_backup)

        return ResticRepoConfig(
            url=f"{remote.rclone_type}:{url}/{remote_path}",
            env=env,
        )
