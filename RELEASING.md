# Releasing

One repository, [romkey/hass-badgemagic](https://github.com/romkey/hass-badgemagic),
holds two projects that release separately:

| Project | Folder | Publishes to | Version lives in | Tag |
|---|---|---|---|---|
| `badgemagic-ble` library | `badgemagic-ble/` | PyPI | `badgemagic-ble/pyproject.toml` | `badgemagic-ble-vX.Y.Z` |
| Home Assistant integration | `custom_components/badgemagic/` | GitHub releases, installed through HACS | `custom_components/badgemagic/manifest.json` | `vX.Y.Z` |

HACS only looks at `custom_components/`, `hacs.json` and the README, so the
library folder does not get in its way.

Library 0.1.0 is what integration 0.2.0 pins. Neither has been released yet.

## Workflows

| Workflow | Runs on | Does |
|---|---|---|
| `tests.yml` | every push | Integration tests, with the library installed from this checkout |
| `library.yml` | pushes touching `badgemagic-ble/` | Library tests on Python 3.13 and 3.14, and a build |
| `validate.yml` | every push, weekly | hassfest and the HACS action (brands check skipped) |
| `library-publish.yml` | `badgemagic-ble-v*` tags | Library tests, build, upload to PyPI |
| `release.yml` | `v*` tags | GitHub release of the integration, zipped |

## 1. Publish the library to PyPI

`library-publish.yml` uses
[trusted publishing](https://docs.pypi.org/trusted-publishers/), so no API token
is stored anywhere.

1. On pypi.org, add a pending trusted publisher for the project `badgemagic-ble`:
   owner `romkey`, repository `hass-badgemagic`, workflow `library-publish.yml`,
   environment `pypi`.
2. In the GitHub repository settings, create an environment named `pypi`.
3. Tag and push:

```bash
git tag badgemagic-ble-v0.1.0
```

```bash
git push origin badgemagic-ble-v0.1.0
```

The workflow checks that the tag matches `pyproject.toml`, runs the tests, builds,
and uploads. To try a build locally first:

```bash
cd badgemagic-ble && uv build
```

## 2. Release the integration

Home Assistant installs the library version pinned in `manifest.json` from PyPI,
so publish the library first.

1. Check that the `Validate` workflow passes. The HACS check also wants a
   repository description and topics on GitHub.
2. Tag and push:

```bash
git tag v0.2.0
```

```bash
git push origin v0.2.0
```

`release.yml` checks that the tag matches `manifest.json` and creates a GitHub
release with the integration zipped. HACS offers releases as updates.

## Later releases

- **Library change:** bump `version` in `badgemagic-ble/pyproject.toml` and push a
  `badgemagic-ble-vX.Y.Z` tag. Bump the pin in `manifest.json` if the integration
  needs the new version.
- **Integration change:** bump `version` in `manifest.json` and push a `vX.Y.Z` tag.
