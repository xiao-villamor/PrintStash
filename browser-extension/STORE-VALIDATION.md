# Chrome Web Store preparation — coverage matrix

The publishing contract is a production MV3 ZIP that installs in Chrome, with
local help/privacy information and usable first-run pairing. Existing adapter
and transport tests continue to cover provider capture behavior.

| #   | Behaviour (test name)                                                              | Category | Precondition / input                               | Observable outcome asserted                                  | Tier          | Status                                                                                                          |
| --- | ---------------------------------------------------------------------------------- | -------- | -------------------------------------------------- | ------------------------------------------------------------ | ------------- | --------------------------------------------------------------------------------------------------------------- |
| 1   | submits supported Vault addresses through native form validation                   | Happy    | Loopback, LAN, HTTPS addresses                     | Normalized Vault and device credential saved                 | Frontend unit | ✅ `tests/popup.test.ts::submits supported Vault addresses through native form validation`                      |
| 2   | keeps an empty Vault address required                                              | Edge     | Empty address                                      | Native validation refuses submission                         | Frontend unit | ✅ `tests/popup.test.ts::keeps an empty Vault address required`                                                 |
| 3   | explains invalid connection input                                                  | Error    | Invalid URL, forbidden scheme, missing credentials | Visible explanation; no network request or saved credentials | Frontend unit | ✅ `tests/popup.test.ts::explains invalid connection input`                                                     |
| 4   | exposes local help from the popup                                                  | Happy    | First-run popup                                    | Local link targets a separate tab                            | Frontend unit | ✅ `tests/popup.test.ts::exposes local help from the popup`                                                     |
| 5   | packages a production MV3 manifest at the ZIP root                                 | Happy    | Chrome ZIP                                         | Release version, root manifest and popup valid               | Integration   | ✅ `tests/store/package.store.ts::packages a production MV3 manifest at the ZIP root`                           |
| 6   | includes every page dependency in the ZIP                                          | Happy    | Popup and help HTML                                | Every local reference exists; scripts bundled                | Integration   | ✅ `tests/store/package.store.ts::includes every page dependency in the ZIP`                                    |
| 7   | limits the ZIP to production assets                                                | Edge     | Chrome ZIP                                         | Only production asset paths present                          | Integration   | ✅ `tests/store/package.store.ts::limits the ZIP to production assets`                                          |
| 8   | preserves the intended permission surface                                          | Edge     | Chrome ZIP                                         | Required and optional permissions unchanged                  | Integration   | ✅ `tests/store/package.store.ts::preserves the intended permission surface`                                    |
| 9   | bundles help and privacy disclosures                                               | Happy    | Chrome ZIP                                         | Setup and privacy information present                        | Integration   | ✅ `tests/store/package.store.ts::bundles help and privacy disclosures`                                         |
| 10  | installs the manifest and opens a popup extension context                          | Happy    | Real Chrome installation                           | Popup exposes required browser APIs                          | E2E           | ✅ `tests/e2e/loaded-extension.e2e.ts::installs the manifest and opens a popup extension context`               |
| 11  | opens packaged help from the installed popup                                       | Happy    | Click Help & privacy in installed popup            | New extension tab renders the policy                         | E2E           | ✅ `tests/e2e/loaded-extension.e2e.ts::opens packaged help from the installed popup`                            |
| 12  | includes a correctly sized PNG icon                                                | Edge     | 16, 32, 48, 128 px icons                           | PNG signature and width/height match                         | Integration   | ✅ `tests/store/package.store.ts::includes a correctly sized PNG icon`                                          |
| 13  | omits Firefox-only settings                                                        | Edge     | Chrome and Edge builds                             | No browser_specific_settings key                             | Integration   | ✅ `tests/manifest.test.ts::omits Firefox-only settings`                                                        |
| 14  | keeps first-run setup focused on pairing                                           | Happy    | No saved connection                                | Pairing shown; capture controls hidden                       | Frontend unit | ✅ `tests/popup.test.ts::keeps first-run setup focused on pairing`                                              |
| 15  | asks users to keep the popup open during a transfer                                | Happy    | Capture started                                    | Visible reminder; capture button disabled                    | Frontend unit | ✅ `tests/popup.test.ts::asks users to keep the popup open during a transfer`                                   |
| 16  | declares the exact Firefox data collection categories for explicit Vault transfers | Edge     | Firefox build                                      | Gecko identity and disclosures preserved                     | Integration   | ✅ `tests/manifest.test.ts::declares the exact Firefox data collection categories for explicit Vault transfers` |

## Executed checks

- `pnpm package:chrome`: format, lint and typecheck passed; 159 behavior tests
  passed in 12 files; Chrome, Firefox and Edge production builds succeeded;
  10 ZIP tests passed. No extension coverage gate is configured.
- `pnpm test:e2e --spec tests/e2e/loaded-extension.e2e.ts`: 2 tests passed in
  installed Chrome for Testing 152.0.7977.8 with matching ChromeDriver.
  The final run installed the files extracted from the delivered ZIP, after
  packaging and copying completed.
- Visual inspection: light/dark first-run popup at 380 px; content height 521 px,
  footer visible within 600 px, no horizontal overflow. Help tested at 380 px
  and 1280 px; no page JavaScript errors in the inspected flow.
- Browser submission of an FTP Vault URL displays the HTTP/HTTPS recovery message.
- `git diff --check` passed.

The host blocks Chrome's Linux sandbox. Browser verification used a temporary,
dedicated test launcher with `--no-sandbox`; the extension package has no such
setting. The first browser run failed before installation for that reason.
A subsequent run exposed ChromeDriver's default omission of extension tabs;
the harness now enables `enableExtensionTargets`. One retry overlapped a build
and observed its temporarily removed help file; the final run followed build
completion and passed.

Live signed-in provider downloads and real-backend capture were not rerun:
this change does not alter extraction, transport or the server. Their current
unit/adapter fixtures passed, but that is not fresh evidence of provider uptime.
Firefox and Edge were built and their manifests tested; only Chrome was
installed for browser verification.

The public policy is maintained in `printstash-landing` at
`https://www.printstash.org/en/extension-privacy/`, with a Spanish version at
`https://www.printstash.org/es/extension-privacy/`. The separate site change
passes its checks and build. Store submission and Google's review are external
steps; preparing this package does not mean the store listing is published.
