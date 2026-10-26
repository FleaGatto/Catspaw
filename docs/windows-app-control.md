# Windows App Control blocks Fleasion

Windows 11 users with Smart App Control, or any App Control policy, hit one of:

```
ImportError: DLL load failed while importing sip: An Application Control policy has blocked this file.
```

with a matching Code Integrity event:

| field                   | value                       |
|-------------------------|-----------------------------|
| Event ID                | 3077                        |
| Provider                | Microsoft-Windows-CodeIntegrity |
| Policy                  | `VerifiedAndReputableDesktop` |
| Status                  | `0xc0e90002`                |
| Requested signing level | 2                           |
| Validated signing level | 1                           |

Reported as upstream [#82](https://github.com/fleasion/Fleasion/issues/82)
(`PyQt6\sip.cp314-win_amd64.pyd`),
[#84](https://github.com/fleasion/Fleasion/issues/84)
(`PIL\_imaging.cp314-win_amd64.pyd`) and
[#79](https://github.com/fleasion/Fleasion/issues/79)
(`libportaudio64bit.dll`).

## Why it happens

The policy evaluates **every native module the process loads**, not just the
executable. A `.pyd` or `.dll` that is neither signed by a trusted publisher nor
known-good to Microsoft's reputation service is refused at load time, which
surfaces in Python as an `ImportError`. Moving the executable between folders
changes nothing, because the check is per file, not per path.

The blocked modules do not come from this project. They ship unsigned inside
their own wheels. Measured on 2026-10-26 against the `win_amd64`, CPython 3.14
wheels this build pins, counting PE files that carry an Authenticode
certificate table:

| wheel                       | signed / total PE files |
|-----------------------------|-------------------------|
| `pillow` 12.3.0             | 0 / 8                   |
| `pyqt6-sip` 13.11.1         | 0 / 1                   |
| `sounddevice` 0.5.6         | 0 / 6 (PortAudio DLLs)  |
| `numpy` 2.3.5               | 1 / 21                  |
| `shiboken6` 6.12.0          | 12 / 12                 |
| `pyside6-essentials` 6.12.0 | 242 / 242               |

Two things follow:

- **Signing `dist/*.exe` alone cannot fix this.** The blocked files are inside
  the one-file bundle and are extracted to `%TEMP%\_MEI*` at startup. They must
  be signed *before* PyInstaller collects them.
- **UPX must stay off for a signed build.** UPX rewrites each file, discarding
  any signature and changing the hash that reputation is keyed on. `Fleasion.spec`
  therefore disables UPX when `FLEASION_SIGN_BUNDLE=1`.

Note that `PyQt6/*.pyd` is already excluded from UPX and is shipped byte-for-byte
from its wheel, yet #82 still reports it blocked. Unmodified upstream bytes are
not enough on their own: the missing signature is what the policy acts on.

## What the build does

`.github/workflows/build.yml` has two Windows steps, both no-ops unless the
`WINDOWS_SIGN_PFX_BASE64` secret is set, so forks and pull requests build
unchanged:

1. **Sign bundled native modules** — signs every unsigned `.pyd`/`.dll` in the
   synced venv before the build, leaving files that already carry a signature
   (the Qt DLLs) untouched, then exports `FLEASION_SIGN_BUNDLE=1`.
2. **Sign Windows executable** — signs and verifies `dist/Fleasion-v*-Windows.exe`.

Configure with:

| name                           | kind   | value                                   |
|--------------------------------|--------|-----------------------------------------|
| `WINDOWS_SIGN_PFX_BASE64`      | secret | base64 of the signing certificate `.pfx` |
| `WINDOWS_SIGN_PFX_PASSWORD`    | secret | its password                            |
| `WINDOWS_SIGN_TIMESTAMP_URL`   | var    | optional; defaults to DigiCert's        |

A certificate that merely exists is not sufficient for Smart App Control: it has
to chain to a Microsoft-trusted root, and new publishers accumulate reputation
over time. [Azure Trusted Signing](https://learn.microsoft.com/azure/trusted-signing/)
is the cheapest route that satisfies this; a plain self-signed certificate does
not help at all.

## Reducing the unsigned surface

`pyside6-essentials` and `shiboken6` are fully signed by The Qt Company, while
`pyqt6-sip` is not. Moving the UI from PyQt6 to PySide6 — which the
`feat/rewrite-ui-v2` line already does — removes the #82 blocker without any
certificate. Pillow, numpy and PortAudio stay unsigned either way, so signing is
still required for #84.

`libportaudio64bit.dll` being blocked no longer stops startup: since
`fix(audio): launch on Windows ARM64 and survive PortAudio load failures`
(in 2.4.7) a PortAudio that cannot load degrades to no audio instead of failing
the launch.

## Re-measuring after a dependency bump

```sh
pip download --no-deps --only-binary=:all: \
  --platform win_amd64 --python-version 3.14 -d wheels pillow pyqt6-sip sounddevice
```

Then, for each PE file in those wheels, read the optional header's Certificate
Table (data directory entry 4); a zero size means unsigned. On Windows the same
check is `Get-AuthenticodeSignature <file>` reporting `NotSigned`.

## What users can do today

- Run from source with Python instead of the packaged executable; interpreters
  installed from python.org are signed, and the policy then evaluates the
  interpreter rather than a bundled copy.
- Turning Smart App Control off is a one-way change: re-enabling it requires
  reinstalling Windows. Say so before suggesting it.
