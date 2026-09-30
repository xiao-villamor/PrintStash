# Original delivery after geometry failure — #259

The API must preserve original bytes independently of derivative outcomes through
both managed storage and indexed sources. Existing local upload cases do not
prove a remote redirect or source-reader path. These cases drive real Jobs through
the API and verify both the failed geometry precondition and downloaded SHA-256.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | preserves managed/mounted originals | Error | Geometry failure in local vault, mounted folder or real S3 vault | Download SHA equals original | E2E | ✅ TestMeshFailureRecovery.test_original_download_survives_geometry_failure |
| 2 | preserves managed/mounted slicer handoff | Error | Same storage modes, failed geometry, signed URL without session | Download SHA equals original | E2E | ✅ TestMeshFailureRecovery.test_signed_slicer_download_survives_geometry_failure |
| 3 | preserves remote-source originals | Error | Geometry failure in real Nextcloud, OpenSSH or S3 library source | Public download SHA equals source bytes | E2E | ✅ TestRefusedRemoteMesh.test_original_download_survives_geometry_failure |
| 4 | preserves remote-source slicer handoff | Error | Same remote transports, signed URL without session | Download SHA equals source bytes | E2E | ✅ TestRefusedRemoteMesh.test_signed_slicer_download_survives_geometry_failure |

Managed/mounted cases live in backend/tests/e2e/test_ingest.py; remote cases
live in backend/tests/e2e/test_remote_library_scan.py.

Normal S3 downloads follow the redirect over real TLS with the fixture's CA.
Slicer downloads exercise the API proxy that preserves their filename-bearing URL.
Remote discovery, materialization and parsing remain the production implementation. The tests
neither replace the mesh consumer nor mark a derivative failed by hand.

Focused local acceptance: six local/mounted recovery cases, two real S3-vault
cases and six real remote-source cases passed. Nextcloud installation exceeded
the fixture's 180-second startup budget on this WSL host; the successful local
rerun allowed 600 seconds for test-container startup only. The committed CI
startup budget and production mesh deadlines are unchanged. Ruff and Pyright passed.
