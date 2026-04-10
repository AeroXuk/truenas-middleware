"""
S3-compatible TrueCloud provider adapter.

Supports AWS S3, Backblaze B2 (via its S3-compatible endpoint), MinIO, and
any other S3-compatible object store that can be configured through a Cloud
Sync S3 credential.

Credential field mapping
------------------------
The adapter reads the following fields from the Cloud Sync S3 credential
provider dict (``cloud_backup["credentials"]["provider"]``):

* ``access_key_id``   – AWS / B2 / MinIO access key (required)
* ``secret_access_key`` – Corresponding secret (required)
* ``endpoint``        – Custom endpoint URL; empty string → AWS S3 (optional)
* ``region``          – AWS region; also consulted via task attributes (optional)

Task attributes (``cloud_backup["attributes"]``):

* ``bucket``  – Target bucket name (required when the credential type has
                ``buckets = True``, which S3 does)
* ``folder``  – Optional prefix / sub-path inside the bucket
* ``region``  – Per-task region override (takes precedence over credential)

Backblaze B2 notes
------------------
Set ``endpoint`` to the cluster-specific S3 endpoint URL, e.g.
``https://s3.us-west-001.backblazeb2.com``.  Restic will automatically use
path-style requests when a custom endpoint is provided, which is what B2
requires.  No additional configuration is needed beyond a valid access key
and secret.
"""

import errno
from urllib.parse import urlparse

from middlewared.plugins.cloud.path import get_remote_path
from middlewared.plugins.cloud.remotes import REMOTES
from middlewared.service import CallError

from .base import ResticRepoConfig, TrueCloudProvider


class S3Provider(TrueCloudProvider):
    """TrueCloud provider adapter for S3-compatible object stores.

    Delegates URL and environment construction to the ``S3`` rclone remote,
    then enriches the environment with the resolved region so that restic's
    embedded AWS SDK uses the correct signing endpoint.
    """

    #: Credential provider type strings recognised by this adapter.
    credential_types = ("S3",)

    def validate(self, cloud_backup: dict) -> None:
        """Verify S3 connectivity: credentials, endpoint reachability, bucket access.

        Performs a lightweight ``HEAD`` request against the target bucket using
        the boto3 S3 client so that errors are surfaced with actionable messages
        before the first restic call is attempted.

        Raises:
            :class:`middlewared.service.CallError`: on auth failure, endpoint
                unreachable, or bucket not found / access denied.
        """
        try:
            import botocore.exceptions
            import boto3
        except ImportError:
            # boto3 is an optional dependency; skip the connectivity check if
            # it is not installed (e.g. in minimal test environments).
            return

        provider = cloud_backup["credentials"]["provider"]
        attrs = cloud_backup["attributes"]

        endpoint = provider.get("endpoint", "").strip() or None
        region = (
            attrs.get("region")
            or provider.get("region", "").strip()
            or None
        )

        try:
            client = boto3.client(
                "s3",
                endpoint_url=endpoint,
                region_name=region,
                aws_access_key_id=provider["access_key_id"],
                aws_secret_access_key=provider["secret_access_key"],
            )
            client.head_bucket(Bucket=attrs["bucket"])
        except botocore.exceptions.ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            http_status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)
            if code in ("403", "AccessDenied") or http_status == 403:
                raise CallError(
                    f"Access denied to bucket {attrs['bucket']!r}: verify that the "
                    "access key has the required S3 permissions (s3:GetBucketLocation, "
                    "s3:ListBucket, s3:PutObject, s3:GetObject, s3:DeleteObject).",
                    errno.EACCES,
                )
            if code in ("404", "NoSuchBucket") or http_status == 404:
                raise CallError(
                    f"Bucket {attrs['bucket']!r} does not exist. "
                    "Create it first or check the bucket name.",
                    errno.ENOENT,
                )
            raise CallError(
                f"S3 validation failed for bucket {attrs['bucket']!r}: {exc}",
                # errno.EREMOTEIO is Linux-specific (available on TrueNAS/FreeBSD
                # via libc extension); fall back to errno.EIO on other platforms.
                errno.EREMOTEIO if hasattr(errno, "EREMOTEIO") else errno.EIO,
            )
        except botocore.exceptions.EndpointResolutionError as exc:
            raise CallError(
                f"Cannot reach S3 endpoint {endpoint!r}: {exc}",
                errno.ECONNREFUSED,
            )
        except botocore.exceptions.NoCredentialsError as exc:
            raise CallError(
                f"S3 credentials are missing or incomplete: {exc}",
                errno.EACCES,
            )
        except Exception as exc:
            raise CallError(
                f"S3 connectivity check failed: {exc}",
                errno.EIO,
            )

    def get_restic_config(self, cloud_backup: dict) -> ResticRepoConfig:
        """Build the restic repository URL and environment for S3.

        Delegates the URL and base environment to the ``S3`` rclone remote
        (which already handles AWS regions and custom endpoints correctly),
        then adds ``AWS_DEFAULT_REGION`` so that the restic AWS SDK uses the
        right signing region for bucket operations.

        The resulting URL is of the form::

            s3:<endpoint_or_aws_host>/<bucket>/<folder>

        For example::

            s3:s3.us-east-2.amazonaws.com/mybucket/backup
            s3:https://s3.us-west-001.backblazeb2.com/mybucket/backup
        """
        remote = REMOTES.get("S3")
        if remote is None:
            raise CallError("S3 remote backend is not available")

        remote_path = get_remote_path(remote, cloud_backup["attributes"])
        url, env = remote.get_restic_config(cloud_backup)

        # Ensure the AWS SDK inside restic uses the correct signing region.
        provider = cloud_backup["credentials"]["provider"]
        attrs = cloud_backup["attributes"]
        region = (
            attrs.get("region")
            or provider.get("region", "").strip()
            or "us-east-1"
        )
        env.setdefault("AWS_DEFAULT_REGION", region)

        return ResticRepoConfig(
            url=f"{remote.rclone_type}:{url}/{remote_path}",
            env=env,
        )
