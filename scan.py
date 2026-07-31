from flask import Flask, request, send_file, jsonify, after_this_request
import win32com.client
import pythoncom
import os, uuid, logging, threading
from PIL import Image
from flask_cors import CORS

app = Flask(__name__)

# Restrict CORS to your actual frontends
CORS(app, origins=["http://localhost:5500", "https://chefbarac.github.io"])

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("scanner")

SCAN_DIR = "C:\\scans"
os.makedirs(SCAN_DIR, exist_ok=True)

WIA_FORMAT_BMP = "{B96B3CAB-0728-11D3-9D7B-0000F81EF32E}"
WIA_PROP_XRES = 6147
WIA_PROP_YRES = 6148

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


def validate_resolution(raw_value, default=300, min_dpi=75, max_dpi=1200):
    try:
        dpi = int(raw_value)
    except (TypeError, ValueError):
        return default
    if not (min_dpi <= dpi <= max_dpi):
        return default
    return dpi


@app.route("/scan", methods=["POST"])
def scan():
    if not scan_lock.acquire(blocking=False):
        return jsonify({"error": "A scan is already in progress. Try again shortly."}), 409

    pythoncom.CoInitialize()
    png_path = None
    try:
        requested_dpi = request.json.get("resolution", 300) if request.is_json else 300
        resolution = validate_resolution(requested_dpi)

        item = get_scanner_item()
        actual_x, actual_y = set_resolution(item, resolution)

        image = item.Transfer(WIA_FORMAT_BMP)

        bmp_path = os.path.join(SCAN_DIR, f"{uuid.uuid4()}.bmp")
        image.SaveFile(bmp_path)

        img = Image.open(bmp_path)
        logger.info(f"Scanned image size: {img.size}, requested DPI: {resolution}, actual DPI: {actual_x}x{actual_y}")

        png_path = bmp_path.replace(".bmp", ".png")
        img.save(png_path)
        img.close()
        os.remove(bmp_path)

        @after_this_request
        def cleanup(response):
            try:
                if png_path and os.path.exists(png_path):
                    os.remove(png_path)
            except Exception as e:
                logger.warning(f"Failed to clean up {png_path}: {e}")
            return response

        return send_file(png_path, mimetype="image/png")

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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001)