"""Typed presets: provider entries that reuse an existing transport.

A preset never certifies an appliance. Its ``defaults`` feed both typed parsing
and the generated role-specific forms; its ``transport`` names the transport
whose configuration model accepts it.
"""

from __future__ import annotations

from typing import Any

# Presets describe existing transports, never appliance certification. Defaults
# are shared by typed parsing and the generated role-specific forms.
PRESETS: dict[str, dict[str, Any]] = {
    "synology": {
        "label": "Synology — mounted folder",
        "transport": "local",
        "guide": "Mount an SMB/NFS share on the PrintStash host, then bind it into the container. Enter container paths, not a NAS URL. Existing models belong in a mounted Library source; backup folders use the mounted backup destination.",
        "url": "https://kb.synology.com/en-global/DSM/tutorial/How_to_access_files_on_Synology_NAS_within_the_local_network_NFS",
    },
    "truenas": {
        "label": "TrueNAS — mounted folder",
        "transport": "local",
        "guide": "Create an SMB or NFS share, mount it on the PrintStash host, and bind it into the container. Enter container paths. Existing models use a mounted Library source; backup folders use the mounted backup destination.",
        "url": "https://www.truenas.com/docs/scale/shares/nfs/addingnfsshares/",
    },
    "qnap": {
        "label": "QNAP — mounted folder",
        "transport": "local",
        "guide": "Mount the NAS share on the PrintStash host and bind it into the container. Enter container paths. Existing models use a mounted Library source; backup folders use the mounted backup destination. WebDAV is a separate connection choice.",
        "url": "https://www.qnap.com/en-us/how-to/tutorial/article/accessing-your-qnap-nas-remotely-with-webdav",
    },
    "unraid": {
        "label": "Unraid — mounted folder",
        "transport": "local",
        "guide": "Enable the share's SMB or NFS export and mount it on the PrintStash host, or bind a host share directory into the container. Existing models use a mounted Library source; backup folders use the mounted backup destination.",
        "url": "https://docs.unraid.net/unraid-os/using-unraid-to/manage-storage/shares/",
    },
    "synology_webdav": {
        "label": "Synology — WebDAV",
        "transport": "webdav",
        "guide": "Install and enable WebDAV Server. Enter your HTTPS WebDAV endpoint (default HTTPS port 5006), including the shared folder. Use a NAS account granted access to that folder; reverse proxy ports can differ.",
        "url": "https://kb.synology.com/en-sg/DSM/tutorial/How_to_fix_WebDAV_connection_issues",
    },
    "qnap_webdav": {
        "label": "QNAP — WebDAV",
        "transport": "webdav",
        "guide": "Enable WebDAV and the shared folder's WebDAV permissions. Copy the HTTPS endpoint and configured port from QTS, including the shared folder. Use an account with access to that folder; no universal NAS endpoint is assumed.",
        "url": "https://www.qnap.com/en-us/how-to/tutorial/article/accessing-your-qnap-nas-remotely-with-webdav",
    },
    "minio": {
        "label": "MinIO",
        "transport": "s3",
        "defaults": {"region": "us-east-1"},
        "guide": "Enter the S3 API endpoint, usually port 9000, not the console. Use a bucket-scoped access key and secret key. Region must match the server configuration; path-style addressing is the preset default.",
        "url": "https://github.com/minio/minio",
    },
    "garage": {
        "label": "Garage",
        "transport": "s3",
        "defaults": {"region": "garage"},
        "guide": "Enter the S3 API endpoint, usually port 3900, and the configured s3_region (commonly garage). Grant the access key access to the existing bucket. Path-style addressing is the preset default.",
        "url": "https://garagehq.deuxfleurs.fr/documentation/connect/cli/",
    },
    "seaweedfs": {
        "label": "SeaweedFS",
        "transport": "s3",
        "defaults": {"region": "us-east-1"},
        "guide": "Enter the S3 gateway endpoint, usually port 8333, with access and secret keys configured on the gateway. Configure authentication before exposing the service. Path-style addressing is the preset default.",
        "url": "https://github.com/seaweedfs/seaweedfs/wiki/Amazon-S3-API",
    },
    "hetzner_object_storage": {
        "label": "Hetzner Object Storage",
        "transport": "s3",
        "defaults": {"region": "fsn1"},
        "guide": "Choose the bucket location as the signing region (for example fsn1, nbg1 or hel1). The endpoint is https://REGION.your-objectstorage.com unless explicitly overridden. Use Object Storage access and secret keys, not Storage Box credentials.",
        "url": "https://docs.hetzner.com/storage/object-storage/getting-started/using-libraries/",
    },
    "hetzner_storage_box": {
        "label": "Hetzner Storage Box — SFTP",
        "transport": "sftp",
        "defaults": {"port": 23},
        "guide": "Enable SSH support for port 23, then enter the exact hostname and username from the Storage Box account or sub-account. Verify and pin its SSH host key out of band. Use the account password or a mounted private key. SFTP on port 22 can be selected explicitly.",
        "url": "https://docs.hetzner.com/storage/storage-box/access/access-overview/",
    },
    "hetzner_storage_box_webdav": {
        "label": "Hetzner Storage Box — WebDAV",
        "transport": "webdav",
        "guide": "Enable WebDAV, then enter https://HOSTNAME using the hostname and username assigned to the account or sub-account. HTTPS uses port 443. Use the Storage Box password and a dedicated folder.",
        "url": "https://docs.hetzner.com/storage/storage-box/access/access-overview/",
    },
    "koofr": {
        "label": "Koofr — WebDAV",
        "transport": "webdav",
        "defaults": {"endpoint_url": "https://app.koofr.net/dav/Koofr"},
        "guide": "Use your Koofr account email as username and generate an application-specific password for WebDAV. The endpoint is case-sensitive. The base folder is relative to the Koofr endpoint; do not repeat dav/Koofr in it.",
        "url": "https://koofr.eu/help/koofr_with_webdav/how-do-i-connect-a-service-to-koofr-through-webdav/",
    },
}
