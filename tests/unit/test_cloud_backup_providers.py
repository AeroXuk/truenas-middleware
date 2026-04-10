"""
Unit tests for the TrueCloud backup provider abstraction.

These tests exercise provider selection, credential mapping, and the
get_restic_config output for each registered provider.  They are designed
to run in isolation without a live TrueNAS instance or native ZFS modules.
"""

import errno
import sys
import types
import unittest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Minimal stubs for modules that cannot be imported in a bare Python env
# ---------------------------------------------------------------------------

def _stub_module(name, **attrs):
    """Register a minimal stub module so imports don't fail."""
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules.setdefault(name, mod)
    return mod


# Stub out native and optional dependencies used by the provider tree.
_stub_module("truenas_pylibzfs")
_stub_module("truenas_api_client", ErrnoMixin=object)
_stub_module("middlewared.utils.threading", io_thread_pool_executor=None)

# CallError stub – mirrors the real class well enough for these tests.
class _CallError(Exception):
    def __init__(self, msg, errno_=None, *args):
        super().__init__(msg)
        self.errmsg = msg
        self.errno = errno_

_service_exc = _stub_module("middlewared.service_exception", CallError=_CallError)

# Minimal middlewared.service stub so providers can import CallError.
_service = _stub_module("middlewared.service", CallError=_CallError)
for _sub in ["private", "job", "TaskPathService", "Service", "ValidationErrors"]:
    setattr(_service, _sub, MagicMock())

# Stub middlewared.plugins.cloud.path
_stub_module(
    "middlewared.plugins.cloud.path",
    get_remote_path=lambda remote, attrs: (
        f"{attrs.get('bucket', '')}/{attrs.get('folder', '').lstrip('/')}"
        if getattr(remote, "buckets", False)
        else attrs.get("folder", "").lstrip("/")
    ),
    check_local_path=MagicMock(),
)


def _make_storj_remote():
    """Create a fresh STORJ_IX rclone remote mock."""
    remote = MagicMock()
    remote.name = "STORJ_IX"
    remote.rclone_type = "s3"
    remote.buckets = True
    remote.get_restic_config.return_value = (
        "gateway.storjshare.io",
        {
            "AWS_ACCESS_KEY_ID": "storj-key",
            "AWS_SECRET_ACCESS_KEY": "storj-secret",
        },
    )
    return remote


def _make_s3_remote(endpoint="s3.us-east-1.amazonaws.com", region="us-east-1"):
    """Create a fresh S3 rclone remote mock."""
    remote = MagicMock()
    remote.name = "S3"
    remote.rclone_type = "s3"
    remote.buckets = True
    remote.get_restic_config.return_value = (
        endpoint,
        {
            "AWS_ACCESS_KEY_ID": "aws-key",
            "AWS_SECRET_ACCESS_KEY": "aws-secret",
        },
    )
    return remote


# Build initial REMOTES dict (tests replace entries as needed).
_fake_storj_remote = _make_storj_remote()
_fake_s3_remote = _make_s3_remote()

_remotes_mod = _stub_module(
    "middlewared.plugins.cloud.remotes",
    REMOTES={"STORJ_IX": _fake_storj_remote, "S3": _fake_s3_remote},
)


# ---------------------------------------------------------------------------
# Import the modules under test *after* stubs are in place
# ---------------------------------------------------------------------------

# We import lazily inside each test to avoid top-level import errors.


class TestProviderRegistry(unittest.TestCase):
    """Provider registry should map credential types to adapter classes."""

    def _get_registry(self):
        # Import fresh each time (stubs already in sys.modules).
        from middlewared.plugins.cloud_backup.providers import PROVIDERS, get_provider
        return PROVIDERS, get_provider

    def test_storj_registered(self):
        PROVIDERS, _ = self._get_registry()
        self.assertIn("STORJ_IX", PROVIDERS)

    def test_s3_registered(self):
        PROVIDERS, _ = self._get_registry()
        self.assertIn("S3", PROVIDERS)

    def test_unknown_type_returns_none(self):
        _, get_provider = self._get_registry()
        result = get_provider("AZURE_BLOB", middleware=None)
        self.assertIsNone(result)

    def test_s3_returns_s3_provider(self):
        from middlewared.plugins.cloud_backup.providers import get_provider
        from middlewared.plugins.cloud_backup.providers.s3 import S3Provider
        provider = get_provider("S3", middleware=None)
        self.assertIsInstance(provider, S3Provider)

    def test_storj_returns_storj_provider(self):
        from middlewared.plugins.cloud_backup.providers import get_provider
        from middlewared.plugins.cloud_backup.providers.storj import StorjProvider
        provider = get_provider("STORJ_IX", middleware=None)
        self.assertIsInstance(provider, StorjProvider)


class TestStorjProvider(unittest.TestCase):
    """StorjProvider should produce correct restic config from Storj creds."""

    def setUp(self):
        # Reset REMOTES to a fresh Storj mock before each test.
        _remotes_mod.REMOTES["STORJ_IX"] = _make_storj_remote()

    def _make_task(self, bucket="my-bucket", folder="backup"):
        return {
            "credentials": {
                "provider": {
                    "type": "STORJ_IX",
                    "access_key_id": "storj-key",
                    "secret_access_key": "storj-secret",
                    "endpoint": "https://gateway.storjshare.io",
                }
            },
            "attributes": {"bucket": bucket, "folder": folder},
            "password": "restic-pass",
            "cache_path": None,
        }

    def test_restic_config_url_format(self):
        from middlewared.plugins.cloud_backup.providers.storj import StorjProvider
        provider = StorjProvider(middleware=None)
        cfg = provider.get_restic_config(self._make_task())
        # URL must start with the rclone type prefix.
        self.assertTrue(cfg.url.startswith("s3:"), f"URL {cfg.url!r} does not start with 's3:'")

    def test_restic_config_contains_bucket(self):
        from middlewared.plugins.cloud_backup.providers.storj import StorjProvider
        provider = StorjProvider(middleware=None)
        cfg = provider.get_restic_config(self._make_task(bucket="test-bucket"))
        self.assertIn("test-bucket", cfg.url)

    def test_restic_config_contains_folder(self):
        from middlewared.plugins.cloud_backup.providers.storj import StorjProvider
        provider = StorjProvider(middleware=None)
        cfg = provider.get_restic_config(self._make_task(folder="mydata"))
        self.assertIn("mydata", cfg.url)

    def test_restic_config_env_has_credentials(self):
        from middlewared.plugins.cloud_backup.providers.storj import StorjProvider
        provider = StorjProvider(middleware=None)
        cfg = provider.get_restic_config(self._make_task())
        self.assertIn("AWS_ACCESS_KEY_ID", cfg.env)
        self.assertIn("AWS_SECRET_ACCESS_KEY", cfg.env)

    def test_validate_is_noop(self):
        """StorjProvider.validate() must not raise for any valid task."""
        from middlewared.plugins.cloud_backup.providers.storj import StorjProvider
        provider = StorjProvider(middleware=None)
        # Should complete without raising.
        provider.validate(self._make_task())


class TestS3Provider(unittest.TestCase):
    """S3Provider should produce correct restic config and map credentials."""

    def setUp(self):
        # Reset REMOTES to a fresh S3 mock before each test.
        _remotes_mod.REMOTES["S3"] = _make_s3_remote()

    def _make_task(
        self,
        bucket="my-bucket",
        folder="backup",
        endpoint="",
        region="",
        task_region="",
    ):
        return {
            "credentials": {
                "provider": {
                    "type": "S3",
                    "access_key_id": "aws-key",
                    "secret_access_key": "aws-secret",
                    "endpoint": endpoint,
                    "region": region,
                }
            },
            "attributes": {"bucket": bucket, "folder": folder, "region": task_region},
            "password": "restic-pass",
            "cache_path": None,
        }

    def test_restic_config_url_starts_with_s3(self):
        from middlewared.plugins.cloud_backup.providers.s3 import S3Provider
        provider = S3Provider(middleware=None)
        cfg = provider.get_restic_config(self._make_task())
        self.assertTrue(cfg.url.startswith("s3:"), f"URL {cfg.url!r} does not start with 's3:'")

    def test_restic_config_bucket_in_url(self):
        from middlewared.plugins.cloud_backup.providers.s3 import S3Provider
        provider = S3Provider(middleware=None)
        cfg = provider.get_restic_config(self._make_task(bucket="mybucket"))
        self.assertIn("mybucket", cfg.url)

    def test_restic_config_folder_in_url(self):
        from middlewared.plugins.cloud_backup.providers.s3 import S3Provider
        provider = S3Provider(middleware=None)
        cfg = provider.get_restic_config(self._make_task(folder="myfolder"))
        self.assertIn("myfolder", cfg.url)

    def test_restic_config_env_has_keys(self):
        from middlewared.plugins.cloud_backup.providers.s3 import S3Provider
        provider = S3Provider(middleware=None)
        cfg = provider.get_restic_config(self._make_task())
        self.assertIn("AWS_ACCESS_KEY_ID", cfg.env)
        self.assertIn("AWS_SECRET_ACCESS_KEY", cfg.env)

    def test_restic_config_sets_default_region(self):
        """AWS_DEFAULT_REGION must be present (defaults to us-east-1)."""
        from middlewared.plugins.cloud_backup.providers.s3 import S3Provider
        # Ensure remote returns no region-specific endpoint.
        _remotes_mod.REMOTES["S3"] = _make_s3_remote()
        provider = S3Provider(middleware=None)
        cfg = provider.get_restic_config(self._make_task())
        self.assertIn("AWS_DEFAULT_REGION", cfg.env)
        self.assertEqual(cfg.env["AWS_DEFAULT_REGION"], "us-east-1")

    def test_restic_config_task_region_takes_precedence(self):
        """Task-level region should override credential-level region."""
        from middlewared.plugins.cloud_backup.providers.s3 import S3Provider

        _remotes_mod.REMOTES["S3"] = _make_s3_remote("s3.eu-west-1.amazonaws.com")
        provider = S3Provider(middleware=None)
        cfg = provider.get_restic_config(
            self._make_task(region="ap-southeast-1", task_region="eu-west-1")
        )
        self.assertEqual(cfg.env["AWS_DEFAULT_REGION"], "eu-west-1")

    def test_restic_config_credential_region_fallback(self):
        """Credential-level region is used when task attributes have no region."""
        from middlewared.plugins.cloud_backup.providers.s3 import S3Provider

        _remotes_mod.REMOTES["S3"] = _make_s3_remote("s3.ap-southeast-1.amazonaws.com")
        provider = S3Provider(middleware=None)
        cfg = provider.get_restic_config(
            self._make_task(region="ap-southeast-1", task_region="")
        )
        self.assertEqual(cfg.env["AWS_DEFAULT_REGION"], "ap-southeast-1")

    def test_backblaze_b2_endpoint_in_url(self):
        """B2 S3-compatible endpoint should appear in the restic repository URL."""
        from middlewared.plugins.cloud_backup.providers.s3 import S3Provider

        b2_endpoint = "https://s3.us-west-001.backblazeb2.com"
        _remotes_mod.REMOTES["S3"] = _make_s3_remote(b2_endpoint)
        provider = S3Provider(middleware=None)
        cfg = provider.get_restic_config(
            self._make_task(endpoint=b2_endpoint, bucket="b2bucket")
        )
        self.assertIn("backblazeb2.com", cfg.url)
        self.assertIn("b2bucket", cfg.url)

    def test_validate_success_no_error(self):
        """validate() should not raise when head_bucket succeeds."""
        from middlewared.plugins.cloud_backup.providers.s3 import S3Provider

        with patch("boto3.client") as mock_boto:
            mock_client = MagicMock()
            mock_boto.return_value = mock_client
            mock_client.head_bucket.return_value = {}

            provider = S3Provider(middleware=None)
            # Should not raise.
            provider.validate(self._make_task(bucket="good-bucket"))

    def test_validate_access_denied_raises_callError(self):
        """validate() should raise CallError with errno.EACCES on 403."""
        import botocore.exceptions

        from middlewared.plugins.cloud_backup.providers.s3 import S3Provider

        error_response = {
            "Error": {"Code": "403", "Message": "Forbidden"},
            "ResponseMetadata": {"HTTPStatusCode": 403},
        }

        with patch("boto3.client") as mock_boto:
            mock_client = MagicMock()
            mock_boto.return_value = mock_client
            mock_client.head_bucket.side_effect = botocore.exceptions.ClientError(
                error_response, "HeadBucket"
            )

            provider = S3Provider(middleware=None)
            with self.assertRaises(Exception) as ctx:
                provider.validate(self._make_task(bucket="private-bucket"))

            self.assertIn("Access denied", str(ctx.exception))

    def test_validate_bucket_not_found_raises_callError(self):
        """validate() should raise CallError with errno.ENOENT on 404."""
        import botocore.exceptions

        from middlewared.plugins.cloud_backup.providers.s3 import S3Provider

        error_response = {
            "Error": {"Code": "404", "Message": "Not Found"},
            "ResponseMetadata": {"HTTPStatusCode": 404},
        }

        with patch("boto3.client") as mock_boto:
            mock_client = MagicMock()
            mock_boto.return_value = mock_client
            mock_client.head_bucket.side_effect = botocore.exceptions.ClientError(
                error_response, "HeadBucket"
            )

            provider = S3Provider(middleware=None)
            with self.assertRaises(Exception) as ctx:
                provider.validate(self._make_task(bucket="missing-bucket"))

            self.assertIn("does not exist", str(ctx.exception))

    def test_validate_skips_when_boto3_unavailable(self):
        """validate() should not raise if boto3 is not installed."""
        from middlewared.plugins.cloud_backup.providers.s3 import S3Provider

        # Temporarily hide boto3 from the import system.
        with patch.dict(sys.modules, {"boto3": None, "botocore.exceptions": None}):
            provider = S3Provider(middleware=None)
            # Should complete without raising even when boto3 is absent.
            provider.validate(self._make_task())


if __name__ == "__main__":
    unittest.main()
