# Badge Magic Home Assistant integration: plan

Goal: a Home Assistant (HA) integration that talks over Bluetooth Low Energy (BLE) to
LED badges running the FOSSASIA open firmware
(https://github.com/fossasia/badgemagic-firmware), and exposes everything the badge can
do: uploading text and images with the badge's animation modes, live-streaming frames
to the display, and the next-gen configuration commands (brightness, power, BLE name,
always-on BLE, splash screen, saving config). Badges still on the OEM firmware
(`LSLED`) should work for the upload part.

This is a plan only. No code has been written yet.

Protocol facts below were read from the firmware source at commit `b2137f0`
(2026-08-28, "BT advertising switches off after badge receives data (#188)") and from
`BadgeBLE.md` and `src/BLEsecurity.md` in that repo. Where the docs and the code
disagree, the code wins and the disagreement is called out.

---

## 1. What the badge exposes over BLE (verified from firmware source)

### Hardware
- WCH CH582M, 11 rows x 44 columns, single BLE connection at a time
  (`PERIPHERAL_MAX_CONNECTION 1`; a second central is dropped).
- Advertising data: flags + 16-bit service UUID `0xFEE0`. Scan response carries the
  local name, default `LED Badge Magic` (OEM: `LSLED`), renameable, max 20 chars.
- Default ATT MTU is 23; the firmware buffer allows ~64. Writes longer than MTU-3 go
  through BlueZ as long writes (prepare/execute), which the firmware stack supports.

### GATT services

| Service | Char | Props | Purpose |
|---|---|---|---|
| `0xFEE0` legacy | `0xFEE1` | Write (with response) | 16-byte packets of the "wang" bitmap upload frame, plus the optional 4-digit PIN |
| `0xF055` next-gen | `0xF057` | Write | command byte + params, 0..512 bytes |
| `0xF055` next-gen | `0xF056` | Notify, Read | 1-byte result code notified after every command (read is a stub) |
| `0x180F` Battery | `0x2A19` | Read | battery percent, computed on read |
| `0x180A` Device Info | `0x2A24` model, `0x2A26` firmware rev, `0x2A27` hw rev, `0x2A29` manufacturer | Read | `BM1144` / `BM1144-C`, git describe string, `REV3`, `FOSSASIA` |

OEM firmware has only `0xFEE0/0xFEE1` (Read/Write/Notify). The next-gen service is
how we tell open firmware from OEM at runtime.

### Legacy upload frame ("wang")
64-byte header, then bitmap data, sent as consecutive 16-byte writes to `0xFEE1`.

| Offset | Size | Content |
|---|---|---|
| 0x00 | 5 | `wang\0` (firmware compares 5 bytes, so byte 4 must be 0) |
| 0x05 | 1 | OEM brightness byte (0x00=100%, 0x10=75%, 0x20=50%, 0x40=25%); open firmware ignores it |
| 0x06 | 1 | flash bitfield, bit i = message i blinks |
| 0x07 | 1 | marquee bitfield, bit i = message i has animated border |
| 0x08 | 8 | per-message mode byte: high nibble speed 0..7, low nibble mode 0..8 |
| 0x10 | 16 | per-message size as big-endian uint16: number of 8-pixel-wide column chunks |
| 0x20 | 6 | zero |
| 0x26 | 6 | timestamp: year % 256, month, day, hour, minute, second |
| 0x2C | 4 | zero |
| 0x30 | 16 | zero |
| 0x40 | 11 * N | bitmap data: for each chunk, 11 bytes top row to bottom row, MSB = leftmost pixel. N = sum of sizes |
| end | pad | zero-pad to a 16-byte boundary |

Notes that matter for implementation:
- The firmware sets its real-time clock from the timestamp:
  `year = 2000 + ((ts[0] - 208) mod 256)`, so `ts[0]` must be `year % 256` (2026 -> 234).
  The older `led-name-badge-ls32` tool writes `year % 100`, which the open firmware
  decodes as the wrong year. We can use this to sync the badge clock on every upload.
- Modes: 0 scroll left, 1 scroll right, 2 scroll up, 3 scroll down, 4 fixed,
  5 animation (shows successive 44-column frames of the bitmap), 6 snowflake,
  7 picture, 8 laser. Frame width in the open firmware is 44 (OEM tools assume 48).
- Up to 8 messages per upload. The whole frame is written to on-chip EEPROM starting at
  offset 0; the game high score lives at 0x7FF8, so keep uploads under 8 KiB
  (the OEM app limit) to stay safe.
- Transfer completes when `(packets+1)*16 >= 64 + 11*N`. A `wang` header mid-stream
  restarts the transfer. A packet that is not 16 bytes is rejected.
- Each 16-byte write is write-with-response, so throughput is roughly one packet per
  connection interval (25 to 125 ms). A single 44-column text message is 9 packets.
  A full 8-message upload can take several seconds; through an ESPHome proxy, longer.

### Optional PIN security (legacy profile only)
If the owner enables SECURITY in the badge menu, entering BT-PAIRING shows a random
4-digit PIN. The client must write the PIN as ASCII in one 16-byte packet
(`"6512" + 12 zero bytes`) before the first `wang` packet. Wrong PIN or `wang` before
PIN returns ATT error 0x0E. The PIN changes every time pairing mode is entered and auth
resets on disconnect. Unattended automation therefore needs security left off; we
should still support a PIN in the config flow and in actions so a manual send works.

### Next-gen commands (`0xF057`, result notified on `0xF056`)

| Cmd | Name | Params | Notes |
|---|---|---|---|
| 0x00 | next_packet | | stub, "to be implemented" |
| 0x01 | power_setting | `00` power off; `01 xx` reset-after-upload flag; `02` reboot | flags need save_cfg to persist |
| 0x02 | streaming_setting | `00` enter, `01` leave | enter stops animations and clears screen; leave resumes stored animation if always-on BLE, else the BLE waiting animation |
| 0x03 | stream_bitmap | up to 44 little-endian uint16 columns, LSB = top pixel | copied straight into the framebuffer; fails if not streaming |
| 0x04 | ble_setting | `00 xx` always-on BLE; `01 <name<=20>` device name | needs save_cfg |
| 0x05 | flash_splash_screen | width, height, frame height, XBM bits | max 48x44; needs save_cfg |
| 0x06 | save_cfg | | writes config to flash |
| 0x07 | load_fallback_cfg | | firmware defaults, in RAM until save_cfg |
| 0x08 | misc | `00 <u16 ms>` splash speed; `01 <0..3>` LED brightness | brightness applies immediately; save_cfg to persist |

Result byte: 0x00 success, 0xFF bad parameter/out of range, other codes per command.
There is no way to read configuration back (the firmware has a TODO for it), so the
integration must remember what it last set.

### Firmware behavior that constrains the design (verify on your badges)
These are the current `master` behaviors and the ones most likely to differ on the
firmware version actually flashed on your badges:

1. Advertising is only on in BT-PAIRING menu mode, or always if `ble_always_on` is set.
   For HA to reach a badge unattended, always-on BLE must be enabled on it. That is a
   next-gen command, so the first contact with a fresh badge requires someone to put it
   in BT-PAIRING mode once; the integration can then turn always-on on and save config.
2. After a legacy upload completes the badge shows "BLUETOOTH OFF" for 1.5 s, disables
   advertising and returns to the main MENU. It does not start showing the new message.
   With `reset_rx` set it reboots instead, and boots into the MENU too. Either way a
   human has to pick ANIMATION on the badge. This is the biggest gap for a home
   automation use case and is a firmware limitation, not something the integration can
   work around with the legacy protocol alone.
3. Disconnecting re-enables advertising unconditionally (`link_onTerminated`), while
   the 1.5 s post-upload timer disables it. Whether advertising survives an upload
   therefore depends on disconnect timing. Do not rely on it.
4. Streaming mode works from any badge mode, including MENU, and leaving it with
   always-on BLE set resumes the stored animation. Streaming is the one path where HA
   can fully control what is on the display with no button presses.

Recommendation: plan a small upstream firmware contribution alongside the integration
(section 6). Until then, the integration ships with these limitations documented.

---

## 2. Architecture

Two deliverables, following HA's rule that protocol code lives in a standalone library:

### 2a. `badgemagic-ble` Python library (PyPI, pure asyncio + bleak)
- `protocol/legacy.py`: dataclasses `Message(bitmap, mode, speed, flash, marquee)` and
  `encode_frame(messages, timestamp, brightness=None) -> bytes`, plus `chunk16()`.
  No I/O. Exhaustively unit-tested against hand-built byte vectors.
- `protocol/ng.py`: encoders for every next-gen command and a result-code decoder.
- `render.py`: text and image to 11-row bitmaps. Bitmap type is a simple
  `width x 11` boolean grid with helpers to convert to column chunks (legacy) and to
  uint16 columns (streaming). Text is rendered from bundled public-domain BDF bitmap
  fonts (section 4) by a small pure-Python BDF parser, so no font conversion happens at
  runtime. Pillow is used only for image input: any Pillow-readable file, thresholded,
  auto-scaled to 11 px high. XBM parsing for splash screens.
- `client.py`: `BadgeClient` wrapping a `BleakClient` obtained from
  `bleak_retry_connector.establish_connection`. Methods: `upload(messages, pin=None,
  set_clock=True)`, `start_streaming()`, `stream_frame(bitmap)`, `stop_streaming()`,
  `set_brightness`, `set_always_on`, `set_name`, `power_off`, `reboot`,
  `set_reset_after_upload`, `set_splash(xbm)`, `set_splash_speed`, `save_config`,
  `load_defaults`, `read_battery()`, `read_device_info()`, `has_nextgen` (service
  present). Next-gen calls subscribe to `0xF056` and await the result byte with a
  timeout; non-zero results raise typed exceptions. Legacy PIN rejection (ATT 0x0E on
  the first `wang` packet) raises `PinRequired`.
- `cli.py`: `badgemagic scan`, `badgemagic send --text ... --mode ...`,
  `badgemagic stream-text`, `badgemagic set-brightness`, etc. This runs from a Mac
  with bleak over CoreBluetooth and is the fastest way to validate the protocol against
  real badges before any HA work.
- Python 3.13+, dependencies: bleak, bleak-retry-connector, Pillow. Apache-2.0 to
  match the firmware.

### 2b. `custom_components/badgemagic` HA integration (HACS-installable)
- `manifest.json`: `bluetooth` matchers on `service_uuid 0000fee0-0000-1000-8000-00805f9b34fb`
  with `connectable: true` (name is user-changeable, so match the service, not the
  name); `dependencies: ["bluetooth_adapters"]`; `requirements: ["badgemagic-ble==x.y"]`;
  `iot_class: local_polling`; `integration_type: device`.
- Config flow: `async_step_bluetooth` on discovery, `async_step_user` listing
  discovered badges by name and address, confirm step. One config entry per badge keyed
  by MAC. On confirm, connect once to read device info (model, firmware, hardware) and
  detect next-gen support; store in entry data. Options flow for per-badge defaults:
  font, default mode, speed, flash, marquee, whether to sync the clock on upload, and
  "enable always-on BLE on first contact".
- Coordinator (`BadgeCoordinator`): owns the last-known desired state (message list,
  mode, speed, flash, marquee, brightness, always-on) since the badge cannot be read
  back; serializes all BLE operations through one lock (one connection at a time, and
  the badge only allows one central); connects on demand and disconnects when idle,
  except while streaming; tracks availability with `bluetooth.async_track_unavailable`
  and `async_register_callback` on the address; refreshes battery on each connection
  and on a slow poll (hourly) when the badge is advertising.
- Device registry: one device per badge with manufacturer FOSSASIA, model from
  `0x2A24`, sw_version from `0x2A26`, hw_version from `0x2A27`, connection
  `(bluetooth, MAC)`.
- Diagnostics: last frame encoded (hex), last result codes, connection stats.
- `strings.json` + `translations/en.json`, `services.yaml`, `icons.json`.

### Entities per badge

| Platform | Entity | Behavior |
|---|---|---|
| notify | `notify.<badge>` | `send_message(message)` renders text with the current mode/speed/flash/marquee and uploads it as message 1. Makes the badge a standard notification target for automations. |
| text | Message | Same as notify but stateful; shows last text sent. Max 255 chars. |
| select | Mode | scroll_left, scroll_right, scroll_up, scroll_down, fixed, animation, snowflake, picture, laser. Changing it re-uploads the current message. |
| number | Speed | 1..8 (mapped to nibble 0..7). Re-uploads. |
| switch | Flash, Marquee | Re-upload. |
| number or select | Brightness | 0..3 via next-gen `misc 01`; also written into the OEM header byte for OEM badges. Next-gen only otherwise. |
| switch | Always-on Bluetooth | next-gen `ble_setting 00` + `save_cfg`. |
| switch | Reset after upload | next-gen `power_setting 01` + `save_cfg`. |
| switch | Streaming mode | enter/leave streaming; while on, keeps the connection and exposes stream actions. |
| button | Power off, Reboot, Save config, Load defaults | next-gen. |
| text | Device name | next-gen `ble_setting 01` + `save_cfg`; renames the badge's BLE name (then the HA device name). |
| sensor | Battery | percent, read on connect and hourly. |
| sensor | Firmware version | diagnostic, from device info. |
| binary_sensor | Streaming active | diagnostic. |
| image | Preview | the last rendered bitmap as a PNG so dashboards show what the badge displays. Nice to have. |

Entities that need the next-gen service are created only when the config entry says
the badge has it, so OEM badges get a clean, smaller device.

### Actions (services), domain `badgemagic`
- `badgemagic.send_messages`: target device(s); `messages`: list of up to 8 items, each
  with `text` or `image` (path or `media-source://`) or `xbm`/raw bitmap, `mode`,
  `speed`, `flash`, `marquee`; `pin` (optional); `set_clock` (default true);
  `brightness` for OEM. This is the full-power action; the entities are conveniences
  built on it.
- `badgemagic.stream_frame`: a 44x11 bitmap (image path, or text rendered as a single
  frame) pushed immediately while streaming is on. Pair with `start_streaming` /
  `stop_streaming` actions or the Streaming switch.
- `badgemagic.stream_text`: text scrolled or shown fixed by the integration itself
  (frames rendered and sent at a configurable rate), with a duration; useful for a
  clock or sensor readout where the badge's own modes are not wanted.
- `badgemagic.set_splash_screen`: image (<= 48x44) + frame height + speed, then save.
- `badgemagic.sync_clock`: uploads the current stored messages with a fresh timestamp
  (the only way to set the clock is via a legacy upload).

### Connection handling details
- Resolve the `BLEDevice` with `bluetooth.async_ble_device_from_address(hass, mac,
  connectable=True)`; if `None`, raise `HomeAssistantError("badge not advertising;
  put it in BT-PAIRING mode or enable always-on Bluetooth")`.
- New `BleakClient` per connection via `establish_connection(BleakClientWithServiceCache,
  device, name, disconnected_callback, max_attempts=3)`; timeout >= 10 s per HA docs.
- Enable `0xF056` notifications once per connection before next-gen commands.
- Legacy upload: write 16-byte packets with response, sequentially, no gaps. On the
  first packet, map ATT 0x0E to `PinRequired`; if a PIN was provided, send it first.
- After a legacy upload on open firmware, disconnect promptly (the badge is about to
  switch BLE off anyway) and expect the badge to become unavailable until someone
  re-enters pairing mode, unless always-on is set. Surface this in the entity
  availability and in a repair issue the first time it happens
  ("Enable always-on Bluetooth so Home Assistant can reach this badge").
- Streaming: hold the connection; cap frame rate (start at 10 fps, measure); each
  frame is one 89-byte write, which exceeds the default MTU and becomes a long write.
  Request a larger MTU where the backend allows it; test through ESPHome proxies.

---

## 3. Phases

### Phase 0: protocol validation on real hardware (library + CLI only)
- Scaffold `badgemagic-ble`, implement `encode_frame`, `render`, and `BadgeClient.upload`.
- From the Mac, scan, connect, upload "HELLO" in each of the 9 modes, with flash and
  marquee, speed 1 and 8. Confirm the clock sets correctly (badge CLOCK mode).
- Verify the post-upload behavior in section 1 ("BLUETOOTH OFF", menu) on the firmware
  version actually on your badges, and record the firmware revision string.
- Implement and test every next-gen command; confirm result codes; test streaming at
  5/10/20 fps and record what the connection sustains.
- Test the PIN flow with SECURITY enabled on one badge.
- Outcome: a working CLI and a written list of confirmed firmware behaviors. This
  phase de-risks everything else and takes the least time.

### Phase 1: HA integration, upload path
- Config flow with Bluetooth discovery, device registry, coordinator, availability.
- Entities: notify, text, mode, speed, flash, marquee, battery, firmware sensor.
- Action `send_messages` with multi-message support and images.
- Works on OEM and open firmware. Document the "badge returns to menu" limitation.
- Install via HACS on a real HA instance (HAOS or Linux with BlueZ, or an ESPHome
  Bluetooth proxy; HA Bluetooth does not work from a macOS dev checkout).

### Phase 2: next-gen configuration
- Brightness, always-on BLE, reset-after-upload, device name, power off, reboot,
  save/load config, splash screen action. Options-flow toggle to enable always-on on
  first contact. Repair issue nudging users to enable always-on.

### Phase 3: streaming
- Streaming switch, `stream_frame`, `stream_text`, preview image entity. Connection
  kept alive while streaming with reconnect on drop. Rate limiting and MTU handling.

### Phase 4: polish and release
- Tests with `pytest-homeassistant-custom-component` (config flow, entity behavior,
  coordinator with a mocked `BadgeClient`); library tests with byte-vector fixtures.
- README with pairing instructions, firmware caveats, example automations
  (doorbell -> badge, calendar event -> badge, Streaming clock).
- HACS metadata (`hacs.json`), GitHub release workflow, PyPI publish for the library.
- Optional later: submit to HA core (needs the library on PyPI, full test coverage,
  and brand assets).

---

## 4. Design decisions to settle early

1. **Font (decided): public-domain X.Org misc-misc bitmap fonts.** These are the
   classic X11 "fixed" fonts maintained by Markus Kuhn, from
   https://gitlab.freedesktop.org/xorg/font/misc-misc. Each BDF file carries the
   copyright property "Public domain font. Share and enjoy.", so there are no
   attribution or license-compatibility constraints for the Apache-2.0 library, HACS,
   or HA core. They are ISO 10646 encoded with broad coverage. Three sizes ship:

   | Font option | BDF file | Cell (w x h) | Glyphs | Characters across 44 px |
   |---|---|---|---|---|
   | `normal` (default) | `6x10.bdf` | 6 x 10 | 1597 | 7 |
   | `compact` | `5x8.bdf` | 5 x 8 | 1426 | 8 |
   | `tiny` | `4x6.bdf` | 4 x 6 | 919 | 11 |

   Coverage was checked for characters HA messages are likely to use. All three have
   Latin-1 accents, the degree sign, the euro sign, the four arrows, a heart, Greek and
   Cyrillic. `normal` and `compact` also have a smiley and a filled circle. None has
   a check mark. Missing glyphs render as `?`.

   Implementation rules:
   - Ship the three `.bdf` files unmodified in `badgemagic_ble/fonts/`, with a
     `fonts/README` naming the source repo and commit (`c4e2af0`, 2023-02-27) and
     quoting the public-domain statement.
   - Parse BDF in pure Python on first use and cache glyphs per font. Advance width
     comes from each glyph's `DWIDTH`; the cells already include 1 px spacing.
   - Place glyphs on a common baseline and center the font's cell vertically in the
     11 rows, rounding toward the top. For `normal` that leaves one blank row.
   - The HA options flow exposes the font as a select of `normal`, `compact`, `tiny`;
     the `send_messages` and `stream_text` actions take a per-message `font` field.
   - No TrueType or user-supplied font path.
   - Width is unconstrained for scrolling modes. For fixed and picture modes, text
     wider than 44 px is either clipped with a warning or auto-switched to scroll,
     chosen by an option.
2. **Where state lives.** The badge cannot be read back, so mode/speed/flash/marquee/
   brightness live in HA (restored across restarts via `RestoreEntity`). The entities
   reflect what HA last sent, not what the badge shows.
3. **Entity-driven vs action-driven.** Keep entities simple (one message) and put the
   8-message power in the action. Changing a mode/speed entity re-uploads the single
   current message; it does not try to merge into a multi-message upload.
4. **Library vendored or published.** HACS integrations install `requirements` from
   PyPI, so publish the library early (even 0.0.x). During Phase 0 it is just a repo
   with a CLI; publish at the start of Phase 1.
5. **Repo layout.** Either one repo with `custom_components/badgemagic/` at the root
   (HACS requirement) and the library in a separate repo, or a monorepo with the
   library under `lib/` and a release workflow that publishes it. Two repos is
   simpler for HACS and PyPI; recommend two repos.

---

## 5. Risks and open questions

- **Firmware version drift.** Your badges may run older firmware with different
  post-upload behavior, no PIN support, or no `misc`/brightness command (that command
  is newer than the rest). Phase 0 pins this down; the integration should degrade
  gracefully when a command returns 0xFF for "unknown command".
- **Unattended display after upload.** On current firmware the badge lands in the menu
  after an upload (section 1). Mitigations: streaming (Phase 3) for fully automated
  displays, and the firmware change in section 6.
- **Long writes through proxies.** `stream_bitmap` (89 bytes) and `flash_splash_screen`
  (up to 267 bytes) exceed the 23-byte default MTU. BlueZ does long writes
  transparently; ESPHome Bluetooth proxies need checking.
- **Connection contention.** The badge accepts one central. If the phone app is
  connected, HA fails to connect; surface a clear error rather than retrying forever.
- **Timestamp year encoding.** Use `year % 256` per firmware; OEM badges ignore the
  field for display, so the same value is safe for both.
- **Brightness semantics differ.** OEM: header byte 5 per upload. Open firmware:
  next-gen `misc 01`, 0..3, persisted with save_cfg. The Brightness entity hides this.
- **Pillow in HA.** Pillow is already a core dependency, so the renderer adds no new
  binary wheel to HA. Keep other dependencies to bleak and bleak-retry-connector.

## 6. Suggested upstream firmware contributions (optional, parallel track)

Small changes that would make the badge a first-class HA device; each is a few lines
in `src/main.c` / `src/ngctrl.c`:
1. When `ble_always_on` is set, finish a legacy upload by entering ANIMATION (NORMAL)
   mode instead of showing "BLUETOOTH OFF" and returning to the menu, and keep
   advertising on.
2. A next-gen command to select the badge mode remotely (animation / clock / menu) and
   to pick the active bitmap bank, so HA can switch what is shown without an upload.
3. A next-gen "read config" command (the firmware has a TODO for it) so HA can show
   true brightness/always-on state instead of remembered state.
4. Implement the `0xF056` read to return the last result or a status struct.

## 7. References
- Firmware repo: https://github.com/fossasia/badgemagic-firmware (BadgeBLE.md,
  src/BLEsecurity.md, src/ngctrl.c, src/legacyctrl.c, src/main.c, src/ble/profile/*.c)
- HA Bluetooth developer docs: https://developers.home-assistant.io/docs/bluetooth/
  (manifest matchers, `async_ble_device_from_address`, `async_track_unavailable`,
  "do not reuse a BleakClient", 10 s connect timeout, bleak-retry-connector)
- Bundled fonts (public domain): https://gitlab.freedesktop.org/xorg/font/misc-misc
- Reference USB tool with the same frame format: https://github.com/fossasia/led-name-badge-ls32
- Companion app (BLE client behavior to compare against): https://github.com/fossasia/badgemagic-app

---

## Phase 0 status (2026-10-18)

- Library and CLI written in `badgemagic-ble/`.
  `pytest` passes (90 tests, no hardware). `badgemagic --help` and `badgemagic preview`
  work. A BLE scan from the agent's sandboxed shell was killed by macOS (no Bluetooth
  permission). Hardware checks are run by the user from their own terminal instead.
- Firmware code vs `BadgeBLE.md`, as implemented: `power_setting 01 01` enables
  reset-after-upload; `misc` returns 0xFE for bad values; an unknown command fails the
  write with ATT 0x18 and sends no notification; the splash bits are limited to 255
  bytes because the length is a uint8.
- Hardware checks done (2026-10-18, user's Mac, one badge advertising as
  `LED Badge Magic`): `badgemagic scan` found the badge, and
  `badgemagic send --text HELLO` uploaded and displayed HELLO on the badge.
- `badgemagic info` on that badge: model `BM1144-C`, firmware `(C) v0.1-68-g00ef45a-dirty`,
  hardware `REV3`, manufacturer `FOSSASIA`, battery 84%, next-gen service present.
  - The `(C)` prefix and `-C` model suffix only mean the build is not first-gen
    (Micro USB); second-gen and third-gen builds both get them. The `REV3` hardware
    string is hardcoded in `devinfo.c` for every build, so it identifies nothing.
    Device info cannot tell second-gen from third-gen hardware.
  - Commit `00ef45a` is not in upstream: not on master, any branch, any pull request,
    or the prebuilt binaries (`origin/bin` holds `v0.1-42-g9ce885d`). Upstream master
    (`b2137f0`) is only 64 commits past v0.1, so this is a local build of about
    master plus 4 local commits, with uncommitted changes (`-dirty`).
  - Behavior matches recent master: the hardware revision string arrived in #167
    (2026-07-21), and the badge stopped advertising after the HELLO upload, which is
    the #188 change (2026-08-28). Section 1's firmware notes therefore apply, with the
    caveat that the local changes are unknown.
  - Open: get the source of the local build to check whether its changes touch BLE,
    the legacy upload path, or `ngctrl.c`.
- Still open from the Phase 0 checklist: all 9 modes, flash, marquee, speeds 1 and 8,
  clock setting, every next-gen command, always-on BLE and whether advertising
  survives an upload with it on, streaming rates, and the PIN flow.

## Phase 1 status (2026-10-18)

- Integration written in `custom_components/badgemagic/`, with `hacs.json` and a
  `README.md` at the top level. **Untested on a real Home Assistant instance**: HA
  Bluetooth does not run on macOS, so nothing has talked to a badge through HA yet.
- Tests: `tests/` (49 tests, `pytest-homeassistant-custom-component` 0.13.373 /
  HA 2026.10.2, Python 3.14, venv `.venv-ha`, library installed editable). They cover
  discovery through the manifest matcher, the user step, already configured, cannot
  connect, the options flow, every entity, the not-advertising error, state restore,
  hourly battery reads, diagnostics and `send_messages` (text, images, PIN errors,
  validation). `BadgeClient` is mocked; advertisements are injected into HA's real
  Bluetooth manager.
- Built as planned: Bluetooth discovery on the `0xFEE0` service, one entry per MAC
  with device info and next-gen support stored at setup, options for font, mode,
  speed, flash, marquee and clock sync, a coordinator with one lock, connect on
  demand, battery read before every upload and hourly while advertising, device
  registry entry, diagnostics (including the last frame in hex), notify / text /
  select / number / switch / sensor entities and `badgemagic.send_messages`.
- Choices made while building:
  - Desired state (message, mode, speed, flash, marquee, last battery level) lives
    in a `Store` per entry rather than `RestoreEntity`, because several entities
    share it. If a re-upload fails, the change is not kept.
  - Control entities stay available while the badge is not advertising, so using
    them raises the "put it in BT-PAIRING mode or enable always-on Bluetooth" error
    instead of being silently skipped. A diagnostic `Bluetooth reachable`
    connectivity sensor (not in the section 2b table) shows advertising state from
    `async_register_callback` / `async_track_unavailable`.
  - Options' mode/speed/flash/marquee are the defaults for `send_messages` items and
    the initial entity values; the font option applies to all text.
  - `send_messages` does not update the single-message entities (section 4.3).
  - Images: file paths must pass `allowlist_external_dirs`; `media-source://` works
    only for local media (resolved to a file path).
- Not done in Phase 1: the repair issue after the first post-upload disconnect
  (section 2b, "connection handling") and the clip-or-scroll option for wide text in
  fixed modes (section 4.1). Text wider than 44 px in fixed mode is sent as is.
- Blockers for a real install: `badgemagic-ble` is not on PyPI, and the manifest pins
  `badgemagic-ble==0.0.1`. The `documentation`/`issue_tracker` URLs in the manifest
  assume a `romkey/hass-badgemagic` repository.
- Library note (not changed): `BadgeClient.read_battery` lets a `BleakError` from
  the GATT read escape unwrapped, unlike the other methods; the integration catches
  it.

## Phases 2 to 4 status (2026-10-18)

Built on top of Phase 1. **Still untested on a real Home Assistant instance**, for
the same reason as Phase 1. Tests: 87 integration tests (`tests/`), 93 library
tests (`badgemagic-ble/tests/`), all with the badge mocked.

### Firmware findings that changed the design

Read from `src/main.c` and `src/ble/peripheral.c` at `b2137f0`:

- Brightness level 0 is the dimmest (LEDs lit 1/4 of each scan cycle) and 3 is full,
  so the four levels are exactly 25/50/75/100 %. The firmware default is level 0.
- `ble_always_on` and the device name are only read at boot (`ble_setup`,
  `gap_init`). Setting them takes effect after a restart. `reset_rx` and brightness
  apply at once.
- After a legacy upload, advertising is switched off 1.5 s later regardless of
  `ble_always_on`, so always-on does not keep a badge reachable across uploads.
- Every disconnect re-enables advertising (`link_onTerminated`). Leaving
  BT-PAIRING mode does not disable it, so after pairing mode the badge advertises
  until the next upload or restart.
- Staying connected for more than 1.5 s after an upload should therefore leave the
  badge advertising. This contradicts the "do not rely on it" note in section 1,
  item 3: it is now an option, unverified on hardware.
- Fixed, snowflake, picture and laser modes show a bitmap wider than 44 columns
  as successive 44-column pages, rather than clipping it.

### Library 0.1.0

- `BadgeClient.upload(..., linger=seconds)` stays connected after the transfer;
  `KEEP_ADVERTISING_LINGER = 2.5`. CLI: `badgemagic send --linger 2.5`.
- `read_battery` wraps `BleakError` in `BadgeConnectionError` (the Phase 1 note).
- Package metadata gained Source and Issues links; the wheel builds with the fonts.

### Phase 2: next-gen configuration

- Brightness `number` (25 to 100 % in steps of 25) for every badge: next-gen sends
  `misc 01` and saves; OEM puts it in upload header byte 5 and re-uploads.
- Switches: Always-on Bluetooth, Restart after upload (disabled by default).
  Text: Bluetooth name (1 to 20 bytes; also renames the HA device and entry).
  Buttons: Restart, Power off, Save settings and Restore default settings (the
  last two disabled by default). Every setting change is followed by `save_cfg`.
- Settings HA has set are stored with the message state and restored; ones never
  set show as unknown. Restore defaults resets them to the firmware defaults.
- `badgemagic.set_splash_screen` action (image or `.xbm`, frame height, frame
  delay), rejecting bitmaps over 255 bytes.
- Option "Keep Bluetooth on after uploads" (next-gen only, default on) passes the
  2.5 s linger to every upload.
- Repair issue `bluetooth_off_after_upload` when a badge stops advertising within
  10 minutes of an upload; deleted when it advertises again or the entry is removed.
- Next-gen entities are created only for badges with the next-gen service.

### Phase 3: streaming

- Streaming switch and a Streaming active diagnostic sensor. While streaming, one
  connection is held and every other action uses it; uploads leave streaming first.
- `badgemagic.stream_frame` (text or image, alignment) and `badgemagic.stream_text`
  (auto/scroll/fixed, 1 to 20 fps, optional duration, optional stop afterwards).
  Both start streaming if needed; a new stream replaces a running one. Frames
  during animations are sent without waiting for each result notification.
- Dropped connections are retried 3 times, 5 s apart, re-sending the last frame;
  then streaming is turned off.
- Display preview `image` entity: message 1 of the last upload or the current
  frame, drawn as round LEDs, updated at most once a second while streaming.
- Streaming stops when the config entry unloads.

### Phase 4: polish and release preparation

- Option "Wide text in page modes": `page` (firmware default), `scroll`, `clip`.
  Applied to text in fixed, snowflake, picture and laser modes, not animation.
- README rewritten for all phases, with corrected reachability facts and example
  automations (doorbell, streamed clock, calendar). The Phase 1 README wrongly said
  always-on keeps the badge reachable between uploads.
- `requirements_test.txt`, `.gitignore`, and workflows: integration `Validate`
  (hassfest, HACS with the brands check skipped), `Tests`, and `Release` (on a
  `v*` tag matching `manifest.json`); library `CI` and `Publish to PyPI` (trusted
  publishing on a `v*` tag). `RELEASING.md` lists the steps.
- Manifest keys sorted for hassfest, `codeowners` set to `@romkey`, version 0.2.0,
  pinning `badgemagic-ble==0.1.0`.

### Not done

- Nothing is published, pushed or committed. The two repositories do not exist
  yet, and the library is not on PyPI, so HACS installs fail until it is.
- hassfest and the HACS action have not been run; they run in CI once the
  repository exists.
- No brands entry in `home-assistant/brands`, and no HA core submission (both
  optional).
- Hardware checks still open: the linger behavior, always-on after a restart,
  streaming frame rates over a real adapter or ESPHome proxy, and everything
  through Home Assistant.

## Repository (2026-10-18)

Published to https://github.com/romkey/hass-badgemagic as **one repository**, at the
user's request, replacing the two-repository recommendation in section 4, item 5.
The library stays in `badgemagic-ble/` and releases from the same repository with
`badgemagic-ble-vX.Y.Z` tags; the integration releases with `vX.Y.Z` tags. Library
CI and PyPI publishing moved to the root `.github/workflows/`, and the integration
tests now install the library from the checkout. See `RELEASING.md`. Both projects
are Apache-2.0. Nothing has been released to PyPI or as a GitHub release yet.
