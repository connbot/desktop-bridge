# Changelog

## Unreleased

### Added
- A read-only setup doctor for Docker, Compose, configuration, and optional local readiness checks.
- First-success walkthroughs, deployment checkpoints, and troubleshooting guidance.
- Contribution and security guidance, bug/feature forms, and a pull-request checklist.
- Regression tests for delayed viewer status responses across logout and re-login.

### Changed
- Public product branding is now **Agent Computer**, with Muse-style computer use inside ChatGPT as the introduction.
- Documentation separates the execution environment from the AI client's reasoning, and distinguishes supported setup from unverified integrations.
- Docker Compose forwards the owner's explicit `BRIDGE_CODING_PERMISSION_MODE` setting. The default remains `safe`.

### Fixed
- Private takeover no longer prevents the authenticated owner from refreshing their file list; AI observations remain blocked.
- The sample app sidebar now uses the Agent Computer brand consistently with the viewer.
- A delayed status response could reopen the viewer after logout, or an old authentication failure could hide a newer logged-in session. Obsolete status responses are now ignored.

### Compatibility
- Existing `BRIDGE_*` configuration, `desktop_bridge` package, MCP tool names, API routes, and persistent data formats remain unchanged.
- This is still a single-owner self-hosted preview. No always-on model loop or scheduler is included.

## Initial preview

The initial implementation combined a Linux desktop, Chromium, Coding Tools MCP, OAuth, owner controls, persistent files, and optional context/MCP integrations. Earlier validation evidence is retained in [the verification record](docs/validation.md).
