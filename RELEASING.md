# Releasing

Everything lives in one repository,
[romkey/hass-badgemagic](https://github.com/romkey/hass-badgemagic), and only the
Home Assistant integration is released. Nothing is published to PyPI.

| Part | Folder | Released as |
|---|---|---|
| Home Assistant integration | `custom_components/badgemagic/` | GitHub releases, installed through HACS |
| `badgemagic_ble` library and `badgemagic` CLI | `badgemagic-ble/` | Not released separately: bundled in the integration, and installable from GitHub |

The integration's version lives in `custom_components/badgemagic/manifest.json`.
The library's lives in `badgemagic-ble/src/badgemagic_ble/__init__.py` and travels
with the bundled copy.

## The bundled library

The integration imports its own copy of the library from
`custom_components/badgemagic/badgemagic_ble/`, so its manifest lists no
requirements and Home Assistant installs nothing for it. That copy is generated:

```bash
python3 scripts/sync_library.py
```

Run it after every library change and commit the result. `--check` reports drift
without changing anything. The `Tests` and `Release` workflows run the check and
fail if the copy is out of date.

## Workflows

| Workflow | Runs on | Does |
|---|---|---|
| `tests.yml` | every push | Bundled-library check, then the integration tests |
| `library.yml` | pushes touching `badgemagic-ble/` | Library tests on Python 3.13 and 3.14, and a build |
| `validate.yml` | every push, weekly | hassfest and the HACS action (brands check skipped) |
| `release.yml` | `v*` tags | Bundled-library check, then a GitHub release with the integration zipped |

## Release the integration

1. Make sure the bundled copy is current and the `Tests` and `Validate` workflows
   pass on `main`.
2. Bump `version` in `manifest.json` if needed, commit, and push.
3. Tag and push the tag:

```bash
git tag v0.2.0
```

```bash
git push origin v0.2.0
```

`release.yml` checks that the tag matches `manifest.json` and creates the GitHub
release. HACS offers releases as updates. Without any release, HACS installs from
the default branch.

## Library changes

1. Change `badgemagic-ble/src/badgemagic_ble/` and its tests.
2. Bump `__version__` in `badgemagic-ble/src/badgemagic_ble/__init__.py` when the
   behavior changes.
3. Run `python3 scripts/sync_library.py`, commit both folders together, and
   release the integration as above.
