"""Send to Your Phone: QR codes that real scanners read back."""

import ctypes
import ctypes.util
import unittest

from qt_test_support import application

from browser import qr


def _zbar():
    name = ctypes.util.find_library("zbar") or "libzbar.so.0"
    try:
        z = ctypes.CDLL(name)
    except OSError:
        return None
    z.zbar_image_scanner_create.restype = ctypes.c_void_p
    z.zbar_image_create.restype = ctypes.c_void_p
    z.zbar_image_first_symbol.restype = ctypes.c_void_p
    z.zbar_symbol_get_data.restype = ctypes.c_void_p
    z.zbar_symbol_get_data_length.restype = ctypes.c_uint
    z.zbar_image_scanner_set_config.argtypes = [ctypes.c_void_p] + [ctypes.c_int] * 3
    z.zbar_image_set_format.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    z.zbar_image_set_size.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint]
    z.zbar_image_set_data.argtypes = [ctypes.c_void_p] * 2 + [
        ctypes.c_ulong,
        ctypes.c_void_p,
    ]
    z.zbar_scan_image.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    z.zbar_image_first_symbol.argtypes = [ctypes.c_void_p]
    z.zbar_symbol_get_data.argtypes = [ctypes.c_void_p]
    z.zbar_symbol_get_data_length.argtypes = [ctypes.c_void_p]
    z.zbar_image_destroy.argtypes = [ctypes.c_void_p]
    z.zbar_image_scanner_destroy.argtypes = [ctypes.c_void_p]
    return z


ZBAR = _zbar()


def scan(rows, scale=3, quiet=4):
    """What zbar reads from the code (rows of booleans), or None."""
    side = (len(rows) + 2 * quiet) * scale
    pixels = bytearray(b"\xff" * side * side)
    for y, row in enumerate(rows):
        for x, dark in enumerate(row):
            if dark:
                for line in range((y + quiet) * scale, (y + quiet + 1) * scale):
                    start = line * side + (x + quiet) * scale
                    pixels[start : start + scale] = b"\x00" * scale
    buffer = (ctypes.c_char * len(pixels)).from_buffer(pixels)
    scanner = ZBAR.zbar_image_scanner_create()
    ZBAR.zbar_image_scanner_set_config(scanner, 0, 0, 1)
    image = ZBAR.zbar_image_create()
    ZBAR.zbar_image_set_format(image, int.from_bytes(b"Y800", "little"))
    ZBAR.zbar_image_set_size(image, side, side)
    ZBAR.zbar_image_set_data(image, ctypes.addressof(buffer), len(pixels), None)
    ZBAR.zbar_scan_image(scanner, image)
    symbol = ZBAR.zbar_image_first_symbol(image)
    found = (
        ctypes.string_at(
            ZBAR.zbar_symbol_get_data(symbol), ZBAR.zbar_symbol_get_data_length(symbol)
        )
        if symbol
        else None
    )
    ZBAR.zbar_image_destroy(image)
    ZBAR.zbar_image_scanner_destroy(scanner)
    return found


def capacity(version):
    """Bytes a version holds in byte mode at level M."""
    count = 8 if version <= 9 else 16
    return (qr._data_codewords(version) * 8 - 4 - count) // 8


@unittest.skipIf(ZBAR is None, "zbar is not installed")
class ScanTests(unittest.TestCase):
    def test_every_version_scans_back_exactly(self):
        for version in range(1, 41):
            text = ("https://e.example/" + "x7-" * 800)[: capacity(version)]
            rows = qr.encode(text)
            self.assertEqual(len(rows), version * 4 + 17, version)
            self.assertEqual(scan(rows, scale=2), text.encode(), version)

    def test_unicode_addresses_scan_back(self):
        text = "https://example.org/café?q=naïve"
        self.assertEqual(scan(qr.encode(text)), text.encode())

    def test_too_long_is_refused(self):
        with self.assertRaises(qr.QrTooLong):
            qr.encode("https://e.example/" + "x" * 3000)


class DialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def test_dialog_shows_the_code_and_the_address(self):
        from ui.send_to_phone import SendToPhoneDialog, qr_image

        url = "https://example.org/page"
        dialog = SendToPhoneDialog(url, "A page")
        self.assertFalse(dialog.code.pixmap().isNull())
        self.assertEqual(dialog.address.text(), url)
        image = qr_image(url)
        self.assertEqual(image.width(), image.height())
        dialog.deleteLater()
        accented = SendToPhoneDialog("https://example.org/caf\u00e9", "")
        self.assertEqual(accented.address.text(), "https://example.org/caf%C3%A9")
        accented.deleteLater()
        long = SendToPhoneDialog("https://e.example/" + "x" * 3000, "")
        self.assertIn("too long", long.code.text())
        long.deleteLater()


if __name__ == "__main__":
    unittest.main()
