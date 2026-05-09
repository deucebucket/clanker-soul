# Changelog

All notable changes to `clanker-soul` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `CLAUDE.md` — guidance for Claude Code agents working in this repo.
- `CHANGELOG.md` — this file.
- `.github/` issue + PR templates.

## [0.1.0] — 2026-05-08

### Added
- Initial extraction from CARL.
- Three-layer VADUGWI runtime: `Score` (conversational), `EmotionalPhysics` (mood),
  `SoulState` + `SoulStore` (persistent baseline).
- `TraumaReservoir` and `NourishmentReservoir` with 14-day half-life.
- Host-agnostic `PulseEngine` driven by a `PulseHost` protocol.
- Test suite covering physics, soul, score, and pulse triggers.

[Unreleased]: https://github.com/deucebucket/clanker-soul/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/deucebucket/clanker-soul/releases/tag/v0.1.0
