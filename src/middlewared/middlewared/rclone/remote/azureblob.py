from middlewared.rclone.base import BaseRcloneRemote


class AzureBlobRcloneRemote(BaseRcloneRemote):
    name = "AZUREBLOB"
    title = "Microsoft Azure Blob Storage"

    buckets = True
    bucket_title = "Container"

    fast_list = True

    rclone_type = "azureblob"

    def get_task_extra(self, task):
        return {"chunk_size": "100Mi"}

    def get_restic_config(self, task):
        provider = task["credentials"]["provider"]
        attrs = task["attributes"]
        container = attrs["bucket"]
        folder = attrs.get("folder", "").strip("/")
        path = f"/{folder}" if folder else "/"
        env = {
            "AZURE_ACCOUNT_NAME": provider["account"],
            "AZURE_ACCOUNT_KEY": provider["key"],
        }
        if endpoint := provider.get("endpoint", "").strip():
            env["AZURE_ENDPOINT_URL"] = endpoint
        return f"azure:{container}:{path}", env
