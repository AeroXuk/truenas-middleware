"""
TrueCloud backup provider registry.

Maps Cloud Sync credential provider type strings to their corresponding
:class:`~.base.TrueCloudProvider` adapter classes.

Supported providers
-------------------
* ``STORJ_IX`` – Storj (existing behaviour, unchanged)
* ``S3``       – AWS S3 / Backblaze B2 S3-compatible / MinIO

Adding a new provider
---------------------
1. Implement a :class:`~.base.TrueCloudProvider` subclass in a new module
   under this package.
2. Add the credential type → class mapping to ``PROVIDERS`` below.
"""

from .base import ResticRepoConfig, TrueCloudProvider  # noqa: F401 – re-exported
from .s3 import S3Provider
from .storj import StorjProvider

# Registry: Cloud Sync credential ``provider.type`` → provider adapter class.
# Each class is instantiated lazily with the current middleware instance.
PROVIDERS: dict[str, type[TrueCloudProvider]] = {}

for _cls in (StorjProvider, S3Provider):
    for _ctype in _cls.credential_types:
        PROVIDERS[_ctype] = _cls


def get_provider(credential_type: str, middleware) -> TrueCloudProvider | None:
    """Return an instantiated provider adapter for *credential_type*, or
    ``None`` when the type is not in the registry.

    Args:
        credential_type: The ``provider.type`` string from a Cloud Sync
            credential, e.g. ``"S3"`` or ``"STORJ_IX"``.
        middleware: The active middleware instance passed to the provider.

    Returns:
        An instantiated :class:`TrueCloudProvider`, or ``None`` if the
        credential type is not supported by TrueCloud.
    """
    cls = PROVIDERS.get(credential_type)
    if cls is None:
        return None
    return cls(middleware)
