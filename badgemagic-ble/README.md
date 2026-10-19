# badgemagic-ble

Asyncio Bluetooth LE client and command-line tool for 11x44 LED name badges
(WCH CH582M) running the FOSSASIA open firmware
([badgemagic-firmware](https://github.com/fossasia/badgemagic-firmware)).
Badges still on the OEM firmware (`LSLED`) work for uploads.

- `badgemagic_ble.protocol.legacy`: the "wang" upload frame (service `0xFEE0`,
  characteristic `0xFEE1`): up to 8 messages, 9 animation modes, speed, flash,
  marquee, and the badge clock.
- `badgemagic_ble.protocol.ng`: the next-gen control commands (service `0xF055`):
  streaming, brightness, always-on Bluetooth, name, splash screen, power, config.
- `badgemagic_ble.render`: text in three bundled public-domain bitmap fonts
  (X.Org misc-misc `6x10`, `5x8`, `4x6`; see `src/badgemagic_ble/fonts/README`),
  images through Pillow, and XBM.
- `badgemagic_ble.client.BadgeClient`: the BLE client, built on `bleak` and
  `bleak-retry-connector`.

This is the protocol layer for the Home Assistant integration in the same
repository, which carries a copy of it (see "The bundled library" in the top-level
README). It needs Python 3.13+ and is licensed Apache-2.0. It is not published on
PyPI.

## Install

The command-line tool, straight from GitHub (needs `git`):

```bash
uv tool install "git+https://github.com/romkey/hass-badgemagic#subdirectory=badgemagic-ble"
```

Or with pip, into a virtual environment:

```bash
pip install "git+https://github.com/romkey/hass-badgemagic#subdirectory=badgemagic-ble"
```

For development, from this checkout:

```bash
python3.13 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

Then `badgemagic --help` lists the commands.

After changing the library, run `python3 scripts/sync_library.py` from the
repository root to refresh the integration's copy.

## Getting a badge on the air: BT-PAIRING mode

The open firmware only advertises over Bluetooth while it is in **BT-PAIRING**
mode, unless always-on Bluetooth has been enabled.

1. On the badge's MENU (ANIMATION, BT-PAIRING, CLOCK, GAMES, SECURITY, OFF),
   move to **BT-PAIRING** and select it. Which buttons do this depends on
   whether the badge has 2 or 4 keys; the firmware README lists both
   layouts. The badge shows a Bluetooth animation and advertises as
   `LED Badge Magic`, or as the name you gave it.
2. If the badge's **SECURITY** option is on, it shows a 4-digit PIN when it
   enters BT-PAIRING. Pass it with `--pin`. The PIN changes each time you enter
   pairing mode.
3. After a legacy upload the badge shows "BLUETOOTH OFF", stops advertising
   and returns to the menu. Pick **ANIMATION** on the badge to see the new
   message, and re-enter BT-PAIRING before the next command.

To make a badge reachable without pressing buttons (needed for Home
Assistant), turn always-on Bluetooth on and save it, while the badge is in
BT-PAIRING mode:

```bash
badgemagic set-always-on on --save
```

## From a Mac

macOS asks the first time a program uses Bluetooth. Allow it for your
terminal app in System Settings > Privacy & Security > Bluetooth. If the
permission is missing, macOS kills the process with SIGABRT (exit code 134,
"Abort trap: 6") instead of showing an error.

On macOS, CoreBluetooth hides MAC addresses, so badge addresses are UUIDs
specific to your Mac.

### Find the badge

```bash
badgemagic scan
```

```
6A1F0C3E-...-9B2D   -58 dBm  LED Badge Magic       fee0
```

`--all` lists every BLE device. With exactly one badge advertising, other
commands find it themselves. Otherwise pick one with `--address` (or `--name`).

### Send text

```bash
badgemagic send --text HELLO
```

This renders `HELLO` in the `normal` font, uploads it as message 1 scrolling
left at speed 4, and sets the badge clock to the Mac's local time. Options:

```bash
badgemagic send --text HELLO --mode fixed --speed 8 --flash --marquee
badgemagic send --text "Line one" --text "Line two" --font compact   # 2 messages
badgemagic send --image heart.png --mode picture
badgemagic send --text HELLO --pin 6512        # badge with SECURITY on
badgemagic send --text HELLO --dry-run         # print the packets, no Bluetooth
```

#### Keeping Bluetooth on after an upload

The open firmware switches Bluetooth advertising off 1.5 seconds after an upload
completes. It also switches advertising back on whenever a client disconnects.
Staying connected past the 1.5 seconds therefore leaves the badge advertising
after the upload, so the next command works without re-entering BT-PAIRING:

```bash
badgemagic send --text HELLO --linger 2.5
```

This follows from the firmware source and has not been verified on a badge yet.
After the upload, run `badgemagic scan`: if the badge is listed, it worked.
In code, pass `linger=KEEP_ADVERTISING_LINGER` (from `badgemagic_ble.client`)
to `BadgeClient.upload`.

Modes: `scroll_left`, `scroll_right`, `scroll_up`, `scroll_down`, `fixed`,
`animation`, `snowflake`, `picture`, `laser` (or `0`..`8`). Speed is 1..8.
Fonts: `normal` (6x10, 7 characters across), `compact` (5x8, 8 characters),
`tiny` (4x6, 11 characters). Characters a font lacks render as `?`.

### Preview without a badge

```bash
badgemagic preview --text HELLO
badgemagic preview --text "21°C" --font tiny
badgemagic preview --image heart.png
```

prints the 11-row bitmap as ASCII art.

### Next-gen commands (open firmware only)

```bash
badgemagic info                      # model, firmware revision, battery, next-gen support
badgemagic battery
badgemagic set-brightness 2 --save   # 0..3, applies immediately
badgemagic set-name "Desk badge" --save
badgemagic set-always-on on --save
badgemagic set-reset-after-upload off --save
badgemagic set-splash logo.xbm --speed 30 --save
badgemagic stream-text "Streaming works" --fps 10
badgemagic stream-text "12:34" --fixed --duration 30
badgemagic save-config | load-defaults | reboot | power-off
```

Settings made without `--save` (or a later `save-config`) are lost at reboot.
`stream-text` takes the display over without any button presses. On
exit it leaves streaming mode, unless you pass `--keep`, and prints the frame
rate it achieved. `--no-wait` skips waiting for each frame's result, which
raises the rate.

## Library use

```python
import asyncio
from bleak import BleakScanner
from badgemagic_ble import BadgeClient, Message, Mode, render_text

async def main():
    device = await BleakScanner.find_device_by_name("LED Badge Magic")
    async with BadgeClient(device) as badge:
        await badge.upload([Message(render_text("HELLO"), Mode.SCROLL_LEFT, speed=5)])

asyncio.run(main())
```

Errors derive from `badgemagic_ble.BadgeError`. Important ones:
`PinRequired` and `PinRejected` (SECURITY), `NextGenUnsupported` (OEM
firmware), `UnknownCommandError` (older open firmware), `CommandTimeout`, and
typed `CommandError` subclasses for non-zero result codes.

## Protocol notes

The encoders follow the firmware source at commit `b2137f0`. Where the
firmware's `BadgeBLE.md` disagrees with the code, the code wins:

- Timestamp byte 0 is `year % 256`. The firmware decodes it as
  `2000 + (b - 208) mod 256`. The older `led-name-badge-ls32` tool sends
  `year % 100`, which the open firmware reads as the wrong year.
- `power_setting 01 01` turns reset-after-upload **on** (the doc says `01 00`).
  `power_setting 02` reboots.
- `misc` returns `0xFE` for an out-of-range brightness (the doc says `0x02`).
- `flash_splash_screen` keeps the bitmap length in a uint8, so the XBM bits must
  be at most 255 bytes (48x42, or 40x44), not the 48x44 the doc allows.
- An unknown next-gen command fails the write with ATT error `0x18` and gets no
  result notification.
- Any rejected legacy packet fails with ATT error `0x0E`. On the first packet
  this means a PIN is needed or was wrong.

## Tests

```bash
.venv/bin/pytest
```

The tests need no hardware. They check frames against hand-built byte
vectors, every next-gen encoder and result code, both bitmap encodings,
glyphs against the BDF sources, and the client against a fake `BleakClient`.
