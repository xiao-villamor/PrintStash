# NAS library-source evidence

Research captured on 2026-08-31 for the PrintStash 0.13 library-source guide.
This note records first-party platform evidence. It does not certify a NAS
model, firmware build, filesystem, network, or operator configuration.

## What PrintStash can test

PrintStash's automated contracts test the protocol clients it owns:

- native S3 against a real SeaweedFS S3-compatible service
- WebDAV through the production OpenDAL adapter against a real Nextcloud service
- SFTP through the production adapter against a real OpenSSH service, including
  pinned host-key rejection
- mounted-source behavior against real filesystem calls and controlled mount
  failure/replacement cases

Those contracts prove protocol behavior, pagination, mutation detection and
fail-closed handling. They cannot prove that every appliance release exposes a
share with the same ACLs, Docker implementation or filesystem semantics.

## Platform evidence

| Platform | First-party evidence | Supported deployment shape | Remaining operator proof |
| --- | --- | --- | --- |
| Unraid | [Container volume mappings connect a host path such as `/mnt/user/media` to a container path and can be read-only](https://docs.unraid.net/unraid-os/using-unraid-to/run-docker-containers/managing-and-customizing-containers/) | Bind a dedicated user-share directory into the API container and configure the container path as a mounted source | Confirm UID/GID permissions, marker persistence and behavior after array/share restart |
| Synology DSM | [Container Manager lets an operator specify a container volume mount path](https://kb.synology.com/en-eu/DSM/help/ContainerManager/docker_container); [DSM exposes shared folders over SMB and NFS](https://kb.synology.com/en-us/DSM/tutorial/How_to_access_files_on_Synology_DiskStation_within_the_Intranet) | Bind a DSM shared folder into Container Manager, or mount SMB/NFS on the Docker host and bind it into the API | Confirm the NAS model supports Container Manager, permissions, and mount recovery |
| TrueNAS SCALE | [Apps can mount an existing dataset as a host path, with an optional read-only flag and explicit ACL entries](https://apps.truenas.com/getting-started/app-storage/) | Add the dataset as a host-path volume to a custom app; use a dedicated dataset rather than the hidden app dataset | Confirm the app UID/GID ACL, snapshots/backups and host-path behavior on the installed SCALE release |
| OpenMediaVault | [Shared folders resolve to filesystem paths and can serve SMB, NFS and other services](https://docs.openmediavault.org/en/latest/administration/storage/sharedfolders.html); [containerized software is recommended](https://docs.openmediavault.org/en/7.x/plugins.html) | Resolve the shared-folder path, then bind that path into the PrintStash API Compose service | Confirm the Compose deployment method, POSIX permissions and mounted-device lifecycle |
| QNAP QTS / QuTS hero | [Container Station maps a NAS host path to a container mount point and lets the operator select access permissions](https://www.qnap.com/nl-nl/how-to/tutorial/article/how-to-use-container-station-2); [current Container Station supports QTS and QuTS hero](https://www.qnap.com/en-us/how-to/tutorial/article/container-station-quick-start-guide) | Map a dedicated shared folder, normally below `/share`, into the API container | Confirm the model/CPU supports Container Station, the actual `/share` path and ACL behavior |
| CasaOS / ZimaOS | [The official app format uses standard Docker Compose semantics](https://github.com/iceWhaleTech/CasaOS-AppStore/blob/main/docs/specs/compose-and-x-casaos.md); [official app manifests use explicit bind sources under `/DATA`](https://github.com/iceWhaleTech/CasaOS-AppStore/blob/main/Apps/Netdata/docker-compose.yml) | Add an explicit host-path bind to the PrintStash Compose definition; mount remote shares on the host before binding them | Confirm the imported Compose is preserved by the installed UI and do not rely on a named-volume conversion for network shares |
| Proxmox VE | [Proxmox supports NFS and CIFS storage, mounted by default below `/mnt/pve/<STORAGE_ID>`](https://pve.proxmox.com/pve-docs/pve-admin-guide.pdf) | Prefer a VM with Docker; mount the share inside the VM and bind it into PrintStash. An experienced operator may pass a host mount into an LXC | Confirm VM/LXC ownership mapping, backup scope and mount availability before the container starts |

## Conclusion

The common denominator is a Docker-visible path, native S3, WebDAV or SFTP.
PrintStash can claim support for those interfaces and publish platform-specific
mount recipes. It cannot honestly claim appliance certification until a named
model, OS/firmware version and repeatable validation log have exercised the
complete checklist in `docs/release-validation.md`.
