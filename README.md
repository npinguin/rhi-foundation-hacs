# Robotix Home Intelligence — Foundation

Public HACS distribution mirror for the Home Assistant custom integration `rhi_foundation`.

The engineering source of truth is the private `npinguin/rhi-foundation` repository. This public repository contains only installable runtime/distribution content. The private engineering repository, contracts, models, tests and governance evidence are not published here.

## Installation with HACS

Add `https://github.com/npinguin/rhi-foundation-hacs` to HACS as a custom **Integration** repository, then download the desired version and restart Home Assistant.

- Default branch: current validated deployment candidate.
- GitHub releases: immutable validated package versions for test installation and rollback.
- Production approval is a separate private release-governance decision and is not implied by the presence of a public HACS version.

For the current pilot cycle, install **1.8.1** before installing Energy E0.12.1 or Mobility M0.7.3.

## Status

A published HACS version is an installable package candidate. It is not automatically an approved production release. Production approval remains subject to private RHI release governance, including clean install, upgrade, rollback, target Home Assistant runtime proof and bundle compatibility.

## License

The software and distribution content in this public HACS repository are licensed under the **GNU General Public License v3.0 only (GPL-3.0-only)**. See `LICENSE` for the full terms.

The separate private `npinguin/rhi-foundation` engineering repository remains proprietary. Its private contracts, models, tests, documentation and governance material are not relicensed merely because the installable runtime is published here under GPL-3.0-only.
