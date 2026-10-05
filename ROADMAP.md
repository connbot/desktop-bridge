# Roadmap

Agent Computer brings the computer-use side of a personal agent to an existing AI client. The current focus is helping a new user reach a useful, verifiable result without guessing how to deploy or connect it.

This is a prioritization guide, not a delivery schedule or a claim that planned features exist.

## Current priorities

- Keep one clear, tested path from setup to a saved file and a visible browser action.
- Make configuration and connection failures understandable without exposing secrets.
- Maintain reproducible Docker, OAuth, browser, desktop, and restart checks.
- Show real execution in demos and state whether the driver is scripted or model-controlled.
- Make bug reports and small contributions easy to review.

## Candidates for the next iteration

- Broader host/CPU validation, with actual macOS, Windows Docker Desktop, and ARM results before claiming support.
- Better reconnect and authorization-expiry guidance, backed by client-specific tests.
- Easier workspace file transfer and task examples, guided by actual user reports.
- More compatible MCP integration examples after real protocol and account testing.

## Requires a separate design

Multi-user hosting, stronger isolation for untrusted code, autonomous background agents, schedulers, and inbound event handling are different responsibilities from providing a computer. They require dedicated threat models, ownership rules, and testing. They are not current capabilities or release promises.

## Help prioritize

Open a feature request with the task you want to complete, the obstacle you encountered, and your client/deployment. A reproducible example or a tested small improvement is more useful than an unbounded feature list. Read [CONTRIBUTING.md](CONTRIBUTING.md) before starting a larger change.
