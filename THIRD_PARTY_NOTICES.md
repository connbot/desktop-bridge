# Third-party components

Agent Computer's own source is Apache-2.0. Upstream projects retain their
copyrights and licenses. This repository imports packages; it does not claim
their implementation as original work.

- Cua computer-server 0.3.46: MIT package metadata; uses its VNC automation
  handler with a small X11 wheel adapter. https://github.com/trycua/cua
  Cua's entire monorepo is not uniformly licensed. Optional visual/model
  extensions are not included by this implementation.
- Coding Tools MCP, exact reviewed commit a2b802171bee1f990effa55f955efa2eddde4c59
  (pre-release fixes based on 0.5.0), Apache-2.0, xyTom and contributors:
  https://github.com/xyTom/coding-tools-mcp
- Playwright, Apache-2.0: https://github.com/microsoft/playwright
- MCP Python SDK, MIT: https://github.com/modelcontextprotocol/python-sdk
- noVNC, mainly MPL-2.0; its license document describes bundled portions:
  https://github.com/novnc/noVNC/blob/master/LICENSE.txt
- x11vnc, GPL: https://github.com/LibVNC/x11vnc
- Chromium, Xvfb, Openbox, fonts and system packages are obtained from Debian;
  package copyright notices are retained under /usr/share/doc in the image.

The Dockerfile builds a derived environment. If redistributing built images,
retain notices and comply with each package's source-distribution obligations.
The repository CI stores short-lived screenshots and test logs, not a public
binary release or a claim that every dependency has the same license.

## Optional initialization web service

- libsodium-wrappers 0.7.15 and the exact libsodium version resolved in
  `initializer/ui/package-lock.json`: ISC, Frank Denis and contributors.
  https://github.com/jedisct1/libsodium.js
  The generated, self-hosted `initializer/static/crypto.js` bundle includes this
  code; retain `initializer/licenses/` when redistributing the web service.
- PyNaCl 1.6.2: Apache-2.0, PyCA contributors. https://github.com/pyca/pynacl
- PyJWT 2.15.1: MIT. https://github.com/jpadilla/pyjwt
- esbuild 0.25.11: MIT, build-time only. https://github.com/evanw/esbuild
- jsdom 27.0.1: MIT, offline test-time only. https://github.com/jsdom/jsdom

No remote CDN, analytics script or third-party font is loaded by the initializer.
Its npm lockfile records exact versions and package integrity digests. The browser
crypto bundle is rebuilt using `npm --prefix initializer/ui ci --ignore-scripts`
and `npm --prefix initializer/ui run build`.
