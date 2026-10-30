"""Generate deterministic, OCR-readable SnapSeek evaluation screenshots."""

import argparse
import binascii
from pathlib import Path
import struct
import zlib


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "evaluation" / "generated"
WIDTH, HEIGHT = 720, 440
SCALE = 3

# A tiny fixed 5x7 bitmap font avoids platform fonts and image dependencies.
FONT = {
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "C": ("01111", "10000", "10000", "10000", "10000", "10000", "01111"),
    "D": ("11110", "10001", "10001", "10001", "10001", "10001", "11110"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    "G": ("01111", "10000", "10000", "10111", "10001", "10001", "01111"),
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "I": ("11111", "00100", "00100", "00100", "00100", "00100", "11111"),
    "J": ("00111", "00010", "00010", "00010", "10010", "10010", "01100"),
    "K": ("10001", "10010", "10100", "11000", "10100", "10010", "10001"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "N": ("10001", "11001", "10101", "10011", "10001", "10001", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "Q": ("01110", "10001", "10001", "10001", "10101", "10010", "01101"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    "V": ("10001", "10001", "10001", "10001", "10001", "01010", "00100"),
    "W": ("10001", "10001", "10001", "10101", "10101", "10101", "01010"),
    "X": ("10001", "10001", "01010", "00100", "01010", "10001", "10001"),
    "Y": ("10001", "10001", "01010", "00100", "00100", "00100", "00100"),
    "Z": ("11111", "00001", "00010", "00100", "01000", "10000", "11111"),
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11110", "00001", "00001", "01110", "00001", "00001", "11110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "10000", "11110", "00001", "00001", "11110"),
    "6": ("01110", "10000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00001", "01110"),
    ":": ("00000", "00100", "00100", "00000", "00100", "00100", "00000"),
    ".": ("00000", "00000", "00000", "00000", "00000", "00100", "00100"),
    ",": ("00000", "00000", "00000", "00000", "00100", "00100", "01000"),
    "-": ("00000", "00000", "00000", "11111", "00000", "00000", "00000"),
    "/": ("00001", "00010", "00010", "00100", "01000", "01000", "10000"),
    "$": ("00100", "01111", "10100", "01110", "00101", "11110", "00100"),
    "#": ("01010", "11111", "01010", "01010", "11111", "01010", "00000"),
    "!": ("00100", "00100", "00100", "00100", "00100", "00000", "00100"),
    "@": ("01110", "10001", "10111", "10101", "10111", "10000", "01111"),
    "'": ("00100", "00100", "00000", "00000", "00000", "00000", "00000"),
    "?": ("01110", "10001", "00001", "00010", "00100", "00000", "00100"),
}

SCREENS = (
    ("receipt_payment_declined", "PAYMENT RECEIPT", ("MERCHANT: NORTHSTAR", "ORDER: 4821", "CARD PAYMENT DECLINED", "TOTAL: $48.20"), "#b64242"),
    ("receipt_duplicate_refund", "REFUND RECEIPT", ("MERCHANT: MARKET LANE", "ORDER: 7734", "DUPLICATE CHARGE REFUNDED", "REFUND: $18.50"), "#27836b"),
    ("chat_meeting_room", "TEAM CHAT", ("JORDAN: DESIGN REVIEW MOVED", "TODAY AT 3 PM", "ROOM: MAPLE", "BRING THE NEW MOCKUPS"), "#5367a8"),
    ("calendar_team_sync", "CALENDAR EVENT", ("PRODUCT TEAM SYNC", "THURSDAY AT 10 AM", "LOCATION: MAPLE ROOM", "AGENDA: DESIGN REVIEW"), "#7b58a4"),
    ("email_flight_delay", "AIRLINE EMAIL", ("FLIGHT 218 STATUS UPDATE", "DEPARTURE DELAYED 90 MIN", "NEW TIME: 6:45 PM", "GATE: 12"), "#397b9d"),
    ("email_hotel_booking", "BOOKING CONFIRMATION", ("HOTEL: RIVER HOUSE", "CHECK IN: AUGUST 12", "RESERVATION: 59021", "TWO NIGHTS CONFIRMED"), "#397b9d"),
    ("error_python_module", "PYTHON ERROR", ("MODULE NOT FOUND", "NO MODULE NAMED REQUESTS", "FILE: REPORT.PY", "LINE: 24"), "#b64242"),
    ("error_network_timeout", "NETWORK ERROR", ("REQUEST TIMED OUT", "SERVER: API.NORTHSTAR.COM", "RETRY AFTER 30 SECONDS", "STATUS CODE: 504"), "#b64242"),
    ("dashboard_sales", "SALES DASHBOARD", ("MONTHLY SALES BY REGION", "NORTH: $42000", "SOUTH: $36500", "WEST: $51800"), "#27836b"),
    ("dashboard_refunds", "PAYMENTS DASHBOARD", ("REFUND SUMMARY", "DECLINED PAYMENTS: 14", "DUPLICATE CHARGES: 3", "TOTAL REFUNDED: $860"), "#27836b"),
    ("notes_grocery_list", "GROCERY NOTES", ("WEEKLY SHOPPING LIST", "APPLES AND OAT MILK", "RICE, COFFEE, TOMATOES", "PICK UP AFTER WORK"), "#9b7540"),
    ("map_train_route", "TRANSIT DIRECTIONS", ("CENTRAL STATION TO HARBOR", "TRAIN LEAVES AT 8:15 AM", "PLATFORM: 4", "ARRIVE IN 22 MINUTES"), "#397b9d"),
    ("chat_package_delivery", "DELIVERY CHAT", ("COURIER: PACKAGE DELIVERED", "LEFT AT FRONT DESK", "BUILDING: 18 WILLOW WAY", "TIME: 11:42 AM"), "#5367a8"),
    ("invoice_cloud_storage", "CLOUD INVOICE", ("INVOICE: 90417", "CLOUD STORAGE PLAN", "RENEWAL DATE: MAY 30", "AMOUNT DUE: $12.00"), "#7b58a4"),
    ("settings_wifi", "NETWORK SETTINGS", ("OFFICE WIRELESS NETWORK", "NETWORK NAME: NORTHSTAR", "STATUS: CONNECTED", "SECURITY: WPA2"), "#5367a8"),
    ("browser_password_reset", "ACCOUNT SECURITY", ("PASSWORD RESET REQUEST", "ACCOUNT: ALEX@EXAMPLE.COM", "RESET LINK EXPIRES IN 15 MIN", "IF THIS WAS NOT YOU, IGNORE"), "#397b9d"),
)


def _rgb(hex_color: str) -> tuple[int, int, int]:
    value = hex_color.lstrip("#")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))


def _rectangle(pixels: bytearray, x: int, y: int, width: int, height: int, color) -> None:
    for row in range(max(0, y), min(HEIGHT, y + height)):
        start = (row * WIDTH + max(0, x)) * 3
        end = (row * WIDTH + min(WIDTH, x + width)) * 3
        pixels[start:end] = bytes(color) * ((end - start) // 3)


def _draw_text(pixels: bytearray, text: str, x: int, y: int, color) -> None:
    cursor_x = x
    for character in text.upper():
        if character == " ":
            cursor_x += 4 * SCALE
            continue
        glyph = FONT.get(character, FONT["?"])
        for row, pattern in enumerate(glyph):
            for column, lit in enumerate(pattern):
                if lit == "1":
                    _rectangle(
                        pixels,
                        cursor_x + column * SCALE,
                        y + row * SCALE,
                        SCALE,
                        SCALE,
                        color,
                    )
        cursor_x += 6 * SCALE


def _chunk(kind: bytes, payload: bytes) -> bytes:
    body = kind + payload
    return struct.pack(">I", len(payload)) + body + struct.pack(">I", binascii.crc32(body) & 0xFFFFFFFF)


def _write_png(path: Path, pixels: bytearray) -> None:
    scanlines = b"".join(
        b"\x00" + pixels[row * WIDTH * 3 : (row + 1) * WIDTH * 3]
        for row in range(HEIGHT)
    )
    png = b"\x89PNG\r\n\x1a\n"
    png += _chunk(b"IHDR", struct.pack(">IIBBBBB", WIDTH, HEIGHT, 8, 2, 0, 0, 0))
    png += _chunk(b"IDAT", zlib.compress(scanlines, level=9))
    png += _chunk(b"IEND", b"")
    path.write_bytes(png)


def render_screen(title: str, lines: tuple[str, ...], accent: str) -> bytearray:
    pixels = bytearray((245, 247, 250) * WIDTH * HEIGHT)
    _rectangle(pixels, 0, 0, WIDTH, 42, (37, 48, 66))
    for index, color in enumerate(((220, 90, 90), (230, 180, 75), (92, 175, 120))):
        _rectangle(pixels, 18 + index * 22, 15, 10, 10, color)
    _rectangle(pixels, 34, 70, WIDTH - 68, HEIGHT - 104, (255, 255, 255))
    _rectangle(pixels, 34, 70, 8, HEIGHT - 104, _rgb(accent))
    _draw_text(pixels, title, 65, 92, (37, 48, 66))
    _rectangle(pixels, 65, 128, WIDTH - 130, 3, (220, 225, 232))
    y = 157
    for line in lines:
        _draw_text(pixels, line, 65, y, (48, 59, 76))
        y += 49
    return pixels


def generate_dataset(output_dir: Path = DEFAULT_OUTPUT) -> tuple[str, ...]:
    """Write or refresh the fixed set of generated screenshots."""
    output_dir.mkdir(parents=True, exist_ok=True)
    ids = []
    for screenshot_id, title, lines, accent in SCREENS:
        _write_png(output_dir / f"{screenshot_id}.png", render_screen(title, lines, accent))
        ids.append(screenshot_id)
    return tuple(ids)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    ids = generate_dataset(args.output_dir)
    print(f"Generated {len(ids)} synthetic screenshots in {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
