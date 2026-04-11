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

        # restic requires GOOGLE_APPLICATION_CREDENTIALS to point to a file on
        # disk.  We write the service-account JSON to a deterministic temp path
        # derived from its content so the file is shared across repeated calls
        # for the same credentials and is automatically replaced when credentials
        # change.  The file is cleaned up by the OS on reboot.
        import hashlib
        creds_hash = hashlib.sha256(creds_json.encode()).hexdigest()[:16]
        creds_path = os.path.join(tempfile.gettempdir(), f"truenas_gcs_{creds_hash}.json")
        if not os.path.exists(creds_path):
            tmp = tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", dir=tempfile.gettempdir(), delete=False
            )
            try:
                tmp.write(creds_json)
            finally:
                tmp.close()
            os.replace(tmp.name, creds_path)

        env = {
            "GOOGLE_APPLICATION_CREDENTIALS": creds_path,
            "GOOGLE_PROJECT_ID": project_id,
        }
        return f"gs:{bucket}:{path}", env
