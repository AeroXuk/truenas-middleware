import json
import os
import tempfile

from middlewared.rclone.base import BaseRcloneRemote


class GoogleCloudStorageRcloneRemote(BaseRcloneRemote):
    name = "GOOGLE_CLOUD_STORAGE"
    title = "Google Cloud Storage"

    buckets = True

    fast_list = True

    rclone_type = "google cloud storage"

    task_attributes = ["bucket_policy_only"]

    def get_credentials_extra(self, credentials):
        return dict(
            service_account_credentials=(credentials["provider"]["service_account_credentials"].
                                         replace("\r", "").
                                         replace("\n", "")),
            project_number=json.loads(credentials["provider"]["service_account_credentials"])["project_id"],
        )

    def get_restic_config(self, task):
        provider = task["credentials"]["provider"]
        attrs = task["attributes"]
        bucket = attrs["bucket"]
        folder = attrs.get("folder", "").strip("/")
        path = f"/{folder}" if folder else "/"

        creds_json = provider["service_account_credentials"]
        project_id = json.loads(creds_json)["project_id"]

        # restic requires GOOGLE_APPLICATION_CREDENTIALS to point to a file.
        # Write a temp file; it is cleaned up by the OS on reboot.
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        try:
            tmp.write(creds_json)
        finally:
            tmp.close()

        env = {
            "GOOGLE_APPLICATION_CREDENTIALS": tmp.name,
            "GOOGLE_PROJECT_ID": project_id,
        }
        return f"gs:{bucket}:{path}", env
