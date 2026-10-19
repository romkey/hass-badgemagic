# Badge Magic LED badges for Home Assistant

A Home Assistant custom integration for 11x44 LED name badges running the
[FOSSASIA Badge Magic firmware](https://github.com/fossasia/badgemagic-firmware).
Badges on the OEM `LSLED` firmware work for uploads too. The integration talks to
the badge over Bluetooth Low Energy using the [`badgemagic-ble`](badgemagic-ble/)
library, which the integration carries inside it. Home Assistant installs nothing
extra for it: no PyPI package and no download at startup.

> **Status: untested on a real Home Assistant instance.** The integration passes
> its automated tests with the badge client mocked. Home Assistant's Bluetooth
> stack does not run on macOS, where it was developed, so it has not yet talked to
> a badge from Home Assistant. The library has been checked against a real badge
> from a Mac: scanning, uploading text, reading device info and battery, and
> setting brightness.

## What you get

For each badge:

| Entity | What it does |
|---|---|
| `notify.<badge>_message` | `notify.send_message` uploads the text as the badge's message, using the current mode, speed, flash and marquee. |
| `text.<badge>_message` | The last message Home Assistant sent. Setting it uploads the new text (up to 255 characters). |
| `select.<badge>_mode` | Scroll left/right/up/down, fixed, animation, snowflake, picture, laser. Changing it re-uploads the current message. |
| `number.<badge>_speed` | 1 (slowest) to 8. Re-uploads the current message. |
| `switch.<badge>_flash`, `switch.<badge>_marquee` | Blinking and an animated border. Re-upload the current message. |
| `number.<badge>_brightness` | 25, 50, 75 or 100 %. Open firmware applies it at once and saves it. OEM firmware gets it with the next upload, so the current message is re-uploaded. |
| `image.<badge>_display_preview` | A picture of what Home Assistant last put on the display: message 1 of the last upload, or the current streamed frame. |
| `sensor.<badge>_battery` | Battery level, read whenever Home Assistant connects and hourly while the badge is advertising. |
| `sensor.<badge>_firmware_version` | Diagnostic, read during setup. |
| `binary_sensor.<badge>_bluetooth_reachable` | Diagnostic: on while the badge is advertising, so Home Assistant can connect to it. |

Badges on the open firmware also get:

| Entity | What it does |
|---|---|
| `switch.<badge>_streaming` | Takes over the display: Home Assistant holds a connection open and sends frames (see [Streaming](#streaming)). Turning it off gives the display back to the badge. |
| `switch.<badge>_always_on_bluetooth` | Makes the badge advertise in every mode, not only in BT-PAIRING. Saved on the badge; takes effect the next time it starts (press **Restart**). |
| `text.<badge>_bluetooth_name` | The badge's Bluetooth name, up to 20 bytes. Saved on the badge; it advertises the new name after it restarts. Home Assistant renames the device right away. |
| `button.<badge>_restart`, `button.<badge>_power_off` | Restart or switch off the badge. |
| `switch.<badge>_restart_after_upload` | Disabled by default. The badge restarts after each upload instead of showing "BLUETOOTH OFF". |
| `button.<badge>_save_settings`, `button.<badge>_restore_default_settings` | Disabled by default. Every setting above is already saved when you change it; "restore defaults" puts back the firmware's name, brightness, splash screen and Bluetooth settings. |
| `binary_sensor.<badge>_streaming_active` | Diagnostic: on while streaming. |

The badge cannot report what it is showing or how it is configured, so these
entities show what Home Assistant last sent or set. Values Home Assistant never
set, such as brightness on a new badge, show as unknown. Everything is remembered
across restarts. If a change fails, the entity keeps its previous value.

## Actions

### `badgemagic.send_messages`

Uploads up to 8 messages in one go, replacing everything stored on the badge. Each
message has `text` or `image` (a file Home Assistant may read, or a local
`media-source://` URI; it is scaled to 11 pixels high), and optionally `mode`,
`speed`, `flash`, `marquee` and `font` (`normal`, `compact`, `tiny`). Fields you
leave out use the badge's options.

```yaml
action: badgemagic.send_messages
target:
  device_id: 0123456789abcdef0123456789abcdef
data:
  messages:
    - text: "Doorbell!"
      mode: fixed
      flash: true
    - text: "Someone is at the front door"
      speed: 6
    - image: /config/www/badge/heart.png
      mode: picture
  set_clock: true
```

Image paths must be inside a folder listed in
[`allowlist_external_dirs`](https://www.home-assistant.io/integrations/homeassistant/#allowlist_external_dirs).
If the badge has its SECURITY setting on, pass the 4-digit `pin` it shows in
BT-PAIRING mode (it changes every time, so this only suits manual sends).

These messages are not reflected in the Message, Mode, Speed, Flash and Marquee
entities. Changing one of those afterwards uploads that single message and
replaces the multi-message upload.

### `badgemagic.stream_frame` (open firmware)

Shows one 44x11 frame right away, from `text` (with an optional `font`) or an
`image`, placed `left`, `center` (default) or `right`. It turns streaming on if
needed, and the frame stays until the next frame or until streaming is turned off.

```yaml
action: badgemagic.stream_frame
target:
  device_id: 0123456789abcdef0123456789abcdef
data:
  text: "{{ states('sensor.outdoor_temperature') | round(0) }}°"
```

### `badgemagic.stream_text` (open firmware)

Shows text with Home Assistant doing the animation. `effect` is `scroll`, `fixed`,
or `auto` (scroll only if the text is wider than the display). `fps` (1 to 20,
default 10) sets the scroll speed, one column per frame. Without `duration`,
scrolling text passes once and fixed text stays. With `duration` (seconds), the
text keeps scrolling or stays up that long. `stop_after: true` turns streaming off
at the end so the badge's own messages come back. The action returns as soon as
the text starts. A new stream action replaces one that is still running.

```yaml
action: badgemagic.stream_text
target:
  device_id: 0123456789abcdef0123456789abcdef
data:
  text: "Dinner is ready"
  effect: scroll
  fps: 15
  duration: 60
  stop_after: true
```

### `badgemagic.set_splash_screen` (open firmware)

Replaces the image the badge scrolls through when it starts. `image` is an image
file (scaled down to fit 48x44 pixels) or an `.xbm` file. The bitmap must fit in
255 bytes, for example 48x42 or 40x44 pixels. `frame_height` (default 11) is how
far it moves per step and `speed_ms` the delay between steps. It takes effect the
next time the badge starts.

## Options

Per badge (**Settings > Devices & services > Badge Magic > Configure**):

- **Font**, and the default **mode**, **speed**, **flash** and **marquee** for
  messages that do not set them.
- **Wide text in page modes.** In fixed, snowflake, picture and laser modes the
  badge shows text wider than 44 pixels one 44-pixel page at a time. You can keep
  that, scroll such text instead, or show only the first page. Animation mode is
  left alone, since pages are its frames.
- **Set the badge clock on upload** (on by default). The open firmware loads each
  upload's timestamp into its clock.
- **Keep Bluetooth on after uploads** (open firmware, on by default). See below.

## Reaching the badge

Home Assistant can only connect to a badge while it advertises over Bluetooth. On
the open firmware that happens:

- while the badge is in **BT-PAIRING** mode (from the badge's menu), and from then
  on until the next upload, even after leaving pairing mode; or
- from start-up, if **always-on Bluetooth** was saved on the badge.

The open firmware also **switches Bluetooth off 1.5 seconds after every upload**,
whatever the always-on setting, and returns to its menu. Someone then has to choose
ANIMATION on the badge to show the new messages. This is a firmware limitation
(see `PLAN.md`, sections 1 and 6).

The firmware also switches advertising back on whenever a client disconnects. With
**Keep Bluetooth on after uploads**, Home Assistant stays connected for 2.5 seconds
after each upload, past the 1.5-second switch-off, so the disconnect turns
advertising back on. This follows from the firmware source and has **not been
verified on a badge yet**. You can check it with the library's command-line tool:

```bash
badgemagic send --text HELLO --linger 2.5
```

```bash
badgemagic scan
```

If the second command still lists the badge, it works.

If a badge stops advertising within ten minutes of an upload, Home Assistant
raises a repair issue explaining how to get it back. The issue clears itself when
the badge advertises again. Every action on an unreachable badge fails with "not
advertising over Bluetooth. Put the badge in BT-PAIRING mode, or enable always-on
Bluetooth on it". The **Bluetooth reachable** sensor shows whether Home Assistant
currently sees the badge.

Only one Bluetooth client can be connected to a badge at a time. If the Badge Magic
phone app is connected, Home Assistant cannot connect, and the other way round.
Outside streaming, Home Assistant connects only for the few seconds an action takes.

## Streaming

Streaming is the one way to change what the badge shows with no button presses.
While the **Streaming** switch is on, Home Assistant keeps a connection open, the
badge's own animation stops, and the display shows whatever frames Home Assistant
sends. Other actions keep working over the same connection. An upload turns
streaming off first. If the connection drops, Home Assistant reconnects up to three
times, five seconds apart, and shows the last frame again; after that it turns the
switch off.

When streaming ends, the badge goes back to its stored messages if always-on
Bluetooth is active, and to its Bluetooth screen otherwise. The achievable frame
rate depends on the Bluetooth adapter or proxy; 10 frames per second is a safe
start.

The display preview follows a stream at most once a second.

## Installation

### Requirements

- Home Assistant 2025.2 or newer with working Bluetooth: a local adapter on Home
  Assistant OS or Linux with BlueZ, or an
  [ESPHome Bluetooth proxy](https://esphome.io/components/bluetooth_proxy.html)
  in active (connectable) mode near the badge.
- Nothing else. The integration bundles its `badgemagic_ble` library, and that
  library's own dependencies (bleak, bleak-retry-connector and Pillow) already
  ship with Home Assistant, so the manifest lists no requirements.
- This repository, `https://github.com/romkey/hass-badgemagic`, added to HACS as a
  custom repository.

### With HACS

1. In HACS, open the menu (three dots) and choose **Custom repositories**.
2. Add `https://github.com/romkey/hass-badgemagic` with type **Integration**.
3. Search HACS for **Badge Magic LED Badge**, download it, and restart Home Assistant.
4. Put the badge in BT-PAIRING mode. Home Assistant discovers it (it matches the
   badge's `0xFEE0` service, so renamed badges are found too). Accept the discovery
   under **Settings > Devices & services**, or add the integration manually and pick
   the badge from the list. Setup connects once to read the model and firmware, so
   keep the badge in pairing mode until it finishes.
5. For an open-firmware badge, turn on **Always-on Bluetooth** and press
   **Restart** while it is still reachable.

### Manually

Copy `custom_components/badgemagic` into your Home Assistant configuration folder's
`custom_components/` folder, restart, and continue with step 4 above.

## Example automations

Doorbell to the badge:

```yaml
automation:
  - alias: Doorbell to badge
    triggers:
      - trigger: state
        entity_id: binary_sensor.doorbell
        to: "on"
    actions:
      - action: notify.send_message
        target:
          entity_id: notify.led_badge_magic_message
        data:
          message: "Door!"
```

A clock on the badge during the day, streamed once a minute:

```yaml
automation:
  - alias: Badge clock
    triggers:
      - trigger: time_pattern
        minutes: "*"
    conditions:
      - condition: time
        after: "08:00:00"
        before: "22:00:00"
    actions:
      - action: badgemagic.stream_frame
        target:
          device_id: 0123456789abcdef0123456789abcdef
        data:
          text: "{{ now().strftime('%H:%M') }}"
  - alias: Badge clock off at night
    triggers:
      - trigger: time
        at: "22:00:00"
    actions:
      - action: switch.turn_off
        target:
          entity_id: switch.led_badge_magic_streaming
```

The next calendar event, as a stored message:

```yaml
automation:
  - alias: Next meeting on badge
    triggers:
      - trigger: calendar
        event: start
        entity_id: calendar.work
        offset: "-0:05:00"
    actions:
      - action: badgemagic.send_messages
        target:
          device_id: 0123456789abcdef0123456789abcdef
        data:
          messages:
            - text: "{{ trigger.calendar_event.summary }}"
              speed: 5
```

## Development

The integration tests use
[`pytest-homeassistant-custom-component`](https://github.com/MatthewFlamm/pytest-homeassistant-custom-component)
with `BadgeClient` mocked and advertisements injected into Home Assistant's
Bluetooth manager. They need Python 3.14 (for the current Home Assistant release):

```bash
uv venv --python 3.14 .venv-ha
```

```bash
uv pip install --python .venv-ha/bin/python -r requirements_test.txt
```

```bash
.venv-ha/bin/python -m pytest
```

`aiousbwatcher`, `serialx` and `dbus-fast` in `requirements_test.txt` are
requirements of Home Assistant's `usb` and `bluetooth` integrations that the test
package does not pull in.

The library has its own tests and virtual environment in `badgemagic-ble/`.

### The bundled library

`badgemagic-ble/src/badgemagic_ble/` is the library's source.
`custom_components/badgemagic/badgemagic_ble/` is a copy that the integration
imports, so Home Assistant needs no requirements. Never edit the copy. After
changing the library, refresh it:

```bash
python3 scripts/sync_library.py
```

The `Tests` workflow runs `scripts/sync_library.py --check` and fails if the copy
is out of date. So does the release workflow.

[RELEASING.md](RELEASING.md) describes releasing the integration.
