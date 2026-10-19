"""``badgemagic`` command-line tool."""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import logging
import sys
import time
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path

from bleak import BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

from . import __version__
from .client import BadgeClient
from .const import DEFAULT_NAME, DISPLAY_COLUMNS, LEGACY_SERVICE_UUID, OEM_NAME
from .exceptions import BadgeError, BadgeNotFound
from .protocol.legacy import Message, Mode, OemBrightness, chunk16, encode_frame
from .render import FONT_NAMES, Bitmap, Xbm, load_image, parse_xbm, render_text

OEM_BRIGHTNESS = {
    "100": OemBrightness.FULL,
    "75": OemBrightness.THREE_QUARTERS,
    "50": OemBrightness.HALF,
    "25": OemBrightness.QUARTER,
}


def is_badge(device: BLEDevice, adv: AdvertisementData) -> bool:
    name = adv.local_name or device.name or ""
    return LEGACY_SERVICE_UUID in {u.lower() for u in adv.service_uuids} or name in (DEFAULT_NAME, OEM_NAME)


async def scan(timeout: float, include_all: bool = False) -> list[tuple[BLEDevice, AdvertisementData]]:
    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    results = [(d, a) for d, a in found.values() if include_all or is_badge(d, a)]
    results.sort(key=lambda da: da[1].rssi, reverse=True)
    return results


async def find_badge(args: argparse.Namespace) -> BLEDevice:
    if args.address:
        device = await BleakScanner.find_device_by_address(args.address, timeout=args.timeout)
        if device is None:
            raise BadgeNotFound(f"no badge advertising at {args.address}; put it in BT-PAIRING mode")
        return device
    badges = await scan(args.timeout)
    if args.name:
        badges = [(d, a) for d, a in badges if (a.local_name or d.name) == args.name]
    if not badges:
        raise BadgeNotFound("no badge found; put it in BT-PAIRING mode (or enable always-on Bluetooth)")
    if len(badges) > 1:
        listing = "\n".join(f"  {d.address}  {a.local_name or d.name}" for d, a in badges)
        raise BadgeNotFound(f"several badges found; pick one with --address:\n{listing}")
    return badges[0][0]


async def with_badge(args: argparse.Namespace, action: Callable[[BadgeClient], Awaitable[None]]) -> None:
    device = await find_badge(args)
    logging.getLogger(__name__).info("connecting to %s (%s)", device.name, device.address)
    async with BadgeClient(device) as badge:
        await action(badge)


# argument helpers ------------------------------------------------------------


def parse_mode(value: str) -> Mode:
    if value.isdigit():
        return Mode(int(value))
    try:
        return Mode[value.upper().replace("-", "_")]
    except KeyError:
        names = ", ".join(m.name.lower() for m in Mode)
        raise argparse.ArgumentTypeError(f"unknown mode {value!r}; choose {names} or 0..8") from None


def parse_on_off(value: str) -> bool:
    lowered = value.lower()
    if lowered in ("on", "true", "1", "yes"):
        return True
    if lowered in ("off", "false", "0", "no"):
        return False
    raise argparse.ArgumentTypeError("expected on or off")


def int_range(lo: int, hi: int) -> Callable[[str], int]:
    def parse(value: str) -> int:
        n = int(value)
        if not lo <= n <= hi:
            raise argparse.ArgumentTypeError(f"must be {lo}..{hi}")
        return n

    return parse


class AppendContent(argparse.Action):
    """Collect --text and --image in command-line order."""

    def __call__(self, parser, namespace, values, option_string=None):  # type: ignore[no-untyped-def]
        items = list(getattr(namespace, "content", None) or [])
        items.append((self.dest, values))
        namespace.content = items


def build_bitmap(kind: str, value: str, args: argparse.Namespace) -> Bitmap:
    if kind == "text":
        return render_text(value, args.font)
    path = Path(value)
    if path.suffix.lower() == ".xbm":
        return parse_xbm(path.read_text()).to_bitmap()
    return load_image(path, threshold=args.threshold, invert=args.invert)


def build_messages(args: argparse.Namespace) -> list[Message]:
    content = getattr(args, "content", None) or []
    if not content:
        raise SystemExit("send: give at least one --text or --image")
    if len(content) > 8:
        raise SystemExit("send: at most 8 messages per upload")
    messages = []
    for kind, value in content:
        bitmap = build_bitmap(kind, value, args)
        if bitmap.width == 0:
            raise SystemExit(f"send: {kind} {value!r} renders to nothing")
        messages.append(Message(bitmap, args.mode, args.speed, args.flash, args.marquee))
    return messages


# commands ----------------------------------------------------------------------


async def cmd_scan(args: argparse.Namespace) -> None:
    badges = await scan(args.timeout, args.all)
    if not badges:
        print("No badges found. Put the badge in BT-PAIRING mode and try again.")
        return
    for device, adv in badges:
        services = " ".join(sorted(u[4:8] for u in adv.service_uuids))
        print(f"{device.address}  {adv.rssi:4d} dBm  {adv.local_name or device.name or '?':<20}  {services}")


async def cmd_send(args: argparse.Namespace) -> None:
    messages = build_messages(args)
    brightness = OEM_BRIGHTNESS[args.brightness] if args.brightness else None
    if args.dry_run:
        frame = encode_frame(messages, None if args.no_clock else dt.datetime.now(), brightness)
        for msg in messages:
            print(msg.bitmap.to_ascii(), end="\n\n")
        for packet in chunk16(frame):
            print(packet.hex(" "))
        return

    async def run(badge: BadgeClient) -> None:
        await badge.upload(
            messages, pin=args.pin, set_clock=not args.no_clock, brightness=brightness, linger=args.linger
        )
        print(f"Uploaded {len(messages)} message(s) to {badge.name}.")

    await with_badge(args, run)


async def cmd_stream_text(args: argparse.Namespace) -> None:
    bitmap = render_text(args.text, args.font)
    scroll = args.scroll or (not args.fixed and bitmap.width > DISPLAY_COLUMNS)
    interval = 1.0 / args.fps

    if scroll:
        # Enter from the right edge and leave off the left edge.
        frames = [bitmap.crop(x, DISPLAY_COLUMNS) for x in range(-DISPLAY_COLUMNS, bitmap.width + 1)]
    else:
        frames = [bitmap]

    async def run(badge: BadgeClient) -> None:
        await badge.start_streaming()
        sent = 0
        started = time.monotonic()
        try:
            deadline = started + args.duration if args.duration else None
            while True:
                for frame in frames:
                    tick = time.monotonic()
                    await badge.stream_frame(frame, wait=not args.no_wait)
                    sent += 1
                    if deadline is not None and time.monotonic() >= deadline:
                        return
                    await asyncio.sleep(max(0.0, interval - (time.monotonic() - tick)))
                if deadline is None and not args.loop:
                    return
        finally:
            elapsed = time.monotonic() - started
            if elapsed > 0:
                print(f"Sent {sent} frames in {elapsed:.1f} s ({sent / elapsed:.1f} fps).")
            if not args.keep and badge.is_connected:
                await badge.stop_streaming()

    await with_badge(args, run)


def simple(method: str, *values: str, save_flag: bool = True) -> Callable[[argparse.Namespace], Awaitable[None]]:
    async def command(args: argparse.Namespace) -> None:
        async def run(badge: BadgeClient) -> None:
            await getattr(badge, method)(*(getattr(args, v) for v in values))
            if save_flag and getattr(args, "save", False):
                await badge.save_config()
            print("OK" + (" (saved)" if getattr(args, "save", False) else ""))

        await with_badge(args, run)

    return command


async def cmd_set_splash(args: argparse.Namespace) -> None:
    path = Path(args.image)
    if path.suffix.lower() == ".xbm":
        xbm = parse_xbm(path.read_text())
    else:
        xbm = Xbm.from_image(path, threshold=args.threshold, invert=args.invert)

    async def run(badge: BadgeClient) -> None:
        await badge.set_splash(xbm, args.frame_height)
        if args.speed is not None:
            await badge.set_splash_speed(args.speed)
        if args.save:
            await badge.save_config()
        print(f"Splash set ({xbm.width}x{xbm.height})" + (" and saved." if args.save else "."))

    await with_badge(args, run)


async def cmd_battery(args: argparse.Namespace) -> None:
    async def run(badge: BadgeClient) -> None:
        print(f"{await badge.read_battery()}%")

    await with_badge(args, run)


async def cmd_info(args: argparse.Namespace) -> None:
    async def run(badge: BadgeClient) -> None:
        info = await badge.read_device_info()
        print(f"name:          {badge.name}")
        print(f"address:       {badge.device.address}")
        print(f"next-gen:      {'yes' if badge.has_nextgen else 'no'}")
        for field in ("manufacturer", "model", "firmware_revision", "hardware_revision", "software_revision", "serial_number"):
            value = getattr(info, field)
            if value is not None:
                print(f"{field.replace('_', ' ') + ':':<15}{value}")
        try:
            print(f"battery:       {await badge.read_battery()}%")
        except Exception as err:  # noqa: BLE001 - informational only
            print(f"battery:       unavailable ({err})")

    await with_badge(args, run)


async def cmd_preview(args: argparse.Namespace) -> None:
    content = getattr(args, "content", None) or []
    if not content:
        raise SystemExit("preview: give --text or --image")
    for kind, value in content:
        bitmap = build_bitmap(kind, value, args)
        print(bitmap.to_ascii(args.on, args.off))
        print(f"({bitmap.width} columns, {bitmap.chunk_count} chunks)\n")


# parser --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="badgemagic", description="Control Badge Magic LED badges over Bluetooth LE.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("-v", "--verbose", action="count", default=0, help="more logging (-vv for bleak debug)")
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    target = argparse.ArgumentParser(add_help=False)
    target.add_argument("-a", "--address", help="badge address (a UUID on macOS, a MAC elsewhere)")
    target.add_argument("-n", "--name", help="badge name to pick when several are advertising")
    target.add_argument("-t", "--timeout", type=float, default=8.0, help="scan timeout in seconds (default 8)")

    save = argparse.ArgumentParser(add_help=False)
    save.add_argument("--save", action="store_true", help="also send save_cfg so the setting survives a reboot")

    image_opts = argparse.ArgumentParser(add_help=False)
    image_opts.add_argument("--threshold", type=int_range(0, 255), default=128, help="gray level that lights a pixel (default 128)")
    image_opts.add_argument("--invert", action="store_true", help="light dark pixels instead of bright ones")

    content = argparse.ArgumentParser(add_help=False)
    content.add_argument("--text", action=AppendContent, help="text message (repeatable)")
    content.add_argument("--image", action=AppendContent, help="image file or .xbm (repeatable)")
    content.add_argument("--font", choices=FONT_NAMES, default="normal", help="font for --text (default normal)")

    p = sub.add_parser("scan", help="list advertising badges", parents=[])
    p.add_argument("-t", "--timeout", type=float, default=8.0, help="scan time in seconds (default 8)")
    p.add_argument("--all", action="store_true", help="list every BLE device, not just badges")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("send", help="upload up to 8 messages (legacy protocol)", parents=[target, content, image_opts])
    p.add_argument("--mode", type=parse_mode, default=Mode.SCROLL_LEFT, help="scroll_left (default), scroll_right, scroll_up, scroll_down, fixed, animation, snowflake, picture, laser, or 0..8")
    p.add_argument("--speed", type=int_range(1, 8), default=4, help="1 (slow) .. 8 (fast), default 4")
    p.add_argument("--flash", action="store_true", help="blink the messages")
    p.add_argument("--marquee", action="store_true", help="animated border")
    p.add_argument("--pin", help="4-digit PIN shown in BT-PAIRING mode when SECURITY is on")
    p.add_argument("--no-clock", action="store_true", help="send a zero timestamp instead of setting the badge clock")
    p.add_argument("--brightness", choices=list(OEM_BRIGHTNESS), help="OEM firmware brightness in percent (open firmware ignores it)")
    p.add_argument("--dry-run", action="store_true", help="print the frame packets instead of sending")
    p.add_argument(
        "--linger",
        type=float,
        default=0.0,
        metavar="SECONDS",
        help="stay connected this long after the upload (experimental: 2.5 may keep some firmware builds advertising)",
    )
    p.set_defaults(func=cmd_send)

    p = sub.add_parser("stream-text", help="stream text to the display (next-gen)", parents=[target])
    p.add_argument("text")
    p.add_argument("--font", choices=FONT_NAMES, default="normal")
    p.add_argument("--fps", type=float, default=10.0, help="frames per second (default 10)")
    p.add_argument("--duration", type=float, help="stop after this many seconds")
    p.add_argument("--loop", action="store_true", help="repeat the scroll until --duration or Ctrl-C")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--scroll", action="store_true", help="always scroll")
    group.add_argument("--fixed", action="store_true", help="never scroll (clip to 44 columns)")
    p.add_argument("--no-wait", action="store_true", help="do not wait for each frame's result (faster)")
    p.add_argument("--keep", action="store_true", help="stay in streaming mode when done")
    p.set_defaults(func=cmd_stream_text)

    p = sub.add_parser("set-brightness", help="LED brightness 0..3 (next-gen)", parents=[target, save])
    p.add_argument("level", type=int_range(0, 3))
    p.set_defaults(func=simple("set_brightness", "level"))

    p = sub.add_parser("set-always-on", help="keep Bluetooth on outside BT-PAIRING (next-gen)", parents=[target, save])
    p.add_argument("state", type=parse_on_off, metavar="on|off")
    p.set_defaults(func=simple("set_always_on", "state"))

    p = sub.add_parser("set-name", help="rename the badge's Bluetooth name (next-gen)", parents=[target, save])
    p.add_argument("new_name", metavar="NAME")
    p.set_defaults(func=simple("set_name", "new_name"))

    p = sub.add_parser("set-reset-after-upload", help="reboot after each upload (next-gen)", parents=[target, save])
    p.add_argument("state", type=parse_on_off, metavar="on|off")
    p.set_defaults(func=simple("set_reset_after_upload", "state"))

    p = sub.add_parser("set-splash", help="set the boot splash image (next-gen)", parents=[target, save, image_opts])
    p.add_argument("image", help="image file or .xbm, at most 48x44 and 255 bytes of bits")
    p.add_argument("--frame-height", type=int_range(1, 255), default=11)
    p.add_argument("--speed", type=int_range(0, 65535), help="ms per splash frame")
    p.set_defaults(func=cmd_set_splash)

    for name, method, help_text in (
        ("power-off", "power_off", "power the badge off (next-gen)"),
        ("reboot", "reboot", "reboot the badge (next-gen)"),
        ("save-config", "save_config", "write the current config to flash (next-gen)"),
        ("load-defaults", "load_defaults", "load firmware default config into RAM (next-gen)"),
    ):
        p = sub.add_parser(name, help=help_text, parents=[target])
        p.set_defaults(func=simple(method, save_flag=False))

    p = sub.add_parser("battery", help="read the battery level", parents=[target])
    p.set_defaults(func=cmd_battery)

    p = sub.add_parser("info", help="read device information", parents=[target])
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("preview", help="print rendered text or an image as ASCII art (no Bluetooth)", parents=[content, image_opts])
    p.add_argument("--on", default="#", help="character for lit pixels")
    p.add_argument("--off", default=".", help="character for dark pixels")
    p.set_defaults(func=cmd_preview)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    level = logging.WARNING - 10 * min(args.verbose, 2)
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")
    if args.verbose < 2:
        logging.getLogger("bleak").setLevel(logging.WARNING)
    try:
        asyncio.run(args.func(args))
    except BadgeError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
