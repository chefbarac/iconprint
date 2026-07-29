from flask import Flask, request, send_file, jsonify
import win32com.client
import pythoncom
import os, uuid
from PIL import Image
from flask_cors import CORS

app = Flask(__name__)
CORS(app)  # add this line right after creating the app
SCAN_DIR = "C:\\scans"
os.makedirs(SCAN_DIR, exist_ok=True)

WIA_FORMAT_BMP = "{B96B3CAB-0728-11D3-9D7B-0000F81EF32E}"
WIA_PROP_XRES = 6147
WIA_PROP_YRES = 6148


def get_scanner_item():
    device_manager = win32com.client.Dispatch("WIA.DeviceManager")
    for info in device_manager.DeviceInfos:
        if info.Type == 1:  # scanner device type
            device = info.Connect()
            return device.Items[1]
    raise RuntimeError("No WIA scanner found — check Windows Settings > Bluetooth & devices > Printers & scanners")


def set_resolution(item, dpi):
    for prop_id in (WIA_PROP_XRES, WIA_PROP_YRES):
        try:
            item.Properties(prop_id).Value = dpi
        except Exception:
            pass  # driver may not expose this property ID; falls back to its default resolution


@app.route("/scan", methods=["POST"])
def scan():
    pythoncom.CoInitialize()
    try:
        resolution = request.json.get("resolution", 300) if request.is_json else 300

        item = get_scanner_item()
        set_resolution(item, resolution)

        image = item.Transfer(WIA_FORMAT_BMP)

        bmp_path = os.path.join(SCAN_DIR, f"{uuid.uuid4()}.bmp")
        image.SaveFile(bmp_path)

        img = Image.open(bmp_path)
        print(img.size)

        png_path = bmp_path.replace(".bmp", ".png")
        Image.open(bmp_path).save(png_path)
        os.remove(bmp_path)

        return send_file(png_path, mimetype="image/png")
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        pythoncom.CoUninitialize()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001)