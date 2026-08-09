from flask import Flask, request, send_file, jsonify, after_this_request
import win32com.client
import pythoncom
import os, uuid, logging, threading, io
from PIL import Image
from flask_cors import CORS

app = Flask(__name__)

# Restrict CORS to your actual frontends
CORS(app, origins=["http://127.0.0.1:5500", "http://localhost:5500", "https://chefbarac.github.io"])

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("scanner")

SCAN_DIR = "C:\\scans"
os.makedirs(SCAN_DIR, exist_ok=True)

WIA_FORMAT_BMP = "{B96B3CAB-0728-11D3-9D7B-0000F81EF32E}"
WIA_PROP_XRES = 6147
WIA_PROP_YRES = 6148

# Named presets (width_in, height_in). "letter" is the default/fallback.
# "id" is 1/4 letter (half width, half height) — a quadrant of the flatbed,
# roomy enough to fit most ID card sizes/orientations without scanning the
# whole bed, so it transfers much faster than a full letter scan.
LETTER_WIDTH_IN = 8.5
LETTER_HEIGHT_IN = 11.0

# A4 is slightly narrower but taller than US Letter (210mm x 297mm).
A4_WIDTH_IN = 8.27
A4_HEIGHT_IN = 11.69

SCAN_AREA_PRESETS = {
    "letter": (LETTER_WIDTH_IN, LETTER_HEIGHT_IN),
    "a4": (A4_WIDTH_IN, A4_HEIGHT_IN),
    "id": (LETTER_WIDTH_IN, LETTER_HEIGHT_IN * (1 / 3)),  # 6.375 x 8.25in, 9/16 letter
}

DEFAULT_PRESET = "letter"
# Bounds must cover the largest preset in each dimension (A4 is taller than
# Letter, Letter is wider than A4), so custom width_in/height_in values and
# the extent clamping in set_scan_area() don't reject valid A4 scans.
MAX_WIDTH_IN = max(LETTER_WIDTH_IN, A4_WIDTH_IN)
MAX_HEIGHT_IN = max(LETTER_HEIGHT_IN, A4_HEIGHT_IN)
MIN_DIM_IN = 0.5

# WIA "Data Type" property (WIA_IPA_DATATYPE) encoding is driver-dependent —
# the WIA spec defines 0=threshold,1=dither,2=grayscale,3=color,4=color
# threshold,5=color dither, but some drivers deviate. Rather than hardcode one
# guess (which throws E_INVALIDARG / "The parameter is incorrect" on drivers
# that disagree), try a short list of plausible candidates per mode and keep
# the first one the driver accepts.
WIA_DATA_TYPE_CANDIDATES = {
    "color": (3, 2),
    "grayscale": (2, 1),
}

# "Current Intent" (WIA_IPS_CUR_INTENT) is a more portable hint most drivers
# honor even when their raw Data Type numbering differs.
WIA_INTENT_VALUES = {
    "color": 1,       # WIA_INTENT_IMAGE_TYPE_COLOR
    "grayscale": 2,   # WIA_INTENT_IMAGE_TYPE_GRAYSCALE
}

DEFAULT_IMAGE_TYPE = "color"

scan_lock = threading.Lock()  # WIA devices generally can't handle concurrent transfers


def get_scanner_item():
    device_manager = win32com.client.Dispatch("WIA.DeviceManager")
    for info in device_manager.DeviceInfos:
        if info.Type == 1:  # scanner device type
            device = info.Connect()
            return device.Items[1]
    raise RuntimeError("No WIA scanner found — check Windows Settings > Bluetooth & devices > Printers & scanners")


def set_resolution(item, dpi):
    """Set X/Y resolution by property NAME — this driver's Properties()
    indexer treats numeric args as positional index, not property ID,
    which is why ID-based access throws 'Index out of range'."""
    for prop_name in ("Horizontal Resolution", "Vertical Resolution"):
        try:
            item.Properties(prop_name).Value = dpi
        except Exception as e:
            logger.warning(f"Failed to set {prop_name} to {dpi}: {e}")

    actual_x = item.Properties("Horizontal Resolution").Value
    actual_y = item.Properties("Vertical Resolution").Value
    if actual_x != dpi or actual_y != dpi:
        logger.warning(f"Requested {dpi} DPI, driver reports {actual_x}x{actual_y}")
    return actual_x, actual_y


def set_image_type(item, image_type):
    """Set color mode. Tries 'Current Intent' (the more portable hint) and
    a short list of candidate 'Data Type' values, since the latter's numeric
    encoding varies by driver. Failures here are non-fatal — PIL does a final
    grayscale conversion after transfer as a correctness safety net."""
    intent_value = WIA_INTENT_VALUES[image_type]
    try:
        item.Properties("Current Intent").Value = intent_value
    except Exception as e:
        logger.warning(f"Failed to set Current Intent to {intent_value} ({image_type}): {e}")

    for candidate in WIA_DATA_TYPE_CANDIDATES[image_type]:
        try:
            item.Properties("Data Type").Value = candidate
            actual = item.Properties("Data Type").Value
            if actual == candidate:
                logger.info(f"Data Type set to {candidate} ({image_type})")
            else:
                logger.warning(f"Set Data Type {candidate}, driver reports {actual}")
            return
        except Exception as e:
            logger.debug(f"Data Type candidate {candidate} rejected for {image_type}: {e}")

    logger.warning(f"No accepted Data Type value for {image_type} — relying on Current Intent + PIL conversion")


def set_scan_area(item, dpi_x, dpi_y, width_in, height_in):
    """Constrain the scan area to the requested width/height (in inches)
    instead of the full flatbed. Smaller areas transfer far fewer pixels
    at a given DPI, so e.g. an ID card scan finishes much faster than a
    full letter-size scan. Start offsets are 0,0 and extents are computed
    in pixels from the actual reported resolution, then clamped to the
    driver's max extent so this doesn't throw on flatbeds smaller than
    the requested area."""
    width_px = int(width_in * dpi_x)
    height_px = int(height_in * dpi_y)

    try:
        max_width = item.Properties("Horizontal Extent").SubTypeMax
        max_height = item.Properties("Vertical Extent").SubTypeMax
        if max_width:
            width_px = min(width_px, int(max_width))
        if max_height:
            height_px = min(height_px, int(max_height))
    except Exception as e:
        logger.warning(f"Could not read max extent, using computed size as-is: {e}")

    for prop_name, value in (
        ("Horizontal Start Position", 0),
        ("Vertical Start Position", 0),
        ("Horizontal Extent", width_px),
        ("Vertical Extent", height_px),
    ):
        try:
            item.Properties(prop_name).Value = value
        except Exception as e:
            logger.warning(f"Failed to set {prop_name} to {value}: {e}")

    logger.info(f"Scan area set to {width_in}x{height_in}in -> {width_px}x{height_px}px @ {dpi_x}x{dpi_y} dpi")


def validate_resolution(raw_value, default=300, min_dpi=75, max_dpi=1200):
    try:
        dpi = int(raw_value)
    except (TypeError, ValueError):
        return default
    if not (min_dpi <= dpi <= max_dpi):
        return default
    return dpi


def validate_dimension(raw_value, default, min_in=MIN_DIM_IN, max_in=MAX_WIDTH_IN):
    try:
        val = float(raw_value)
    except (TypeError, ValueError):
        return default
    if not (min_in <= val <= max_in):
        return default
    return val


def validate_image_type(raw_value, default=DEFAULT_IMAGE_TYPE):
    if raw_value is None:
        return default
    val = str(raw_value).strip().lower()
    if val in WIA_INTENT_VALUES:
        return val
    logger.warning(f"Unknown image_type '{raw_value}', falling back to {default}")
    return default


def resolve_scan_area(payload):
    """Figure out (width_in, height_in) from the request body.

    Priority:
      1. Explicit width_in/height_in (custom dimensions, clamped to sane bounds)
      2. Named preset via "size" (e.g. "id", "letter", "a4")
      3. Default preset (letter)
    """
    if payload:
        raw_w = payload.get("width_in")
        raw_h = payload.get("height_in")
        if raw_w is not None and raw_h is not None:
            default_w, default_h = SCAN_AREA_PRESETS[DEFAULT_PRESET]
            width_in = validate_dimension(raw_w, default_w, max_in=MAX_WIDTH_IN)
            height_in = validate_dimension(raw_h, default_h, max_in=MAX_HEIGHT_IN)
            return width_in, height_in

        size_name = payload.get("size")
        if size_name:
            preset = SCAN_AREA_PRESETS.get(str(size_name).lower())
            if preset:
                return preset
            logger.warning(f"Unknown size preset '{size_name}', falling back to {DEFAULT_PRESET}")

    return SCAN_AREA_PRESETS[DEFAULT_PRESET]


@app.route("/scan", methods=["POST"])
def scan():
    if not scan_lock.acquire(blocking=False):
        return jsonify({"error": "A scan is already in progress. Try again shortly."}), 409

    pythoncom.CoInitialize()
    png_path = None
    try:
        payload = request.get_json(silent=True) or {}
        requested_dpi = payload.get("resolution", 300)
        resolution = validate_resolution(requested_dpi)
        width_in, height_in = resolve_scan_area(payload)
        image_type = validate_image_type(payload.get("image_type"))

        item = get_scanner_item()
        actual_x, actual_y = set_resolution(item, resolution)
        set_scan_area(item, actual_x, actual_y, width_in, height_in)
        set_image_type(item, image_type)

        image = item.Transfer(WIA_FORMAT_BMP)

        bmp_path = os.path.join(SCAN_DIR, f"{uuid.uuid4()}.bmp")
        image.SaveFile(bmp_path)

        img = Image.open(bmp_path)
        logger.info(
            f"Scanned image size: {img.size}, area: {width_in}x{height_in}in, "
            f"requested DPI: {resolution}, actual DPI: {actual_x}x{actual_y}, "
            f"image_type: {image_type}"
        )

        # Safety net: force grayscale in software in case the driver ignored
        # the Data Type property (some WIA drivers only honor it partially).
        if image_type == "grayscale" and img.mode != "L":
            img = img.convert("L")

        png_path = bmp_path.replace(".bmp", ".png")
        img.save(png_path)
        img.close()
        os.remove(bmp_path)

        # Read the PNG into memory and remove it from disk immediately,
        # rather than deleting after the response streams. On Windows,
        # send_file's dev-server file wrapper can still hold the handle
        # open when after_this_request fires, causing WinError 32.
        with open(png_path, "rb") as f:
            png_bytes = f.read()
        os.remove(png_path)
        png_path = None

        return send_file(
            io.BytesIO(png_bytes),
            mimetype="image/png",
            download_name="scan.png",
        )

    except Exception as e:
        logger.exception("Scan failed")
        return jsonify({"error": str(e)}), 500
    finally:
        pythoncom.CoUninitialize()
        scan_lock.release()

@app.route("/scanner-info", methods=["GET"])
def scanner_info():
    pythoncom.CoInitialize()
    try:
        item = get_scanner_item()
        props = []
        for prop in item.Properties:
            try:
                props.append({
                    "id": prop.PropertyID,
                    "name": prop.Name,
                    "value": prop.Value,
                    "valid_values": getattr(prop, "ValidValues", None) if hasattr(prop, "ValidValues") else None
                })
            except Exception as e:
                props.append({"error": str(e)})
        return jsonify(props)
    except Exception as e:
        logger.exception("scanner-info failed")
        return jsonify({"error": str(e)}), 500
    finally:
        pythoncom.CoUninitialize()

@app.route("/scan-sizes", methods=["GET"])
def scan_sizes():
    """Lets the frontend populate a size dropdown without hardcoding presets."""
    return jsonify({
        "default": DEFAULT_PRESET,
        "presets": {name: {"width_in": w, "height_in": h} for name, (w, h) in SCAN_AREA_PRESETS.items()},
    })

@app.route("/scan-image-types", methods=["GET"])
def scan_image_types():
    """Lets the frontend populate an image-type (color/grayscale) dropdown."""
    return jsonify({
        "default": DEFAULT_IMAGE_TYPE,
        "types": list(WIA_INTENT_VALUES.keys()),
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001)