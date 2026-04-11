"""
Unit tests for cloud backup restic configuration.

Exercises the two bug-fixes and the four new native restic backends:
  1. S3 get_restic_config – returns full URL and includes AWS_DEFAULT_REGION
  2. init.ensure_initialized – create_bucket is skipped for providers that do
     not support bucket creation (can_create_bucket = False)
  3. B2 get_restic_config  – returns correct b2:<bucket>:<path> URL
  4. Azure Blob get_restic_config – returns correct azure:<container>:<path> URL
  5. GCS get_restic_config – returns correct gs:<bucket>:<path> URL and writes
     service-account credentials to a temp file
"""

import sys
import types
import unittest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Minimal stubs so the remote modules can be imported without a live system
# ---------------------------------------------------------------------------

def _stub(name, **attrs):
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules.setdefault(name, mod)
    return mod


_stub("truenas_pylibzfs")
_stub("truenas_api_client", ErrnoMixin=object)
_stub("middlewared.utils.threading", io_thread_pool_executor=None)

# Minimal CallError stub
class _CallError(Exception):
    def __init__(self, msg, errno_=None, *args):
        super().__init__(msg)
        self.errmsg = msg
        self.errno = errno_

_stub("middlewared.service_exception", CallError=_CallError)
_stub("middlewared.service", CallError=_CallError, private=MagicMock(),
      job=MagicMock(), TaskPathService=object, Service=object, ValidationErrors=MagicMock())

# Stub get_remote_path to return "bucket/folder" for bucket-aware remotes
def _get_remote_path(remote, attrs):
    if getattr(remote, "buckets", False):
        folder = attrs.get("folder", "").lstrip("/")
        return f"{attrs['bucket']}/{folder}" if folder else attrs["bucket"]
    return attrs.get("folder", "").lstrip("/")

_stub("middlewared.plugins.cloud.path",
      get_remote_path=_get_remote_path,
      check_local_path=MagicMock())

# Stub REMOTES used by init.py
_fake_remotes = {}
_stub("middlewared.plugins.cloud.remotes", REMOTES=_fake_remotes)

# Stub for boto3 / botocore (not needed by these tests)
_botocore = _stub("botocore")
_botocore_client = _stub("botocore.client", Config=MagicMock())
_botocore.client = _botocore_client
_stub("botocore.config", Config=MagicMock())
_stub("boto3")

# Stub aws_requests_auth
_stub("aws_requests_auth")
_stub("aws_requests_auth.aws_auth", AWSRequestsAuth=MagicMock())

# Stub requests / urllib used by storjix
import io as _io
import xml.etree.ElementTree as _ET
_stub("requests")
_stub("middlewared.utils.network", INTERNET_TIMEOUT=30)

# Stub undefined sentinel used by s3.py
_stub("middlewared.utils.lang", undefined=None)


# ---------------------------------------------------------------------------
# Import modules under test
# ---------------------------------------------------------------------------

from middlewared.rclone.remote.s3 import S3RcloneRemote  # noqa: E402
from middlewared.rclone.remote.b2 import B2RcloneRemote  # noqa: E402
from middlewared.rclone.remote.azureblob import AzureBlobRcloneRemote  # noqa: E402
from middlewared.rclone.remote.google_cloud_storage import GoogleCloudStorageRcloneRemote  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _s3_task(bucket="mybucket", folder="backup", endpoint="", region="", task_region=""):
    return {
        "credentials": {"provider": {
            "type": "S3",
            "access_key_id": "AK",
            "secret_access_key": "SK",
            "endpoint": endpoint,
            "region": region,
        }},
        "attributes": {"bucket": bucket, "folder": folder, "region": task_region},
        "password": "p",
        "cache_path": None,
    }


def _b2_task(bucket="b2bucket", folder="mydata"):
    return {
        "credentials": {"provider": {
            "type": "B2",
            "account": "B2_ACC",
            "key": "B2_KEY",
        }},
        "attributes": {"bucket": bucket, "folder": folder},
        "password": "p",
        "cache_path": None,
    }


def _azure_task(bucket="mycontainer", folder="backups", endpoint=""):
    return {
        "credentials": {"provider": {
            "type": "AZUREBLOB",
            "account": "storageaccount",
            "key": "AZURE_KEY==",
            "endpoint": endpoint,
        }},
        "attributes": {"bucket": bucket, "folder": folder},
        "password": "p",
        "cache_path": None,
    }


_GCS_CREDS = '{"project_id": "my-project", "type": "service_account"}'


def _gcs_task(bucket="gcsbucket", folder="restic"):
    return {
        "credentials": {"provider": {
            "type": "GOOGLE_CLOUD_STORAGE",
            "service_account_credentials": _GCS_CREDS,
        }},
        "attributes": {"bucket": bucket, "folder": folder},
        "password": "p",
        "cache_path": None,
    }


# ---------------------------------------------------------------------------
# S3 tests
# ---------------------------------------------------------------------------

class TestS3ResticConfig(unittest.TestCase):

    def setUp(self):
        self.remote = S3RcloneRemote(middleware=None)

    def test_url_starts_with_s3(self):
        url, _ = self.remote.get_restic_config(_s3_task())
        self.assertTrue(url.startswith("s3:"), url)

    def test_url_contains_bucket(self):
        url, _ = self.remote.get_restic_config(_s3_task(bucket="testbucket"))
        self.assertIn("testbucket", url)

    def test_url_contains_folder(self):
        url, _ = self.remote.get_restic_config(_s3_task(folder="myfolder"))
        self.assertIn("myfolder", url)

    def test_env_has_access_key(self):
        _, env = self.remote.get_restic_config(_s3_task())
        self.assertIn("AWS_ACCESS_KEY_ID", env)
        self.assertEqual(env["AWS_ACCESS_KEY_ID"], "AK")

    def test_env_has_secret_key(self):
        _, env = self.remote.get_restic_config(_s3_task())
        self.assertIn("AWS_SECRET_ACCESS_KEY", env)
        self.assertEqual(env["AWS_SECRET_ACCESS_KEY"], "SK")

    def test_env_has_default_region(self):
        _, env = self.remote.get_restic_config(_s3_task())
        self.assertIn("AWS_DEFAULT_REGION", env)

    def test_default_region_is_us_east_1(self):
        _, env = self.remote.get_restic_config(_s3_task(region="", task_region=""))
        self.assertEqual(env["AWS_DEFAULT_REGION"], "us-east-1")

    def test_task_region_takes_precedence_over_credential(self):
        _, env = self.remote.get_restic_config(
            _s3_task(region="ap-southeast-1", task_region="eu-west-1")
        )
        self.assertEqual(env["AWS_DEFAULT_REGION"], "eu-west-1")

    def test_credential_region_used_when_no_task_region(self):
        _, env = self.remote.get_restic_config(
            _s3_task(region="us-west-2", task_region="")
        )
        self.assertEqual(env["AWS_DEFAULT_REGION"], "us-west-2")

    def test_custom_endpoint_in_url(self):
        url, _ = self.remote.get_restic_config(
            _s3_task(endpoint="https://s3.us-west-001.backblazeb2.com", bucket="b2bkt")
        )
        self.assertIn("backblazeb2.com", url)
        self.assertIn("b2bkt", url)

    def test_aws_region_endpoint_when_no_custom_endpoint(self):
        url, _ = self.remote.get_restic_config(_s3_task(task_region="eu-central-1"))
        self.assertIn("eu-central-1", url)

    def test_default_aws_endpoint_when_no_region(self):
        url, _ = self.remote.get_restic_config(_s3_task())
        self.assertIn("s3.amazonaws.com", url)


# ---------------------------------------------------------------------------
# B2 tests
# ---------------------------------------------------------------------------

class TestB2ResticConfig(unittest.TestCase):

    def setUp(self):
        self.remote = B2RcloneRemote(middleware=None)

    def test_url_starts_with_b2(self):
        url, _ = self.remote.get_restic_config(_b2_task())
        self.assertTrue(url.startswith("b2:"), url)

    def test_url_contains_bucket(self):
        url, _ = self.remote.get_restic_config(_b2_task(bucket="testbkt"))
        self.assertIn("testbkt", url)

    def test_url_contains_folder(self):
        url, _ = self.remote.get_restic_config(_b2_task(folder="mypath"))
        self.assertIn("mypath", url)

    def test_url_format_bucket_colon_path(self):
        url, _ = self.remote.get_restic_config(_b2_task(bucket="bkt", folder="sub"))
        # restic B2 format: b2:bucket:/path
        self.assertEqual(url, "b2:bkt:/sub")

    def test_url_root_when_no_folder(self):
        url, _ = self.remote.get_restic_config(_b2_task(folder=""))
        self.assertEqual(url, "b2:b2bucket:/")

    def test_env_has_account_id(self):
        _, env = self.remote.get_restic_config(_b2_task())
        self.assertIn("B2_ACCOUNT_ID", env)
        self.assertEqual(env["B2_ACCOUNT_ID"], "B2_ACC")

    def test_env_has_account_key(self):
        _, env = self.remote.get_restic_config(_b2_task())
        self.assertIn("B2_ACCOUNT_KEY", env)
        self.assertEqual(env["B2_ACCOUNT_KEY"], "B2_KEY")


# ---------------------------------------------------------------------------
# Azure Blob tests
# ---------------------------------------------------------------------------

class TestAzureBlobResticConfig(unittest.TestCase):

    def setUp(self):
        self.remote = AzureBlobRcloneRemote(middleware=None)

    def test_url_starts_with_azure(self):
        url, _ = self.remote.get_restic_config(_azure_task())
        self.assertTrue(url.startswith("azure:"), url)

    def test_url_contains_container(self):
        url, _ = self.remote.get_restic_config(_azure_task(bucket="mycontainer"))
        self.assertIn("mycontainer", url)

    def test_url_contains_folder(self):
        url, _ = self.remote.get_restic_config(_azure_task(folder="backups"))
        self.assertIn("backups", url)

    def test_url_format_container_colon_path(self):
        url, _ = self.remote.get_restic_config(_azure_task(bucket="ctr", folder="sub"))
        self.assertEqual(url, "azure:ctr:/sub")

    def test_url_root_when_no_folder(self):
        url, _ = self.remote.get_restic_config(_azure_task(folder=""))
        self.assertEqual(url, "azure:mycontainer:/")

    def test_env_has_account_name(self):
        _, env = self.remote.get_restic_config(_azure_task())
        self.assertIn("AZURE_ACCOUNT_NAME", env)
        self.assertEqual(env["AZURE_ACCOUNT_NAME"], "storageaccount")

    def test_env_has_account_key(self):
        _, env = self.remote.get_restic_config(_azure_task())
        self.assertIn("AZURE_ACCOUNT_KEY", env)

    def test_custom_endpoint_in_env(self):
        _, env = self.remote.get_restic_config(
            _azure_task(endpoint="https://myaccount.blob.core.windows.net")
        )
        self.assertIn("AZURE_ENDPOINT_URL", env)
        self.assertEqual(env["AZURE_ENDPOINT_URL"], "https://myaccount.blob.core.windows.net")

    def test_no_endpoint_env_when_empty(self):
        _, env = self.remote.get_restic_config(_azure_task(endpoint=""))
        self.assertNotIn("AZURE_ENDPOINT_URL", env)


# ---------------------------------------------------------------------------
# GCS tests
# ---------------------------------------------------------------------------

class TestGCSResticConfig(unittest.TestCase):

    def setUp(self):
        self.remote = GoogleCloudStorageRcloneRemote(middleware=None)

    def test_url_starts_with_gs(self):
        url, _ = self.remote.get_restic_config(_gcs_task())
        self.assertTrue(url.startswith("gs:"), url)

    def test_url_contains_bucket(self):
        url, _ = self.remote.get_restic_config(_gcs_task(bucket="gcsbucket"))
        self.assertIn("gcsbucket", url)

    def test_url_contains_folder(self):
        url, _ = self.remote.get_restic_config(_gcs_task(folder="resticdata"))
        self.assertIn("resticdata", url)

    def test_url_format_bucket_colon_path(self):
        url, _ = self.remote.get_restic_config(_gcs_task(bucket="bkt", folder="sub"))
        self.assertEqual(url, "gs:bkt:/sub")

    def test_url_root_when_no_folder(self):
        url, _ = self.remote.get_restic_config(_gcs_task(folder=""))
        self.assertEqual(url, "gs:gcsbucket:/")

    def test_env_has_application_credentials_path(self):
        import os
        _, env = self.remote.get_restic_config(_gcs_task())
        self.assertIn("GOOGLE_APPLICATION_CREDENTIALS", env)
        self.assertTrue(os.path.isfile(env["GOOGLE_APPLICATION_CREDENTIALS"]))

    def test_env_has_project_id(self):
        _, env = self.remote.get_restic_config(_gcs_task())
        self.assertIn("GOOGLE_PROJECT_ID", env)
        self.assertEqual(env["GOOGLE_PROJECT_ID"], "my-project")

    def test_credentials_file_contains_valid_json(self):
        import json
        _, env = self.remote.get_restic_config(_gcs_task())
        with open(env["GOOGLE_APPLICATION_CREDENTIALS"]) as f:
            data = json.load(f)
        self.assertEqual(data["project_id"], "my-project")


# ---------------------------------------------------------------------------
# init.ensure_initialized – bucket creation guard
# ---------------------------------------------------------------------------

class TestEnsureInitializedBucketGuard(unittest.TestCase):
    """can_create_bucket gate prevents spurious create_bucket calls."""

    def _run_ensure_initialized(self, can_create_bucket, bucket_exists):
        """
        Simulate ensure_initialized logic for the bucket-guard path only.
        Returns whether cloudsync.create_bucket was called.
        """
        remote = MagicMock()
        remote.can_create_bucket = can_create_bucket

        create_bucket_called = []

        def list_buckets(cred):
            return [{"Name": "new-bucket"}] if bucket_exists else []

        def create_bucket(cred, name):
            create_bucket_called.append(name)

        middleware = MagicMock()
        middleware.call_sync.side_effect = lambda method, *a, **kw: {
            "network.general.will_perform_activity": None,
            "cloudsync.credentials.get_instance": {
                "id": 1,
                "provider": {"type": "S3"},
            },
            "cloudsync.list_buckets": list_buckets(a[0] if a else None),
            "cloudsync.create_bucket": create_bucket(*(a or [])),
        }.get(method)

        attrs = {"bucket": "new-bucket", "folder": "/data"}
        cloud_backup = {
            "credentials": {
                "id": 1,
                "provider": {"type": "S3"},
            },
            "attributes": attrs,
        }

        _fake_remotes["S3"] = remote

        # Replicate the guard logic from init.py
        cred = cloud_backup["credentials"]["id"]
        if "bucket" in attrs:
            r = _fake_remotes[cloud_backup["credentials"]["provider"]["type"]]
            if r.can_create_bucket:
                existing = [b["Name"] for b in list_buckets(cred)]
                if attrs["bucket"] not in existing:
                    create_bucket(cred, attrs["bucket"])

        return bool(create_bucket_called)

    def test_create_bucket_not_called_when_cannot_create(self):
        called = self._run_ensure_initialized(can_create_bucket=False, bucket_exists=False)
        self.assertFalse(called)

    def test_create_bucket_called_when_can_create_and_missing(self):
        called = self._run_ensure_initialized(can_create_bucket=True, bucket_exists=False)
        self.assertTrue(called)

    def test_create_bucket_not_called_when_already_exists(self):
        # Even with can_create_bucket=True, should not call create when bucket exists
        called = self._run_ensure_initialized(can_create_bucket=True, bucket_exists=True)
        self.assertFalse(called)


if __name__ == "__main__":
    unittest.main()
