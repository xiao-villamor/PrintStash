# Publicar PrintStash Model Importer en Chrome Web Store

Este documento prepara una primera publicación. La extensión requiere una
instancia de PrintStash; no incluye un servidor ni crea una cuenta de alojamiento.
La interfaz y los textos de la ficha están en inglés.

## Generar el archivo que se sube

En GitHub, abre **Actions → CI**, elige una ejecución y descarga
`printstash-browser-extension-<version>-chrome.zip` desde **Artifacts**.
El job **Browser extension** lo publica cuando pasan sus comprobaciones y lo
conserva durante 30 días. El archivo descargado es el ZIP de la extensión, listo
para subir a Chrome Web Store. Para generarlo a demanda, usa **Run workflow**
sobre `main`. También se genera en PRs, cambios en `main` y ejecuciones de CI
nocturnas o de release.

Para generarlo en tu equipo:

```bash
cd browser-extension
pnpm install --frozen-lockfile
pnpm package:chrome
```

El comando comprueba formato, lint, tipos, las pruebas de comportamiento y los
builds de Chrome, Firefox y Edge. Después genera el ZIP de Chrome y comprueba
su contenido real: manifiesto en la raíz, permisos, páginas, dependencias e iconos.

Sube **`.output/printstash-browser-extension-0.13.0-chrome.zip`**. El nombre
incorpora la versión de `package.json`. No subas el repositorio, el directorio
contenedor, un CRX ni el ZIP de código fuente. No se incluyen credenciales,
dependencias de desarrollo, mapas de código ni imágenes promocionales.

Para revisar el resultado, descomprime ese ZIP en una carpeta nueva y cárgala
desde `chrome://extensions` → **Developer mode → Load unpacked**. Empareja el
navegador con tu servidor y completa una captura hasta Pending Imports.
La [matriz de pruebas](STORE-VALIDATION.md) recoge la validación automatizada.

Para repetir la prueba de instalación automatizada, usa el navegador y WebDriver
descritos en `wdio.conf.ts` y ejecuta
`pnpm test:e2e --spec tests/e2e/loaded-extension.e2e.ts`. El empaquetado no descarga
ni inicia un navegador y no sustituye esta prueba.

## Textos de la ficha

**Name:** PrintStash Model Importer

**Summary:** Send models from MakerWorld, Printables, Thingiverse, or direct file links to PrintStash.

**Language:** English

**Description:**

```text
Bring the models you find online into your own PrintStash library.

PrintStash Model Importer connects your browser to a self-hosted PrintStash
server. Send a supported model page or direct file link to Pending Imports,
then review the files before adding them to your library.

• Pair your browser with a one-time code from PrintStash Settings → Imports.
• Capture individual MakerWorld models using your signed-in browser session.
• Capture Printables models and collections, with file selection when available.
• Select files from Thingiverse model pages, with manual attachment as a fallback.
• Send direct ZIP, 3MF, STL, OBJ, STEP and G-code links to your server.
• Review imported files and source information in Pending Imports.

You need a running PrintStash server and an account on it. This extension does
not provide cloud hosting. Localhost, LAN and HTTPS server addresses are supported.

Transfers happen when you request them. Keep the popup open until a transfer
finishes. Source sites may require sign-in or browser checks; the extension does
not bypass access restrictions. MakerWorld collections are not supported.

Your configured server receives the content you select. The extension has no
advertising or telemetry. Source-site cookies and session credentials are not
sent to PrintStash. Open Help & privacy in the popup for setup instructions,
permissions and the complete privacy policy.
```

**Support URL:** <https://github.com/xiao-villamor/PrintStash/issues>

**Homepage:** <https://www.printstash.org/en/>

**Privacy policy URL:** <https://www.printstash.org/en/extension-privacy/>

**Política en español:** <https://www.printstash.org/es/extension-privacy/>

## Privacy practices

**Single purpose:**

```text
Import user-selected 3D model files and their source information from supported
web pages into the Pending Imports inbox of the user's own PrintStash server.
```

Justificaciones para los permisos declarados:

| Campo            | Texto para la tienda                                                                                                                                                                                                                                                                                                                                                            |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| activeTab        | Identifies the current page when the user opens the importer and gives temporary access to that page for the requested capture. The extension does not monitor browsing in the background.                                                                                                                                                                                      |
| scripting        | Runs bundled extraction functions on the active supported model page to obtain allowlisted source metadata and file choices during a user-requested capture. No remotely hosted code is loaded or evaluated.                                                                                                                                                                    |
| storage          | Saves the configured PrintStash URL and browser device credential locally. The optional legacy setup saves a username and named API key. One-time pairing codes are not retained.                                                                                                                                                                                               |
| Host permissions | Loopback access connects to a PrintStash server on the user's own computer. Optional HTTP/HTTPS host access is needed because self-hosted PrintStash servers can use arbitrary hostnames, LAN addresses and ports. The extension requests specific server and provider hosts when needed for pairing or selected-file transfer; all-site access is not granted at installation. |

**Remote code:** No. Los scripts ejecutados se incluyen en el ZIP. Las respuestas
de los proveedores son datos y archivos, no código ejecutable descargado.

Declara los datos que procesa la extensión, aunque el destino sea el servidor
del usuario. Los campos aplicables son **Authentication information** (credencial
de dispositivo y configuración antigua), **Personally identifiable information**
(nombre de usuario en la configuración antigua), **Web history** (URL de la página
seleccionada, sin historial general) y **Website content** (metadatos y archivos
seleccionados). Comprueba que las declaraciones finales correspondan al binario
que envías; no marques «no se recogen datos» por el hecho de que sea self-hosted.

La política completa está en `entrypoints/help/index.html`, sección **Privacy
policy**, y en la página `help.html` del paquete. La versión pública vive en
`printstash-landing`, en inglés y español. Para el campo **Privacy policy URL**,
usa `https://www.printstash.org/en/extension-privacy/`.

Antes de enviar la ficha, comprueba que esa página carga sin iniciar sesión.
Una URL `chrome-extension://`, `localhost` o un archivo local no sirve. En futuras
versiones, actualiza la política incluida y la pública cuando cambie el tratamiento
de datos, incluidos sus destinatarios, retención, controles y contacto.

## Imágenes

La tienda requiere un icono de 128 × 128, al menos una captura y una imagen
promocional pequeña. Las capturas pueden medir 1280 × 800 o 640 × 400; la imagen
promocional pequeña mide 440 × 280. Las imágenes se suben por separado del ZIP.

El lote de entrega contiene en `.output/store-assets/`:

- `icon-128.png`: el icono de la extensión.
- `promo-440x280.png`: imagen promocional con la marca y el propósito.
- `01-pair-browser-1280x800.png`: captura del formulario real de emparejamiento.
- `02-help-privacy-1280x800.png`: captura real de la ayuda incluida.

Estos archivos son artefactos locales, no se versionan ni se regeneran con
`package:chrome`. Para futuras versiones, toma nuevas capturas del build instalado
sin credenciales ni datos privados. No añadas estrellas, reseñas o sellos de
aprobación ficticios a las imágenes.

## Instrucciones para el revisor

```text
This extension requires a self-hosted PrintStash server. It does not require an
account with the extension publisher. Installation instructions for the server
are available in the linked PrintStash repository README.

1. Start PrintStash locally and complete its initial account setup.
2. In PrintStash Settings → Imports, create a browser pairing code.
3. Open the extension, enter the local server URL and code, and click Pair browser.
4. Open a supported model page, reopen the extension, and select Send to Pending
   Imports. For MakerWorld, sign in on the source site first. Select files when
   prompted and keep the popup open until the transfer completes.
5. Open Pending Imports in PrintStash and review the captured item before import.
6. The Help & privacy link works without a server or a source-site account.

Provider challenges or sign-in restrictions are not bypassed. A manual-file
fallback is available where the provider cannot complete automatic capture.
```

Si Google solicita un entorno de prueba accesible, proporciona uno dedicado y
limitado en las instrucciones privadas del revisor; no publiques credenciales
del servidor habitual. Un código de emparejamiento caduca y no sirve como acceso
permanente para una revisión posterior.

## Subida

1. Abre el [panel de desarrolladores](https://chrome.google.com/webstore/devconsole)
   con tu cuenta y crea un elemento nuevo. Sube el ZIP de Chrome.
2. Completa la ficha con los textos y las imágenes anteriores.
3. Completa Privacy practices, la URL pública de privacidad y las instrucciones
   de prueba. Elige la distribución y envíalo a revisión cuando el panel esté completo.

La aprobación depende de Google. La versión del paquete es la versión del
proyecto; si ya hay una versión igual o superior en ese elemento de la tienda,
necesitas una nueva versión siguiendo el procedimiento de releases del repositorio.
No cambies solo el manifiesto ni publiques una versión anterior sobre la existente.

Referencias oficiales consultadas: [preparar el ZIP](https://developer.chrome.com/docs/webstore/prepare),
[campos de privacidad](https://developer.chrome.com/docs/webstore/cws-dashboard-privacy)
y [requisitos de imágenes](https://developer.chrome.com/docs/webstore/images).
