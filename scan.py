from flask import Flask, request, send_file, jsonify, send_from_directory
import win32com.client
import pythoncom
import os, uuid, logging, threading, io, sqlite3, csv
from datetime import datetime, timezone, timedelta
from time import perf_counter
from PIL import Image
from flask_cors import CORS

app = Flask(__name__)
CORS(app, origins=[
    "http://127.0.0.1:5500",
    "http://localhost:5500",
    "https://chefbarac.github.io",
])

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("scanner")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCAN_DIR = r"C:\scans"
DB_PATH = os.path.join(BASE_DIR, "scanner_history.sqlite3")
HISTORY_HTML = os.path.join(BASE_DIR, "scanner-history.html")
os.makedirs(SCAN_DIR, exist_ok=True)

WIA_FORMAT_BMP = "{B96B3CAB-0728-11D3-9D7B-0000F81EF32E}"
WIA_PROP_XRES = 6147
WIA_PROP_YRES = 6148

LETTER_WIDTH_IN = 8.5
LETTER_HEIGHT_IN = 11.0
A4_WIDTH_IN = 8.27
A4_HEIGHT_IN = 11.69

SCAN_AREA_PRESETS = {
    "letter": (LETTER_WIDTH_IN, LETTER_HEIGHT_IN),
    "a4": (A4_WIDTH_IN, A4_HEIGHT_IN),
    # Existing optimized ID preset from the user's scanner service.
    "id": (LETTER_WIDTH_IN, LETTER_HEIGHT_IN * (1 / 3)),
}

DEFAULT_PRESET = "letter"
MAX_WIDTH_IN = max(LETTER_WIDTH_IN, A4_WIDTH_IN)
MAX_HEIGHT_IN = max(LETTER_HEIGHT_IN, A4_HEIGHT_IN)
MIN_DIM_IN = 0.5

WIA_DATA_TYPE_CANDIDATES = {
    "color": (3, 2),
    "grayscale": (2, 1),
}
WIA_INTENT_VALUES = {
    "color": 1,
    "grayscale": 2,
}
DEFAULT_IMAGE_TYPE = "color"

scan_lock = threading.Lock()


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db_connect():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db_connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS scan_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_uuid TEXT NOT NULL UNIQUE,
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL,
                status TEXT NOT NULL,
                scanner_name TEXT,
                preset TEXT,
                image_type TEXT,
                requested_dpi INTEGER,
                actual_x_dpi INTEGER,
                actual_y_dpi INTEGER,
                width_in REAL,
                height_in REAL,
                scan_area_sq_in REAL,
                area_ratio_a4 REAL,
                pixel_width INTEGER,
                pixel_height INTEGER,
                megapixels REAL,
                output_bytes INTEGER,
                total_duration_ms REAL,
                transfer_duration_ms REAL,
                processing_duration_ms REAL,
                estimated_carriage_travel_in REAL,
                service TEXT,
                job_type TEXT,
                paper_size TEXT,
                customer_type TEXT,
                price REAL,
                notes TEXT,
                error_message TEXT
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_scan_history_started
            ON scan_history(started_at)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_scan_history_status
            ON scan_history(status)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_scan_history_service
            ON scan_history(service)
        """)
        conn.commit()


def record_scan(data):
    columns = [
        "scan_uuid", "started_at", "finished_at", "status", "scanner_name",
        "preset", "image_type", "requested_dpi", "actual_x_dpi", "actual_y_dpi",
        "width_in", "height_in", "scan_area_sq_in", "area_ratio_a4",
        "pixel_width", "pixel_height", "megapixels", "output_bytes",
        "total_duration_ms", "transfer_duration_ms", "processing_duration_ms",
        "estimated_carriage_travel_in", "service", "job_type", "paper_size",
        "customer_type", "price", "notes", "error_message"
    ]
    values = [data.get(k) for k in columns]
    with db_connect() as conn:
        conn.execute(
            f"INSERT INTO scan_history ({','.join(columns)}) "
            f"VALUES ({','.join(['?'] * len(columns))})",
            values,
        )
        conn.commit()


def get_scanner_item():
    device_manager = win32com.client.Dispatch("WIA.DeviceManager")
    for info in device_manager.DeviceInfos:
        if info.Type == 1:
            device = info.Connect()
            return device.Items[1]
    raise RuntimeError(
        "No WIA scanner found — check Windows Settings > Bluetooth & devices > "
        "Printers & scanners"
    )


def get_scanner_name():
    try:
        device_manager = win32com.client.Dispatch("WIA.DeviceManager")
        for info in device_manager.DeviceInfos:
            if info.Type == 1:
                for attr in ("Properties",):
                    try:
                        value = info.Properties("Name").Value
                        if value:
                            return str(value)
                    except Exception:
                        pass
                try:
                    return str(info.Properties("Description").Value)
                except Exception:
                    pass
                return "WIA Scanner"
    except Exception:
        pass
    return "WIA Scanner"


def set_resolution(item, dpi):
    for prop_name in ("Horizontal Resolution", "Vertical Resolution"):
        try:
            item.Properties(prop_name).Value = dpi
        except Exception as e:
            logger.warning(f"Failed to set {prop_name} to {dpi}: {e}")

    actual_x = item.Properties("Horizontal Resolution").Value
    actual_y = item.Properties("Vertical Resolution").Value
    if actual_x != dpi or actual_y != dpi:
        logger.warning(
            f"Requested {dpi} DPI, driver reports {actual_x}x{actual_y}"
        )
    return actual_x, actual_y


def set_image_type(item, image_type):
    intent_value = WIA_INTENT_VALUES[image_type]
    try:
        item.Properties("Current Intent").Value = intent_value
    except Exception as e:
        logger.warning(
            f"Failed to set Current Intent to {intent_value} "
            f"({image_type}): {e}"
        )

    for candidate in WIA_DATA_TYPE_CANDIDATES[image_type]:
        try:
            item.Properties("Data Type").Value = candidate
            actual = item.Properties("Data Type").Value
            if actual == candidate:
                logger.info(f"Data Type set to {candidate} ({image_type})")
            else:
                logger.warning(
                    f"Set Data Type {candidate}, driver reports {actual}"
                )
            return
        except Exception as e:
            logger.debug(
                f"Data Type candidate {candidate} rejected for {image_type}: {e}"
            )

    logger.warning(
        f"No accepted Data Type value for {image_type} — "
        "relying on Current Intent + PIL conversion"
    )


def set_scan_area(item, dpi_x, dpi_y, width_in, height_in):
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
        logger.warning(
            f"Could not read max extent, using computed size as-is: {e}"
        )

    for prop_name, value in (
        ("Horizontal Start Position", 0),
        ("Vertical Start Position", 0),
        ("Horizontal Extent", width_px),
        ("Vertical Extent", height_px),
    ):
        try:
            item.Properties(prop_name).Value = value
        except Exception as e:
            logger.warning(
                f"Failed to set {prop_name} to {value}: {e}"
            )

    logger.info(
        f"Scan area set to {width_in}x{height_in}in -> "
        f"{width_px}x{height_px}px @ {dpi_x}x{dpi_y} dpi"
    )
    return width_px, height_px


def validate_resolution(raw_value, default=300, min_dpi=75, max_dpi=1200):
    try:
        dpi = int(raw_value)
    except (TypeError, ValueError):
        return default
    if not (min_dpi <= dpi <= max_dpi):
        return default
    return dpi


def validate_dimension(
    raw_value, default, min_in=MIN_DIM_IN, max_in=MAX_WIDTH_IN
):
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
    logger.warning(
        f"Unknown image_type '{raw_value}', falling back to {default}"
    )
    return default


def clean_text(value, max_len=200):
    if value is None:
        return None
    value = str(value).strip()
    return value[:max_len] if value else None


def clean_price(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def resolve_scan_area(payload):
    if payload:
        raw_w = payload.get("width_in")
        raw_h = payload.get("height_in")
        if raw_w is not None and raw_h is not None:
            default_w, default_h = SCAN_AREA_PRESETS[DEFAULT_PRESET]
            width_in = validate_dimension(
                raw_w, default_w, max_in=MAX_WIDTH_IN
            )
            height_in = validate_dimension(
                raw_h, default_h, max_in=MAX_HEIGHT_IN
            )
            return width_in, height_in, "custom"

        size_name = payload.get("size")
        if size_name:
            normalized = str(size_name).lower()
            preset = SCAN_AREA_PRESETS.get(normalized)
            if preset:
                return preset[0], preset[1], normalized
            logger.warning(
                f"Unknown size preset '{size_name}', "
                f"falling back to {DEFAULT_PRESET}"
            )

    w, h = SCAN_AREA_PRESETS[DEFAULT_PRESET]
    return w, h, DEFAULT_PRESET


def build_scan_record(payload, scan_uuid, started_at):
    width_in, height_in, preset = resolve_scan_area(payload)
    image_type = validate_image_type(payload.get("image_type"))
    requested_dpi = validate_resolution(payload.get("resolution", 300))

    scan_area_sq_in = width_in * height_in
    a4_area = A4_WIDTH_IN * A4_HEIGHT_IN
    area_ratio_a4 = scan_area_sq_in / a4_area
    # Proxy only: carriage moves down the requested scan height and back
    # to home in the common flatbed workflow. It is not a manufacturer rating.
    estimated_carriage_travel_in = height_in * 2

    return {
        "scan_uuid": scan_uuid,
        "started_at": started_at,
        "finished_at": started_at,
        "status": "failed",
        "scanner_name": None,
        "preset": preset,
        "image_type": image_type,
        "requested_dpi": requested_dpi,
        "actual_x_dpi": None,
        "actual_y_dpi": None,
        "width_in": width_in,
        "height_in": height_in,
        "scan_area_sq_in": scan_area_sq_in,
        "area_ratio_a4": area_ratio_a4,
        "pixel_width": None,
        "pixel_height": None,
        "megapixels": None,
        "output_bytes": None,
        "total_duration_ms": None,
        "transfer_duration_ms": None,
        "processing_duration_ms": None,
        "estimated_carriage_travel_in": estimated_carriage_travel_in,
        "service": clean_text(payload.get("service")),
        "job_type": clean_text(payload.get("job_type")),
        "paper_size": clean_text(payload.get("paper_size")),
        "customer_type": clean_text(payload.get("customer_type")),
        "price": clean_price(payload.get("price")),
        "notes": clean_text(payload.get("notes"), 500),
        "error_message": None,
    }


@app.route("/scan", methods=["POST"])
def scan():
    if not scan_lock.acquire(blocking=False):
        return jsonify({
            "error": "A scan is already in progress. Try again shortly."
        }), 409

    started_perf = perf_counter()
    started_at = utc_now_iso()
    scan_uuid = str(uuid.uuid4())
    payload = request.get_json(silent=True) or {}
    record = build_scan_record(payload, scan_uuid, started_at)

    pythoncom.CoInitialize()
    png_path = None

    try:
        record["scanner_name"] = get_scanner_name()
        item = get_scanner_item()

        actual_x, actual_y = set_resolution(
            item, record["requested_dpi"]
        )
        record["actual_x_dpi"] = int(actual_x)
        record["actual_y_dpi"] = int(actual_y)

        pixel_width, pixel_height = set_scan_area(
            item,
            actual_x,
            actual_y,
            record["width_in"],
            record["height_in"],
        )
        record["pixel_width"] = pixel_width
        record["pixel_height"] = pixel_height
        record["megapixels"] = (
            pixel_width * pixel_height / 1_000_000
        )

        set_image_type(item, record["image_type"])

        transfer_start = perf_counter()
        image = item.Transfer(WIA_FORMAT_BMP)
        record["transfer_duration_ms"] = (
            perf_counter() - transfer_start
        ) * 1000

        bmp_path = os.path.join(
            SCAN_DIR, f"{scan_uuid}.bmp"
        )
        image.SaveFile(bmp_path)

        processing_start = perf_counter()
        img = Image.open(bmp_path)

        logger.info(
            f"Scanned image size: {img.size}, area: "
            f"{record['width_in']}x{record['height_in']}in, "
            f"requested DPI: {record['requested_dpi']}, "
            f"actual DPI: {actual_x}x{actual_y}, "
            f"image_type: {record['image_type']}"
        )

        if record["image_type"] == "grayscale" and img.mode != "L":
            img = img.convert("L")

        png_path = bmp_path.replace(".bmp", ".png")
        img.save(png_path)
        img.close()
        os.remove(bmp_path)

        with open(png_path, "rb") as f:
            png_bytes = f.read()
        record["output_bytes"] = len(png_bytes)

        os.remove(png_path)
        png_path = None

        record["processing_duration_ms"] = (
            perf_counter() - processing_start
        ) * 1000
        record["total_duration_ms"] = (
            perf_counter() - started_perf
        ) * 1000
        record["finished_at"] = utc_now_iso()
        record["status"] = "success"

        record_scan(record)

        return send_file(
            io.BytesIO(png_bytes),
            mimetype="image/png",
            download_name="scan.png",
        )

    except Exception as e:
        record["total_duration_ms"] = (
            perf_counter() - started_perf
        ) * 1000
        record["finished_at"] = utc_now_iso()
        record["error_message"] = str(e)[:1000]
        logger.exception("Scan failed")

        try:
            record_scan(record)
        except Exception:
            logger.exception("Could not record failed scan")

        return jsonify({"error": str(e)}), 500

    finally:
        if png_path and os.path.exists(png_path):
            try:
                os.remove(png_path)
            except OSError:
                pass
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
                    "valid_values": (
                        getattr(prop, "ValidValues", None)
                        if hasattr(prop, "ValidValues")
                        else None
                    ),
                })
            except Exception as e:
                props.append({"error": str(e)})
        return jsonify({
            "scanner_name": get_scanner_name(),
            "properties": props,
        })
    except Exception as e:
        logger.exception("scanner-info failed")
        return jsonify({"error": str(e)}), 500
    finally:
        pythoncom.CoUninitialize()


@app.route("/scan-sizes", methods=["GET"])
def scan_sizes():
    return jsonify({
        "default": DEFAULT_PRESET,
        "presets": {
            name: {"width_in": w, "height_in": h}
            for name, (w, h) in SCAN_AREA_PRESETS.items()
        },
    })


@app.route("/scan-image-types", methods=["GET"])
def scan_image_types():
    return jsonify({
        "default": DEFAULT_IMAGE_TYPE,
        "types": list(WIA_INTENT_VALUES.keys()),
    })


@app.route("/history", methods=["GET"])
def history_page():
    return send_from_directory(BASE_DIR, "scanner-history.html")


@app.route("/api/history", methods=["GET"])
def api_history():
    limit = max(1, min(int(request.args.get("limit", 200)), 2000))
    status = clean_text(request.args.get("status"))
    service = clean_text(request.args.get("service"))
    date_from = clean_text(request.args.get("date_from"))
    date_to = clean_text(request.args.get("date_to"))

    clauses = []
    params = []

    if status:
        clauses.append("status = ?")
        params.append(status)
    if service:
        clauses.append("service = ?")
        params.append(service)
    if date_from:
        clauses.append("started_at >= ?")
        params.append(date_from + "T00:00:00+00:00")
    if date_to:
        clauses.append("started_at < ?")
        try:
            next_day = datetime.fromisoformat(date_to) + timedelta(days=1)
            params.append(
                next_day.replace(tzinfo=timezone.utc).isoformat(
                    timespec="seconds"
                )
            )
        except ValueError:
            params.append(date_to + "T23:59:59+00:00")

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    with db_connect() as conn:
        rows = conn.execute(
            f"""
            SELECT *
            FROM scan_history
            {where}
            ORDER BY id DESC
            LIMIT ?
            """,
            [*params, limit],
        ).fetchall()

    return jsonify([dict(row) for row in rows])


@app.route("/api/stats", methods=["GET"])
def api_stats():
    with db_connect() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM scan_history"
        ).fetchone()[0]
        success = conn.execute(
            "SELECT COUNT(*) FROM scan_history WHERE status='success'"
        ).fetchone()[0]
        failed = conn.execute(
            "SELECT COUNT(*) FROM scan_history WHERE status='failed'"
        ).fetchone()[0]

        today = conn.execute(
            """
            SELECT COUNT(*)
            FROM scan_history
            WHERE date(started_at, 'localtime') = date('now', 'localtime')
            """
        ).fetchone()[0]

        last_7 = conn.execute(
            """
            SELECT COUNT(*)
            FROM scan_history
            WHERE datetime(started_at) >= datetime('now', '-7 days')
            """
        ).fetchone()[0]

        agg = conn.execute(
            """
            SELECT
                COALESCE(AVG(total_duration_ms), 0),
                COALESCE(AVG(
                    CASE WHEN status='success'
                    THEN total_duration_ms END
                ), 0),
                COALESCE(SUM(scan_area_sq_in), 0),
                COALESCE(SUM(megapixels), 0),
                COALESCE(SUM(output_bytes), 0),
                COALESCE(SUM(estimated_carriage_travel_in), 0),
                COALESCE(SUM(price), 0)
            FROM scan_history
            """
        ).fetchone()

        services = conn.execute(
            """
            SELECT COALESCE(service, '(unspecified)') AS service,
                   COUNT(*) AS scans,
                   COALESCE(SUM(price), 0) AS revenue
            FROM scan_history
            GROUP BY service
            ORDER BY scans DESC
            """
        ).fetchall()

        # Preset comparison: size, area, speed
        presets = conn.execute(
            """
            SELECT
                COALESCE(preset, '(unspecified)') AS preset,
                COUNT(*) AS scans,
                SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS success_scans,
                SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed_scans,
                ROUND(AVG(CASE WHEN status='success' THEN total_duration_ms END), 1)
                    AS avg_success_ms,
                ROUND(AVG(CASE WHEN status='success' THEN transfer_duration_ms END), 1)
                    AS avg_transfer_ms,
                ROUND(AVG(width_in), 2) AS avg_width_in,
                ROUND(AVG(height_in), 2) AS avg_height_in,
                ROUND(AVG(scan_area_sq_in), 2) AS avg_area_sq_in,
                ROUND(AVG(CASE WHEN status='success' THEN megapixels END), 3)
                    AS avg_megapixels,
                ROUND(AVG(CASE WHEN status='success' THEN output_bytes END), 0)
                    AS avg_output_bytes,
                ROUND(
                    AVG(
                        CASE
                            WHEN status='success'
                                 AND scan_area_sq_in > 0
                                 AND total_duration_ms IS NOT NULL
                            THEN total_duration_ms / scan_area_sq_in
                        END
                    ),
                    1
                ) AS avg_ms_per_sq_in,
                ROUND(
                    AVG(
                        CASE
                            WHEN status='success'
                                 AND height_in > 0
                                 AND total_duration_ms IS NOT NULL
                            THEN total_duration_ms / height_in
                        END
                    ),
                    1
                ) AS avg_ms_per_in_height
            FROM scan_history
            GROUP BY preset
            ORDER BY scans DESC
            """
        ).fetchall()

        # Mode (color / grayscale) comparison
        modes = conn.execute(
            """
            SELECT
                COALESCE(image_type, '(unspecified)') AS image_type,
                COUNT(*) AS scans,
                SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS success_scans,
                SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed_scans,
                ROUND(AVG(CASE WHEN status='success' THEN total_duration_ms END), 1)
                    AS avg_success_ms,
                ROUND(AVG(CASE WHEN status='success' THEN transfer_duration_ms END), 1)
                    AS avg_transfer_ms,
                ROUND(AVG(scan_area_sq_in), 2) AS avg_area_sq_in,
                ROUND(AVG(CASE WHEN status='success' THEN megapixels END), 3)
                    AS avg_megapixels,
                ROUND(AVG(CASE WHEN status='success' THEN output_bytes END), 0)
                    AS avg_output_bytes,
                ROUND(
                    AVG(
                        CASE
                            WHEN status='success'
                                 AND scan_area_sq_in > 0
                                 AND total_duration_ms IS NOT NULL
                            THEN total_duration_ms / scan_area_sq_in
                        END
                    ),
                    1
                ) AS avg_ms_per_sq_in
            FROM scan_history
            GROUP BY image_type
            ORDER BY scans DESC
            """
        ).fetchall()

        # Cross analysis: preset x mode
        preset_mode = conn.execute(
            """
            SELECT
                COALESCE(preset, '(unspecified)') AS preset,
                COALESCE(image_type, '(unspecified)') AS image_type,
                COUNT(*) AS scans,
                SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS success_scans,
                ROUND(AVG(CASE WHEN status='success' THEN total_duration_ms END), 1)
                    AS avg_success_ms,
                ROUND(AVG(scan_area_sq_in), 2) AS avg_area_sq_in,
                ROUND(
                    AVG(
                        CASE
                            WHEN status='success'
                                 AND scan_area_sq_in > 0
                                 AND total_duration_ms IS NOT NULL
                            THEN total_duration_ms / scan_area_sq_in
                        END
                    ),
                    1
                ) AS avg_ms_per_sq_in
            FROM scan_history
            GROUP BY preset, image_type
            ORDER BY preset, image_type
            """
        ).fetchall()

        # Area buckets for speed vs area trend
        area_buckets = conn.execute(
            """
            SELECT
                CASE
                    WHEN scan_area_sq_in IS NULL THEN '(unknown)'
                    WHEN scan_area_sq_in < 20 THEN 'small (<20 in2)'
                    WHEN scan_area_sq_in < 50 THEN 'medium (20-50 in2)'
                    WHEN scan_area_sq_in < 90 THEN 'large (50-90 in2)'
                    ELSE 'full (>=90 in2)'
                END AS area_bucket,
                COUNT(*) AS scans,
                SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS success_scans,
                ROUND(AVG(scan_area_sq_in), 2) AS avg_area_sq_in,
                ROUND(AVG(CASE WHEN status='success' THEN total_duration_ms END), 1)
                    AS avg_success_ms,
                ROUND(
                    AVG(
                        CASE
                            WHEN status='success'
                                 AND scan_area_sq_in > 0
                                 AND total_duration_ms IS NOT NULL
                            THEN total_duration_ms / scan_area_sq_in
                        END
                    ),
                    1
                ) AS avg_ms_per_sq_in
            FROM scan_history
            GROUP BY area_bucket
            ORDER BY
                CASE area_bucket
                    WHEN 'small (<20 in2)' THEN 1
                    WHEN 'medium (20-50 in2)' THEN 2
                    WHEN 'large (50-90 in2)' THEN 3
                    WHEN 'full (>=90 in2)' THEN 4
                    ELSE 5
                END
            """
        ).fetchall()

        # Speed ranking: fastest / slowest successful presets by avg duration
        speed_ranking = conn.execute(
            """
            SELECT
                COALESCE(preset, '(unspecified)') AS preset,
                COALESCE(image_type, '(unspecified)') AS image_type,
                COUNT(*) AS success_scans,
                ROUND(AVG(total_duration_ms), 1) AS avg_success_ms,
                ROUND(AVG(scan_area_sq_in), 2) AS avg_area_sq_in,
                ROUND(AVG(total_duration_ms / NULLIF(scan_area_sq_in, 0)), 1)
                    AS avg_ms_per_sq_in
            FROM scan_history
            WHERE status = 'success'
              AND total_duration_ms IS NOT NULL
            GROUP BY preset, image_type
            HAVING COUNT(*) >= 1
            ORDER BY avg_success_ms ASC
            """
        ).fetchall()

        devices = conn.execute(
            """
            SELECT
                COALESCE(scanner_name, 'WIA Scanner') AS display_name,
                COALESCE(scanner_name, 'WIA Scanner') AS device_id,
                COUNT(*) AS scans,
                SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed_scans,
                ROUND(AVG(CASE WHEN status='success' THEN total_duration_ms END), 1)
                    AS avg_success_ms
            FROM scan_history
            GROUP BY scanner_name
            ORDER BY scans DESC
            """
        ).fetchall()

        daily = conn.execute(
            """
            SELECT date(started_at, 'localtime') AS day,
                   COUNT(*) AS scans,
                   ROUND(AVG(total_duration_ms), 1) AS avg_ms
            FROM scan_history
            WHERE datetime(started_at) >= datetime('now', '-30 days')
            GROUP BY day
            ORDER BY day
            """
        ).fetchall()

        last_scan = conn.execute(
            """
            SELECT *
            FROM scan_history
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()

    return jsonify({
        "total_scans": total,
        "successful_scans": success,
        "failed_scans": failed,
        "error_rate_percent": (
            (failed / total * 100) if total else 0
        ),
        "today_scans": today,
        "last_7_days_scans": last_7,
        "average_duration_ms": agg[0],
        "average_success_duration_ms": agg[1],
        "total_scan_area_sq_in": agg[2],
        "total_megapixels": agg[3],
        "total_output_bytes": agg[4],
        "estimated_carriage_travel_in": agg[5],
        "recorded_revenue": agg[6],
        "services": [dict(r) for r in services],
        "presets": [dict(r) for r in presets],
        "modes": [dict(r) for r in modes],
        "preset_mode": [dict(r) for r in preset_mode],
        "area_buckets": [dict(r) for r in area_buckets],
        "speed_ranking": [dict(r) for r in speed_ranking],
        "devices": [dict(r) for r in devices],
        "daily": [dict(r) for r in daily],
        "last_scan": dict(last_scan) if last_scan else None,
        "database": os.path.basename(DB_PATH),
    })


@app.route("/api/export.csv", methods=["GET"])
def export_csv():
    with db_connect() as conn:
        rows = conn.execute(
            "SELECT * FROM scan_history ORDER BY id ASC"
        ).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    if rows:
        writer.writerow(rows[0].keys())
        for row in rows:
            writer.writerow(list(row))

    response = app.response_class(
        output.getvalue(),
        mimetype="text/csv",
    )
    response.headers["Content-Disposition"] = (
        'attachment; filename="scanner_history.csv"'
    )
    return response


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001)
