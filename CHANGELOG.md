# Changelog

Notable changes to `pi-galley` are documented here.

## [Unreleased]

### Added

- Added a global symbol that lets a tool hint how wide its images are drawn: at their natural size, or capped at a number of cells.

## [0.1.1] - 2026-11-11

### Fixed

- Fixed wide Mermaid diagrams staying in the galley, with a warning at session start, on Pi 1.5.1 and later, which renders them with `lovely-mermaid` instead of `grok-mermaid`. Earlier Pi versions keep working.

## [0.1.0] - 2026-10-12

### Added

- Added a measure for Pi's transcript, flush left or centered, with Mermaid diagrams breaking out when wider.
- Added settings that run tool output, the input editor, and the footer full width or in the galley.
- Added global symbols that let other extensions keep a block full width and read the current measure.
