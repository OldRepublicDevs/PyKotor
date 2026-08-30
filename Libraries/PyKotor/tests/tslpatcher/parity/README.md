# TSLPatcher Parity Harness

Versioned corpus of TSLPatcher/HoloPatcher install scenarios indexed by GitHub issue number. Supports STRATEGY **TSLPatcher install parity** metric.

## Layout

```
parity/
  manifest.json          # case registry (stdlib JSON; no PyYAML dependency)
  runner.py              # loads INI, applies [HACKList], checks byte assertions
  test_parity_harness.py # parametrized pytest entry
  fixtures/<case>/patchdata/   # renamed to tslpatchdata/ at runtime (.gitignore-safe)
    changes.ini
    *.ncs                  # HACKList source files
```

## Run

From repo root:

```bash
QT_QPA_PLATFORM=offscreen uv run pytest --import-mode=importlib -m "not gui and not slow" --timeout=120 \
  Libraries/PyKotor/tests/tslpatcher/parity/test_parity_harness.py -v
```

## Add a case

1. Create `fixtures/<name>/patchdata/changes.ini` plus any source files.
2. Append an entry to `manifest.json`:

```json
{
  "id": "my_case",
  "issue": 83,
  "description": "What this reproduces",
  "fixture_dir": "my_case",
  "expect": "pass",
  "skip": false,
  "assertions": [
    {
      "type": "ncs_bytes_at_offset",
      "file": "Override/test.ncs",
      "offset": 16,
      "hex": "00bc614e"
    }
  ]
}
```

3. Run pytest on this directory.

## Planned cases (placeholders in manifest)

| Issue | Topic | Status |
|-------|-------|--------|
| #83 | `[HACKList]` | Active (official LongInt default, optional u16/u32 prefixes) |
| #55 | Required/RequiredMsg namespaces | Skipped — fixture TBD |
| #53 | GFF ListIndex / TypeId | Skipped — fixture TBD |
| #67 | Namespace script compile | TBD |
| #59 | Built-in vs nwnnsscomp compiler | TBD |

## Notes

- Runner uses library `ConfigReader` + `ModificationsNCS.apply` — no HoloPatcher submodule required.
- Unprefixed HACKList values match official TSLPatcher: 4-byte LongInt, big-endian on `.ncs`, little-endian otherwise. Optional `u8:`/`u16:`/`u32:` prefixes keep HoloPatcher width overrides (`0x10=u32:12345678`, not `VALUE:u32`).
- Manifest is JSON (stdlib) rather than YAML to avoid optional dependencies in minimal test runs.
- Use synthetic NCS buffers only unless mod redistribution is cleared.
- Classic TSLPatcher binary comparison on Windows is out of scope for this harness.
