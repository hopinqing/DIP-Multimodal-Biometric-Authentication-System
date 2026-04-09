import os
import cv2
import time
import json
import math
import sqlite3
import ctypes
import hashlib
import random
import shutil
import datetime as dt
import numpy as np
from pathlib import Path
from collections import defaultdict
from tkinter import *
from tkinter import ttk, filedialog, messagebox, simpledialog

try:
    from PIL import Image, ImageTk
except ImportError:
    raise ImportError("Please install Pillow first: pip install pillow")


# =========================
# Configuration
# =========================
APP_TITLE = "Iris Biometric Security System"
DB_PATH = "iris_auth.db"
SCANS_DIR = "saved_scans"
SUPPORTED_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff")

# Recognition thresholds
IDENTIFY_REJECT_THRESHOLD = 0.36
VERIFY_ACCEPT_THRESHOLD = 0.2
VERIFY_MARGIN_THRESHOLD = 0.02

# Quality thresholds
MIN_STRIP_NONZERO_RATIO = 0.45
MIN_STRIP_CONTRAST = 18.0

# Security policy
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_SECONDS = 60

# Enrollment policy
MIN_ENROLL_SAMPLES = 3
MAX_ENROLL_INCONSISTENCY = 0.34

# SDK
IDDK_DLL_PATH = r"C:\Program Files (x86)\IriTech\IDDK 2000 3.3.3 x64\SDK\Bin\Iddk2000.dll"


# =========================
# Helpers
# =========================
def utcnow_str():
    return dt.datetime.utcnow().isoformat(timespec="seconds")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def is_image_file(path):
    return str(path).lower().endswith(SUPPORTED_EXTS)


def np_to_blob(arr: np.ndarray) -> bytes:
    return arr.astype(np.uint8).tobytes()


def blob_to_np(blob: bytes, shape, dtype=np.uint8) -> np.ndarray:
    return np.frombuffer(blob, dtype=dtype).reshape(shape).copy()


def ensure_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            user_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            disabled INTEGER NOT NULL DEFAULT 0
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            eye_side TEXT NOT NULL,
            radial_res INTEGER NOT NULL,
            angular_res INTEGER NOT NULL,
            channels INTEGER NOT NULL,
            template_blob BLOB NOT NULL,
            quality_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(user_id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS auth_state (
            user_id TEXT PRIMARY KEY,
            failed_attempts INTEGER NOT NULL DEFAULT 0,
            locked_until TEXT,
            FOREIGN KEY(user_id) REFERENCES users(user_id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            event_type TEXT NOT NULL,
            user_id TEXT,
            eye_side TEXT,
            match_score REAL,
            result TEXT NOT NULL,
            details_json TEXT
        )
    """)

    conn.commit()
    conn.close()


def db_connect():
    return sqlite3.connect(DB_PATH)


def ensure_scan_dirs():
    Path(SCANS_DIR).mkdir(parents=True, exist_ok=True)


def save_scan_for_user(image, user_id, eye_side):
    ensure_scan_dirs()
    user_dir = Path(SCANS_DIR) / user_id / eye_side
    user_dir.mkdir(parents=True, exist_ok=True)

    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    out_path = user_dir / f"{user_id}_{eye_side}_{ts}.png"

    if image is None:
        raise ValueError("No scanned image is available to save.")

    arr = np.asarray(image)
    if arr.ndim == 3:
        arr = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY)
    ok = cv2.imwrite(str(out_path), arr)
    if not ok:
        raise IOError(f"Failed to save scanned image to {out_path}")
    return str(out_path)


def copy_image_for_user(src_path, user_id, eye_side):
    ensure_scan_dirs()
    src = Path(src_path)
    if not src.exists():
        raise FileNotFoundError(f"Image file not found: {src_path}")

    user_dir = Path(SCANS_DIR) / user_id / eye_side
    user_dir.mkdir(parents=True, exist_ok=True)

    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    safe_stem = src.stem.replace(" ", "_")
    ext = src.suffix if src.suffix else ".png"
    out_path = user_dir / f"{safe_stem}_{ts}{ext}"
    shutil.copy2(str(src), str(out_path))
    return str(out_path)


def persist_image_sources_for_user(image_sources, user_id, eye_side):
    persisted = []
    saved_files = []
    failed = []

    for src in image_sources:
        try:
            if isinstance(src, str):
                saved_path = copy_image_for_user(src, user_id, eye_side)
            else:
                saved_path = save_scan_for_user(src, user_id, eye_side)
            persisted.append(saved_path)
            saved_files.append(saved_path)
        except Exception as e:
            name = src if isinstance(src, str) else "<captured_frame>"
            failed.append(f"{name} -> {e}")

    return persisted, saved_files, failed


def count_user_templates(user_id, eye_side=None, include_disabled=False):
    conn = db_connect()
    cur = conn.cursor()

    sql = """
        SELECT COUNT(*)
        FROM templates t
        JOIN users u ON t.user_id = u.user_id
        WHERE t.user_id=?
    """
    params = [user_id]

    if eye_side is not None:
        sql += " AND t.eye_side=?"
        params.append(eye_side)

    if not include_disabled:
        sql += " AND u.disabled=0"

    cur.execute(sql, params)
    count = cur.fetchone()[0]
    conn.close()
    return count


def log_event(event_type, result, user_id=None, eye_side=None, match_score=None, details=None):
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO audit_log (timestamp, event_type, user_id, eye_side, match_score, result, details_json)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        utcnow_str(),
        event_type,
        user_id,
        eye_side,
        match_score,
        result,
        json.dumps(details or {})
    ))
    conn.commit()
    conn.close()


def get_users():
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE disabled=0 ORDER BY user_id")
    rows = [r[0] for r in cur.fetchall()]
    conn.close()
    return rows


def user_exists(user_id):
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM users WHERE user_id=?", (user_id,))
    exists = cur.fetchone() is not None
    conn.close()
    return exists


def create_user_if_missing(user_id):
    if user_exists(user_id):
        return
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO users (user_id, user_hash, created_at, disabled)
        VALUES (?, ?, ?, 0)
    """, (user_id, sha256_text(user_id), utcnow_str()))
    cur.execute("""
        INSERT OR IGNORE INTO auth_state (user_id, failed_attempts, locked_until)
        VALUES (?, 0, NULL)
    """, (user_id,))
    conn.commit()
    conn.close()


def disable_user(user_id):
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("UPDATE users SET disabled=1 WHERE user_id=?", (user_id,))
    conn.commit()
    changed = cur.rowcount > 0
    conn.close()
    return changed


def clear_user_templates(user_id, eye_side=None):
    conn = db_connect()
    cur = conn.cursor()
    if eye_side is None:
        cur.execute("DELETE FROM templates WHERE user_id=?", (user_id,))
    else:
        cur.execute("DELETE FROM templates WHERE user_id=? AND eye_side=?", (user_id, eye_side))
    conn.commit()
    conn.close()


def insert_template(user_id, eye_side, template, quality):
    radial_res = template.shape[1]
    angular_res = template.shape[2]
    channels = template.shape[0]

    conn = db_connect()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO templates
        (user_id, eye_side, radial_res, angular_res, channels, template_blob, quality_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        user_id,
        eye_side,
        radial_res,
        angular_res,
        channels,
        sqlite3.Binary(np_to_blob(template)),
        json.dumps(quality),
        utcnow_str(),
    ))
    conn.commit()
    conn.close()


def load_templates(user_id=None, eye_side=None, include_disabled=False):
    conn = db_connect()
    cur = conn.cursor()

    sql = """
        SELECT t.user_id, t.eye_side, t.radial_res, t.angular_res, t.channels, t.template_blob, t.quality_json
        FROM templates t
        JOIN users u ON t.user_id = u.user_id
        WHERE 1=1
    """
    params = []

    if not include_disabled:
        sql += " AND u.disabled=0"

    if user_id is not None:
        sql += " AND t.user_id=?"
        params.append(user_id)

    if eye_side is not None:
        sql += " AND t.eye_side=?"
        params.append(eye_side)

    cur.execute(sql, params)
    rows = cur.fetchall()
    conn.close()

    out = []
    for uid, eside, radial_res, angular_res, channels, blob, quality_json in rows:
        tpl = blob_to_np(blob, (channels, radial_res, angular_res), dtype=np.uint8)
        out.append({
            "user_id": uid,
            "eye_side": eside,
            "template": tpl,
            "quality": json.loads(quality_json),
        })
    return out


def get_user_lock_state(user_id):
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("SELECT failed_attempts, locked_until FROM auth_state WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    conn.close()

    if row is None:
        return 0, None

    failed_attempts, locked_until = row
    return failed_attempts, locked_until


def is_user_locked(user_id):
    failed_attempts, locked_until = get_user_lock_state(user_id)
    if not locked_until:
        return False, 0

    lock_dt = dt.datetime.fromisoformat(locked_until)
    now = dt.datetime.utcnow()
    if now >= lock_dt:
        reset_failures(user_id)
        return False, 0

    remaining = int((lock_dt - now).total_seconds())
    return True, max(remaining, 0)


def reset_failures(user_id):
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO auth_state (user_id, failed_attempts, locked_until)
        VALUES (?, 0, NULL)
        ON CONFLICT(user_id) DO UPDATE SET failed_attempts=0, locked_until=NULL
    """, (user_id,))
    conn.commit()
    conn.close()


def record_failed_attempt(user_id):
    failed_attempts, _ = get_user_lock_state(user_id)
    failed_attempts += 1

    locked_until = None
    if failed_attempts >= MAX_FAILED_ATTEMPTS:
        locked_until = (dt.datetime.utcnow() + dt.timedelta(seconds=LOCKOUT_SECONDS)).isoformat(timespec="seconds")

    conn = db_connect()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO auth_state (user_id, failed_attempts, locked_until)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET failed_attempts=?, locked_until=?
    """, (user_id, failed_attempts, locked_until, failed_attempts, locked_until))
    conn.commit()
    conn.close()
    return failed_attempts, locked_until


# =========================
# SDK Scanner Integration
# =========================
class IddkImage(ctypes.Structure):
    _fields_ = [
        ("imageKind", ctypes.c_ubyte),
        ("imageFormat", ctypes.c_ubyte),
        ("imageWidth", ctypes.c_int),
        ("imageHeight", ctypes.c_int),
        ("imageData", ctypes.POINTER(ctypes.c_ubyte)),
        ("imageDataLen", ctypes.c_int),
    ]


class IrisScannerSDK:
    IDDK_OK = 0
    IDDK_TIMEBASED = 0x01
    IDDK_QUALITY_NORMAL = 0x01
    IDDK_AUTO_CAPTURE = 0x01
    IDDK_UNKNOWN_EYE = 0x00
    IDDK_COMPLETE = 0x03
    IDDK_ABORT = 0x05
    IDDK_IKIND_K1 = 0x01
    IDDK_IFORMAT_MONO_RAW = 0x02

    def __init__(self, dll_path=IDDK_DLL_PATH):
        if not os.path.exists(dll_path):
            raise FileNotFoundError(f"SDK DLL not found: {dll_path}")

        self.dll_path = dll_path
        self.iddk = ctypes.CDLL(dll_path)
        self._configure_functions()

    def _configure_functions(self):
        self.iddk.Iddk_ScanDevices.argtypes = [
            ctypes.POINTER(ctypes.POINTER(ctypes.c_char_p)),
            ctypes.POINTER(ctypes.c_int),
        ]
        self.iddk.Iddk_ScanDevices.restype = ctypes.c_int

        self.iddk.Iddk_OpenDevice.argtypes = [
            ctypes.c_char_p,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        self.iddk.Iddk_OpenDevice.restype = ctypes.c_int

        self.iddk.Iddk_CloseDevice.argtypes = [ctypes.c_void_p]
        self.iddk.Iddk_CloseDevice.restype = ctypes.c_int

        self.iddk.Iddk_InitCamera.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int),
        ]
        self.iddk.Iddk_InitCamera.restype = ctypes.c_int

        self.iddk.Iddk_DeinitCamera.argtypes = [ctypes.c_void_p]
        self.iddk.Iddk_DeinitCamera.restype = ctypes.c_int

        self.iddk.Iddk_StartCapture.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_ubyte,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        self.iddk.Iddk_StartCapture.restype = ctypes.c_int

        self.iddk.Iddk_StopCapture.argtypes = [ctypes.c_void_p]
        self.iddk.Iddk_StopCapture.restype = ctypes.c_int

        self.iddk.Iddk_GetCaptureStatus.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_int),
        ]
        self.iddk.Iddk_GetCaptureStatus.restype = ctypes.c_int

        self.iddk.Iddk_GetResultImage.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_ubyte,
            ctypes.POINTER(ctypes.POINTER(IddkImage)),
            ctypes.POINTER(ctypes.c_int),
        ]
        self.iddk.Iddk_GetResultImage.restype = ctypes.c_int

    def capture_image(self):
        device_descs = ctypes.POINTER(ctypes.c_char_p)()
        device_count = ctypes.c_int()

        rc = self.iddk.Iddk_ScanDevices(ctypes.byref(device_descs), ctypes.byref(device_count))
        if rc != self.IDDK_OK:
            raise ValueError(f"Iddk_ScanDevices failed: {rc}")
        if device_count.value == 0:
            raise ValueError("No iris scanner found")

        h_device = ctypes.c_void_p()
        rc = self.iddk.Iddk_OpenDevice(device_descs[0], ctypes.byref(h_device))
        if rc != self.IDDK_OK:
            raise ValueError(f"Iddk_OpenDevice failed: {rc}")

        try:
            width = ctypes.c_int()
            height = ctypes.c_int()

            rc = self.iddk.Iddk_InitCamera(h_device, ctypes.byref(width), ctypes.byref(height))
            if rc != self.IDDK_OK:
                raise ValueError(f"Iddk_InitCamera failed: {rc}")

            # Based on vendor demo defaults shared in conversation.
            rc = self.iddk.Iddk_StartCapture(
                h_device,
                self.IDDK_TIMEBASED,
                3,
                self.IDDK_QUALITY_NORMAL,
                self.IDDK_AUTO_CAPTURE,
                self.IDDK_UNKNOWN_EYE,
                1,
                None,
                None
            )
            if rc != self.IDDK_OK:
                raise ValueError(f"Iddk_StartCapture failed: {rc}")

            status = ctypes.c_int()
            deadline = time.time() + 10.0

            while time.time() < deadline:
                rc = self.iddk.Iddk_GetCaptureStatus(h_device, ctypes.byref(status))
                if rc != self.IDDK_OK:
                    raise ValueError(f"Iddk_GetCaptureStatus failed: {rc}")

                if status.value == self.IDDK_COMPLETE:
                    break
                if status.value == self.IDDK_ABORT:
                    raise ValueError("Capture aborted by scanner")

                time.sleep(0.1)
            else:
                raise ValueError("Capture timed out")

            images = ctypes.POINTER(IddkImage)()
            max_eye = ctypes.c_int()

            rc = self.iddk.Iddk_GetResultImage(
                h_device,
                self.IDDK_IKIND_K1,
                self.IDDK_IFORMAT_MONO_RAW,
                1,
                ctypes.byref(images),
                ctypes.byref(max_eye),
            )
            if rc != self.IDDK_OK:
                raise ValueError(f"Iddk_GetResultImage failed: {rc}")
            if not images:
                raise ValueError("Scanner returned no image")

            img = images[0]
            if not img.imageData:
                raise ValueError("Returned image has null data pointer")

            size = img.imageWidth * img.imageHeight
            raw = ctypes.string_at(img.imageData, size)
            np_img = np.frombuffer(raw, dtype=np.uint8).reshape((img.imageHeight, img.imageWidth)).copy()

            return np_img

        finally:
            try:
                self.iddk.Iddk_StopCapture(h_device)
            except Exception:
                pass
            try:
                self.iddk.Iddk_DeinitCamera(h_device)
            except Exception:
                pass
            try:
                self.iddk.Iddk_CloseDevice(h_device)
            except Exception:
                pass


# =========================
# Iris Processing Pipeline
# =========================
def load_grayscale(image_path):
    img = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise ValueError(f"Could not read image: {image_path}")
    return img


def preprocess(img):
    img = cv2.medianBlur(img, 5)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    img = clahe.apply(img)
    return img


def detect_pupil(img):
    blur = cv2.GaussianBlur(img, (9, 9), 2)
    inv = 255 - blur

    circles = cv2.HoughCircles(
        inv,
        cv2.HOUGH_GRADIENT,
        dp=1.5,
        minDist=img.shape[0] // 4,
        param1=100,
        param2=20,
        minRadius=20,
        maxRadius=80,
    )

    if circles is None:
        raise ValueError("Pupil not detected")

    circles = np.round(circles[0]).astype(int)

    best = None
    best_score = float("inf")

    for x, y, r in circles:
        dist = np.hypot(x - img.shape[1] // 2, y - img.shape[0] // 2)
        if dist > img.shape[0] * 0.25:
            continue

        mask = np.zeros_like(img, dtype=np.uint8)
        cv2.circle(mask, (x, y), r, 255, -1)
        pixels = img[mask == 255]
        if len(pixels) == 0:
            continue

        score = np.mean(pixels)
        if score < best_score:
            best_score = score
            best = (x, y, r)

    if best is None:
        raise ValueError("Pupil not detected")
    return best


def detect_iris(img, pupil):
    px, py, pr = pupil
    h, w = img.shape

    blur = cv2.GaussianBlur(img, (5, 5), 0)
    edges = cv2.Canny(blur, 40, 120)

    min_r = max(int(pr * 1.8), pr + 18)
    max_r = min(int(pr * 3.4), int(min(h, w) * 0.42))

    if min_r >= max_r:
        fallback_r = max(pr + 20, int(pr * 2.3))
        fallback_r = min(fallback_r, int(min(h, w) * 0.40))
        return (px, py, fallback_r)

    angles1 = np.linspace(np.deg2rad(35), np.deg2rad(145), 90, endpoint=False)
    angles2 = np.linspace(np.deg2rad(215), np.deg2rad(325), 90, endpoint=False)
    angles = np.concatenate([angles1, angles2])

    best_r = max(pr + 20, int(pr * 2.3))
    best_score = -1.0

    for r in range(min_r, max_r + 1):
        score = 0.0
        valid = 0

        for t in angles:
            x = int(round(px + r * np.cos(t)))
            y = int(round(py + r * np.sin(t)))
            if 1 <= x < w - 1 and 1 <= y < h - 1:
                valid += 1
                if edges[y, x] > 0:
                    score += 1.0

        if valid == 0:
            continue

        score /= valid
        ratio = r / max(pr, 1)
        if 2.0 <= ratio <= 3.2:
            score += 0.03

        if score > best_score:
            best_score = score
            best_r = r

    return (px, py, best_r)


def normalize_iris(img, pupil, iris, radial_res=64, angular_res=256):
    px, py, pr = pupil
    ix, iy, ir = iris

    angles1 = np.linspace(np.deg2rad(40), np.deg2rad(140), angular_res // 2, endpoint=False)
    angles2 = np.linspace(np.deg2rad(220), np.deg2rad(320), angular_res // 2, endpoint=False)
    theta = np.concatenate([angles1, angles2])

    r = np.linspace(0, 1, radial_res)
    strip = np.zeros((radial_res, len(theta)), dtype=np.uint8)

    for j, t in enumerate(theta):
        x_p = px + pr * np.cos(t)
        y_p = py + pr * np.sin(t)

        x_i = ix + ir * np.cos(t)
        y_i = iy + ir * np.sin(t)

        xs = (1.0 - r) * x_p + r * x_i
        ys = (1.0 - r) * y_p + r * y_i

        xs = np.clip(xs, 0, img.shape[1] - 1)
        ys = np.clip(ys, 0, img.shape[0] - 1)

        strip[:, j] = img[ys.astype(int), xs.astype(int)]

    inner_cut = int(0.10 * radial_res)
    outer_cut = int(0.12 * radial_res)
    strip[:inner_cut, :] = 0
    strip[-outer_cut:, :] = 0

    return strip


def strip_quality(strip):
    nonzero = np.count_nonzero(strip)
    total = strip.size
    nz_ratio = nonzero / total
    contrast = float(np.std(strip))
    mean_val = float(np.mean(strip))
    return nz_ratio, contrast, mean_val


def build_gabor_kernels():
    kernels = []
    params = [
        (9.0, 8.0, 0.0),
        (9.0, 16.0, 0.0),
        (12.0, 8.0, np.pi / 2),
        (12.0, 16.0, np.pi / 2),
    ]

    for sigma, lambd, psi in params:
        k = cv2.getGaborKernel(
            ksize=(21, 21),
            sigma=sigma,
            theta=0,
            lambd=lambd,
            gamma=0.5,
            psi=psi,
            ktype=cv2.CV_32F,
        )
        kernels.append(k)

    return kernels


GABOR_KERNELS = build_gabor_kernels()


def extract_template(normalized_strip):
    img = normalized_strip.astype(np.float32) / 255.0
    template_maps = []

    for kernel in GABOR_KERNELS:
        resp = cv2.filter2D(img, cv2.CV_32F, kernel)
        bits = (resp > 0).astype(np.uint8)
        template_maps.append(bits)

    return np.stack(template_maps, axis=0)


def hamming_distance(t1, t2):
    return np.mean(t1 != t2)


def match_templates(query, enrolled, max_shift=16):
    best = 1.0
    for shift in range(-max_shift, max_shift + 1):
        shifted = np.roll(query, shift=shift, axis=2)
        d = hamming_distance(shifted, enrolled)
        if d < best:
            best = d
    return best


def image_to_template(img_or_path):
    if isinstance(img_or_path, (str, Path)):
        img = load_grayscale(img_or_path)
    else:
        img = img_or_path.copy()

    img = preprocess(img)
    pupil = detect_pupil(img)
    iris = detect_iris(img, pupil)
    strip = normalize_iris(img, pupil, iris)

    nz_ratio, contrast, mean_val = strip_quality(strip)
    if nz_ratio < MIN_STRIP_NONZERO_RATIO or contrast < MIN_STRIP_CONTRAST:
        raise ValueError(
            f"Poor iris normalization quality (nz_ratio={nz_ratio:.2f}, contrast={contrast:.2f})"
        )

    template = extract_template(strip)

    debug = {
        "preprocessed": img,
        "pupil": pupil,
        "iris": iris,
        "strip": strip,
        "strip_nonzero_ratio": nz_ratio,
        "strip_contrast": contrast,
        "strip_mean": mean_val,
    }

    return template, debug


# =========================
# Enrollment / Verification / Identification
# =========================
def enroll_user_images(user_id, eye_side, image_sources):
    create_user_if_missing(user_id)

    extracted = []
    failed = []

    for src in image_sources:
        try:
            template, debug = image_to_template(src)
            quality = {
                "strip_nonzero_ratio": debug["strip_nonzero_ratio"],
                "strip_contrast": debug["strip_contrast"],
                "strip_mean": debug["strip_mean"],
            }
            extracted.append((template, quality))
        except Exception as e:
            name = src if isinstance(src, str) else "<captured_frame>"
            failed.append(f"{name} -> {e}")

    if len(extracted) == 0:
        return 0, failed, "No valid samples were extracted."

    if len(extracted) >= 2:
        dists = []
        for i in range(len(extracted)):
            for j in range(i + 1, len(extracted)):
                d = match_templates(extracted[i][0], extracted[j][0])
                dists.append(d)

        if dists and max(dists) > MAX_ENROLL_INCONSISTENCY:
            return 0, failed, (
                f"Enrollment samples are inconsistent "
                f"(max intra-user distance={max(dists):.4f} > {MAX_ENROLL_INCONSISTENCY:.4f})."
            )

    for template, quality in extracted:
        insert_template(user_id, eye_side, template, quality)

    log_event(
        event_type="enrollment",
        result="success" if len(extracted) > 0 else "failed",
        user_id=user_id,
        eye_side=eye_side,
        details={"enrolled_samples": len(extracted), "failed_samples": len(failed)},
    )

    warning = None
    if len(extracted) < MIN_ENROLL_SAMPLES:
        warning = (
            f"Only {len(extracted)} valid sample(s) enrolled. "
            f"Recommended minimum is {MIN_ENROLL_SAMPLES}."
        )

    return len(extracted), failed, warning


def identify_user(image_or_path, eye_side):
    all_templates = load_templates(eye_side=eye_side)
    if not all_templates:
        raise ValueError("No enrolled templates found for this eye")

    query, debug = image_to_template(image_or_path)

    best_user = None
    best_score = 1.0

    for row in all_templates:
        score = match_templates(query, row["template"])
        if score < best_score:
            best_score = score
            best_user = row["user_id"]

    if best_score > IDENTIFY_REJECT_THRESHOLD:
        log_event(
            event_type="identify",
            result="reject",
            user_id=None,
            eye_side=eye_side,
            match_score=best_score,
            details={"reason": "threshold"},
        )
        return None, best_score, debug

    log_event(
        event_type="identify",
        result="accept",
        user_id=best_user,
        eye_side=eye_side,
        match_score=best_score,
    )
    return best_user, best_score, debug


def verify_user(claimed_user, image_or_path, eye_side):
    if not user_exists(claimed_user):
        raise ValueError(f"User '{claimed_user}' is not enrolled")

    locked, remaining = is_user_locked(claimed_user)
    if locked:
        raise ValueError(f"User '{claimed_user}' is locked for {remaining} more seconds")

    claimed_templates = load_templates(user_id=claimed_user, eye_side=eye_side)
    if not claimed_templates:
        raise ValueError(f"No {eye_side} eye enrolled for user '{claimed_user}'")

    query, debug = image_to_template(image_or_path)

    claimed_score = min(match_templates(query, row["template"]) for row in claimed_templates)

    all_other = [
        row for row in load_templates(eye_side=eye_side)
        if row["user_id"] != claimed_user
    ]

    best_other_user = None
    best_other_score = 1.0

    for row in all_other:
        score = match_templates(query, row["template"])
        if score < best_other_score:
            best_other_score = score
            best_other_user = row["user_id"]

    margin = best_other_score - claimed_score if all_other else 1.0
    accepted = (claimed_score <= VERIFY_ACCEPT_THRESHOLD) and (margin >= VERIFY_MARGIN_THRESHOLD)

    debug["claimed_score"] = claimed_score
    debug["best_other_score"] = best_other_score
    debug["best_other_user"] = best_other_user
    debug["verification_margin"] = margin

    if accepted:
        reset_failures(claimed_user)
        log_event(
            event_type="verify",
            result="accept",
            user_id=claimed_user,
            eye_side=eye_side,
            match_score=claimed_score,
            details={"margin": margin, "best_other_user": best_other_user},
        )
    else:
        failed_attempts, locked_until = record_failed_attempt(claimed_user)
        log_event(
            event_type="verify",
            result="reject",
            user_id=claimed_user,
            eye_side=eye_side,
            match_score=claimed_score,
            details={
                "margin": margin,
                "best_other_user": best_other_user,
                "failed_attempts": failed_attempts,
                "locked_until": locked_until,
            },
        )

    return accepted, claimed_score, debug


def suggest_thresholds():
    rows = load_templates()
    genuine_scores = []
    impostor_scores = []

    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            if rows[i]["eye_side"] != rows[j]["eye_side"]:
                continue

            score = match_templates(rows[i]["template"], rows[j]["template"])
            if rows[i]["user_id"] == rows[j]["user_id"]:
                genuine_scores.append(score)
            else:
                impostor_scores.append(score)

    if not genuine_scores:
        raise ValueError("Not enough genuine comparisons. Enroll more samples per user.")
    if not impostor_scores:
        raise ValueError("Not enough impostor comparisons. Enroll more users.")

    genuine_scores = np.array(genuine_scores)
    impostor_scores = np.array(impostor_scores)

    worst_genuine = np.max(genuine_scores)
    best_impostor = np.min(impostor_scores)
    midpoint = (worst_genuine + best_impostor) / 2.0

    return {
        "genuine_min": float(np.min(genuine_scores)),
        "genuine_max": float(np.max(genuine_scores)),
        "genuine_mean": float(np.mean(genuine_scores)),
        "genuine_95": float(np.percentile(genuine_scores, 95)),
        "impostor_min": float(np.min(impostor_scores)),
        "impostor_max": float(np.max(impostor_scores)),
        "impostor_mean": float(np.mean(impostor_scores)),
        "verification_threshold_midpoint": float(midpoint),
        "verification_threshold_safe": float(np.percentile(genuine_scores, 95)),
        "identification_threshold": float(min(midpoint + 0.01, 1.0)),
    }


# =========================
# IITD dataset utilities
# =========================
def collect_iitd_images(dataset_root):
    dataset_root = Path(dataset_root)
    if not dataset_root.exists():
        raise ValueError("Dataset path does not exist")
    if not dataset_root.is_dir():
        raise ValueError("Dataset path is not a folder")

    data = defaultdict(lambda: {"left": [], "right": []})

    for user_dir in sorted(dataset_root.iterdir()):
        if not user_dir.is_dir():
            continue

        user_id = user_dir.name
        for file_path in sorted(user_dir.iterdir()):
            if not file_path.is_file() or not is_image_file(file_path):
                continue

            name = file_path.name.upper()
            if "_L" in name:
                data[user_id]["left"].append(str(file_path))
            elif "_R" in name:
                data[user_id]["right"].append(str(file_path))

    return dict(data)


def split_iitd_train_test(data, train_ratio=0.7, seed=42):
    random.seed(seed)
    train_data = defaultdict(lambda: {"left": [], "right": []})
    test_data = defaultdict(lambda: {"left": [], "right": []})

    for user_id, eyes in data.items():
        for eye_side in ["left", "right"]:
            imgs = eyes.get(eye_side, []).copy()
            if not imgs:
                continue

            random.shuffle(imgs)

            if len(imgs) == 1:
                train_imgs = imgs
                test_imgs = []
            else:
                split_idx = max(1, int(len(imgs) * train_ratio))
                split_idx = min(split_idx, len(imgs) - 1)
                train_imgs = imgs[:split_idx]
                test_imgs = imgs[split_idx:]

            train_data[user_id][eye_side] = train_imgs
            test_data[user_id][eye_side] = test_imgs

    return dict(train_data), dict(test_data)


def bulk_enroll_dataset(dataset_dict):
    total_users = 0
    total_left = 0
    total_right = 0
    failed_items = []

    for user_id, eyes in sorted(dataset_dict.items()):
        user_added = False
        for eye_side in ["left", "right"]:
            paths = eyes.get(eye_side, [])
            if not paths:
                continue

            success, failed, _ = enroll_user_images(user_id, eye_side, paths)
            if eye_side == "left":
                total_left += success
            else:
                total_right += success

            if success > 0:
                user_added = True
            failed_items.extend([f"{user_id} {eye_side.upper()}: {x}" for x in failed])

        if user_added:
            total_users += 1

    return {
        "total_users": total_users,
        "total_left": total_left,
        "total_right": total_right,
        "failed_items": failed_items,
    }


def evaluate_on_test_set(test_data):
    identification_total = 0
    identification_correct = 0
    identification_rejected = 0

    verification_total = 0
    verification_correct = 0

    identification_failed = []
    verification_failed = []

    for user_id, eyes in sorted(test_data.items()):
        for eye_side in ["left", "right"]:
            for img_path in eyes.get(eye_side, []):
                try:
                    pred_user, _, _ = identify_user(img_path, eye_side)
                    identification_total += 1
                    if pred_user is None:
                        identification_rejected += 1
                    elif pred_user == user_id:
                        identification_correct += 1
                except Exception as e:
                    identification_failed.append(f"{os.path.basename(img_path)} -> {e}")

                try:
                    accepted, _, _ = verify_user(user_id, img_path, eye_side)
                    verification_total += 1
                    if accepted:
                        verification_correct += 1
                except Exception as e:
                    verification_failed.append(f"{os.path.basename(img_path)} -> {e}")

    identification_accuracy = identification_correct / identification_total if identification_total else 0.0
    verification_accuracy = verification_correct / verification_total if verification_total else 0.0

    return {
        "identification_total": identification_total,
        "identification_correct": identification_correct,
        "identification_rejected": identification_rejected,
        "identification_accuracy": identification_accuracy,
        "verification_total": verification_total,
        "verification_correct": verification_correct,
        "verification_accuracy": verification_accuracy,
        "identification_failed": identification_failed,
        "verification_failed": verification_failed,
    }


def reset_database():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    ensure_db()


# =========================
# GUI
# =========================
class IrisSecurityApp:
    def __init__(self, root):
        ensure_db()

        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1180x790")
        self.root.configure(bg="#f4f6f8")

        self.selected_image_path = None
        self.captured_image = None
        self.last_debug = None
        self.preview_photo = None
        self.strip_photo = None
        self.current_users = []

        self.build_ui()
        self.refresh_users()

    def build_ui(self):
        title = Label(
            self.root,
            text=APP_TITLE,
            font=("Arial", 22, "bold"),
            bg="#f4f6f8"
        )
        title.pack(pady=15)

        main = Frame(self.root, bg="#f4f6f8")
        main.pack(fill=BOTH, expand=True, padx=15, pady=10)

        left_panel = Frame(main, bg="white", bd=1, relief=SOLID)
        left_panel.pack(side=LEFT, fill=Y, padx=(0, 10))

        right_panel = Frame(main, bg="white", bd=1, relief=SOLID)
        right_panel.pack(side=RIGHT, fill=BOTH, expand=True)

        Label(left_panel, text="Controls", font=("Arial", 16, "bold"), bg="white").pack(pady=10)

        self.eye_var = StringVar(value="left")
        eye_frame = Frame(left_panel, bg="white")
        eye_frame.pack(pady=5)

        Label(eye_frame, text="Eye:", font=("Arial", 12), bg="white").pack(side=LEFT, padx=5)

        ttk.Combobox(
            eye_frame,
            textvariable=self.eye_var,
            values=["left", "right"],
            state="readonly",
            width=10
        ).pack(side=LEFT, padx=5)

        Button(
            left_panel,
            text="Scan from Iris Scanner",
            command=self.scan_from_scanner,
            width=24,
            height=2
        ).pack(pady=10)

        Label(left_panel, text="Enrollment", font=("Arial", 14, "bold"), bg="white").pack(pady=(20, 8))

        Button(
            left_panel,
            text="Enroll New User (3-5 Samples)",
            command=self.enroll_new_user,
            width=24,
            height=2
        ).pack(pady=6)

        Button(
            left_panel,
            text="Add More Samples to Existing User",
            command=self.add_to_existing_user,
            width=24,
            height=2
        ).pack(pady=6)

        Label(left_panel, text="Authentication", font=("Arial", 14, "bold"), bg="white").pack(pady=(20, 8))

        Button(
            left_panel,
            text="Verify Claimed User (1:1)",
            command=self.verify_current,
            width=24,
            height=2
        ).pack(pady=6)

        Button(
            left_panel,
            text="Identify User (1:N)",
            command=self.identify_current,
            width=24,
            height=2
        ).pack(pady=6)

        Label(left_panel, text="Database Users", font=("Arial", 14, "bold"), bg="white").pack(pady=(20, 8))

        self.user_listbox = Listbox(left_panel, width=28, height=12)
        self.user_listbox.pack(padx=10, pady=5)

        Button(
            left_panel,
            text="Refresh Users",
            command=self.refresh_users,
            width=24,
            height=2
        ).pack(pady=10)

        top_right = Frame(right_panel, bg="white")
        top_right.pack(fill=BOTH, expand=True, padx=10, pady=10)

        preview_frame = LabelFrame(
            top_right,
            text="Captured Iris",
            bg="white",
            font=("Arial", 12, "bold")
        )
        preview_frame.pack(side=LEFT, fill=BOTH, expand=True, padx=5, pady=5)

        self.preview_label = Label(preview_frame, text="No scan captured yet", bg="white")
        self.preview_label.pack(fill=BOTH, expand=True, padx=10, pady=10)

        strip_frame = LabelFrame(
            top_right,
            text="Normalized Iris Strip",
            bg="white",
            font=("Arial", 12, "bold")
        )
        strip_frame.pack(side=RIGHT, fill=BOTH, expand=True, padx=5, pady=5)

        self.strip_label = Label(strip_frame, text="No processing yet", bg="white")
        self.strip_label.pack(fill=BOTH, expand=True, padx=10, pady=10)

        result_frame = LabelFrame(
            right_panel,
            text="Result",
            bg="white",
            font=("Arial", 12, "bold")
        )
        result_frame.pack(fill=BOTH, expand=False, padx=10, pady=(0, 10))

        self.result_text = Text(result_frame, height=14, font=("Consolas", 11))
        self.result_text.pack(fill=BOTH, expand=True, padx=10, pady=10)

        self.status_var = StringVar(value="Ready.")
        status = Label(
            self.root,
            textvariable=self.status_var,
            anchor="w",
            bg="#dfe6eb",
            font=("Arial", 10)
        )
        status.pack(side=BOTTOM, fill=X)

    def set_status(self, text):
        self.status_var.set(text)
        self.root.update_idletasks()

    def write_result(self, text):
        self.result_text.delete("1.0", END)
        self.result_text.insert(END, text)

    def refresh_users(self, select_user=None):
        self.current_users = get_users()
        self.user_listbox.delete(0, END)

        selected_index = None
        for i, u in enumerate(self.current_users):
            self.user_listbox.insert(END, u)
            if select_user is not None and u == select_user:
                selected_index = i

        if selected_index is not None:
            self.user_listbox.selection_clear(0, END)
            self.user_listbox.selection_set(selected_index)
            self.user_listbox.activate(selected_index)
            self.user_listbox.see(selected_index)

    def ask_user_selection(self, title, prompt, initial_user=None):
        self.refresh_users(select_user=initial_user)
        users = list(getattr(self, "current_users", []))

        if not users:
            messagebox.showerror("Error", "No users enrolled yet.")
            return None

        current_sel = self.user_listbox.curselection()
        if current_sel:
            selected = self.user_listbox.get(current_sel[0])
            if messagebox.askyesno(title, f"Use selected user '{selected}'?"):
                return selected

        dialog = Toplevel(self.root)
        dialog.title(title)
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)

        Label(dialog, text=prompt, font=("Arial", 11)).pack(padx=12, pady=(12, 8))

        lb = Listbox(dialog, width=35, height=min(max(len(users), 6), 12))
        lb.pack(padx=12, pady=6, fill=BOTH, expand=True)

        selected_index = 0
        for i, user in enumerate(users):
            lb.insert(END, user)
            if initial_user is not None and user == initial_user:
                selected_index = i

        if users:
            lb.selection_set(selected_index)
            lb.activate(selected_index)
            lb.see(selected_index)

        result = {"user": None}

        def confirm(event=None):
            sel = lb.curselection()
            if not sel:
                messagebox.showwarning(title, "Please select a user.")
                return
            result["user"] = lb.get(sel[0])
            dialog.destroy()

        def cancel(event=None):
            dialog.destroy()

        btn_row = Frame(dialog)
        btn_row.pack(pady=(4, 12))
        Button(btn_row, text="OK", width=10, command=confirm).pack(side=LEFT, padx=6)
        Button(btn_row, text="Cancel", width=10, command=cancel).pack(side=LEFT, padx=6)

        lb.bind("<Double-1>", confirm)
        dialog.bind("<Return>", confirm)
        dialog.bind("<Escape>", cancel)

        self.root.wait_window(dialog)
        return result["user"]

    def choose_image_sources(self, user_id, eye_side, allow_current=True):
        if allow_current and (self.captured_image is not None or self.selected_image_path is not None):
            use_current = messagebox.askyesnocancel(
                "Enrollment Source",
                f"Add data for user '{user_id}' ({eye_side} eye).\n\n"
                "Yes = use the currently loaded/scanned image\n"
                "No = choose one or more image files\n"
                "Cancel = stop"
            )
            if use_current is None:
                return None
            if use_current:
                return [self.get_current_image_source()]

        image_paths = filedialog.askopenfilenames(
            title=f"Select {eye_side} iris images for {user_id}",
            filetypes=[("Image files", "*.png *.jpg *.jpeg *.bmp *.tif *.tiff")]
        )
        if not image_paths:
            return None
        return list(image_paths)

    def ask_enrollment_target(self):
        target = simpledialog.askinteger(
            "Enrollment Samples",
            f"How many samples do you want to enroll?\n\nChoose between {MIN_ENROLL_SAMPLES} and 5.",
            initialvalue=MIN_ENROLL_SAMPLES,
            minvalue=MIN_ENROLL_SAMPLES,
            maxvalue=5,
            parent=self.root,
        )
        return target

    def collect_multi_sample_sources(self, user_id, eye_side, target_count, min_required=MIN_ENROLL_SAMPLES, allow_current=True):
        collected = []

        while len(collected) < target_count:
            remaining = target_count - len(collected)
            title = f"Enrollment Sample {len(collected) + 1} of {target_count}"
            dialog = Toplevel(self.root)
            dialog.title(title)
            dialog.transient(self.root)
            dialog.grab_set()
            dialog.resizable(False, False)

            Label(
                dialog,
                text=(
                    f"User: {user_id}\n"
                    f"Target eye to enroll: {eye_side}\n\n"
                    f"Collected so far: {len(collected)}\n"
                    f"Remaining to target: {remaining}\n\n"
                    f"Please provide a sample for the '{eye_side}' eye."
                ),
                justify=LEFT,
                font=("Arial", 11),
            ).pack(padx=14, pady=(14, 10))

            choice = {"action": None}

            def set_action(action):
                choice["action"] = action
                dialog.destroy()

            if allow_current and (self.captured_image is not None or self.selected_image_path is not None):
                Button(
                    dialog,
                    text=f"Use Current Loaded/Scanned {eye_side.capitalize()} Image",
                    width=36,
                    command=lambda: set_action("current")
                ).pack(padx=14, pady=4)

            Button(
                dialog,
                text=f"Scan New {eye_side.capitalize()} Eye Image",
                width=36,
                command=lambda: set_action("scan")
            ).pack(padx=14, pady=4)

            Button(
                dialog,
                text=f"Choose {eye_side.capitalize()} Eye Image File(s)",
                width=36,
                command=lambda: set_action("files")
            ).pack(padx=14, pady=4)

            finish_text = "Finish Enrollment" if len(collected) >= min_required else f"Finish After At Least {min_required} Samples"
            Button(dialog, text=finish_text, width=36, command=lambda: set_action("finish")).pack(padx=14, pady=(10, 4))
            Button(dialog, text="Cancel", width=36, command=lambda: set_action("cancel")).pack(padx=14, pady=(0, 14))

            dialog.bind("<Escape>", lambda event: set_action("cancel"))
            self.root.wait_window(dialog)
            action = choice["action"]

            if action in (None, "cancel"):
                return None

            if action == "finish":
                if len(collected) >= min_required:
                    break
                messagebox.showwarning("Enrollment", f"Please collect at least {min_required} sample(s) before finishing.")
                continue

            if action == "current":
                try:
                    collected.append(self.get_current_image_source())
                    self.write_result(
                        f"Enrollment in progress for '{user_id}' [{eye_side}]\n"
                        f"Collected sample {len(collected)} of {target_count} using the current image."
                    )
                except Exception as e:
                    messagebox.showerror("Enrollment", str(e))
                continue

            if action == "scan":
                try:
                    self.captured_image = None
                    self.selected_image_path = None
                    messagebox.showinfo("Scan Required", f"Please scan the '{eye_side}' eye now.")
                    self.scan_from_scanner()
                    collected.append(self.get_current_image_source())
                    self.write_result(
                        f"Enrollment in progress for '{user_id}' [{eye_side}]\n"
                        f"Collected sample {len(collected)} of {target_count} from the scanner."
                    )
                except Exception as e:
                    messagebox.showerror("Scanner Error", str(e))
                continue

            if action == "files":
                image_paths = filedialog.askopenfilenames(
                    title=f"Select {eye_side} iris images for {user_id}",
                    filetypes=[("Image files", "*.png *.jpg *.jpeg *.bmp *.tif *.tiff")],
                )
                if not image_paths:
                    continue

                slots = target_count - len(collected)
                chosen = list(image_paths)[:slots]
                collected.extend(chosen)
                self.write_result(
                    f"Enrollment in progress for '{user_id}' [{eye_side}]\n"
                    f"Added {len(chosen)} file sample(s). Total collected: {len(collected)} of {target_count}."
                )
                if len(image_paths) > slots:
                    messagebox.showinfo(
                        "Enrollment",
                        f"Only the first {slots} selected file(s) were used so the total stays within {target_count} samples.",
                    )
                continue

        return collected[:target_count]

    def get_enrolled_eyes_for_user(self, user_id):
        conn = db_connect()
        cur = conn.cursor()
        cur.execute("""
            SELECT DISTINCT eye_side
            FROM templates
            WHERE user_id=?
            ORDER BY eye_side
        """, (user_id,))
        rows = [r[0] for r in cur.fetchall()]
        conn.close()
        return rows

    def choose_verification_eye_for_user(self, user_id, target_eye):
        enrolled_eyes = self.get_enrolled_eyes_for_user(user_id)

        if not enrolled_eyes:
            messagebox.showerror(
                "No Enrolled Templates",
                f"User '{user_id}' has no enrolled iris templates yet."
            )
            return None

        if target_eye in enrolled_eyes:
            return target_eye

        if len(enrolled_eyes) == 1:
            verify_eye = enrolled_eyes[0]
            messagebox.showinfo(
                "Using Existing Eye for Verification",
                f"User '{user_id}' does not yet have any '{target_eye}' eye templates.\n\n"
                f"The system will first verify the user using the already enrolled '{verify_eye}' eye,\n"
                f"then allow enrollment of the new '{target_eye}' eye."
            )
            return verify_eye

        verify_eye = simpledialog.askstring(
            "Verification Eye",
            f"Which enrolled eye should be used to verify user '{user_id}' first?\n"
            f"Available enrolled eyes: {', '.join(enrolled_eyes)}"
        )
        if not verify_eye:
            return None

        verify_eye = verify_eye.strip().lower()
        if verify_eye not in enrolled_eyes:
            messagebox.showerror(
                "Invalid Eye",
                f"'{verify_eye}' is not an enrolled eye for user '{user_id}'."
            )
            return None

        return verify_eye

    def verify_existing_user_for_sample_addition(self, user_id, verify_eye):
        if not user_id:
            return False

        messagebox.showinfo(
            "Verification Required",
            f"To continue, please verify user '{user_id}' using the '{verify_eye}' eye."
        )

        self.captured_image = None
        self.selected_image_path = None
        self.scan_from_scanner()

        src = self.get_current_image_source()
        accepted, score, debug = verify_user(user_id, src, verify_eye)

        self.last_debug = debug
        self.show_debug_outputs(src, debug)

        nz_ratio = debug.get("strip_nonzero_ratio", 0.0)
        contrast = debug.get("strip_contrast", 0.0)
        mean_val = debug.get("strip_mean", 0.0)
        nearest_other = debug.get("best_other_user", "N/A")
        nearest_other_score = debug.get("best_other_score", 1.0)
        margin = debug.get("verification_margin", 0.0)

        result = (
            f"PRE-ENROLLMENT VERIFICATION RESULT\n"
            f"---------------------------------\n"
            f"Claimed user: {user_id}\n"
            f"Verification eye: {verify_eye}\n"
            f"Accepted: {accepted}\n"
            f"Claimed-user score: {score:.4f}\n"
            f"Nearest other user: {nearest_other}\n"
            f"Nearest other score: {nearest_other_score:.4f}\n"
            f"Margin vs nearest other: {margin:.4f}\n"
            f"Threshold: {VERIFY_ACCEPT_THRESHOLD:.4f}\n"
            f"Strip nonzero ratio: {nz_ratio:.2f}\n"
            f"Strip contrast: {contrast:.2f}\n"
            f"Strip mean intensity: {mean_val:.2f}\n"
        )
        self.write_result(result)

        if not accepted:
            messagebox.showerror(
                "Verification Failed",
                f"User '{user_id}' could not be verified using the '{verify_eye}' eye.\n\n"
                f"More samples cannot be added."
            )
            self.set_status("Verification failed. Sample addition blocked.")
            return False

        messagebox.showinfo(
            "Verification Passed",
            f"User '{user_id}' has been verified successfully using the '{verify_eye}' eye."
        )
        self.set_status("Verification passed.")
        return True

    def get_current_image_source(self):
        if self.captured_image is not None:
            return self.captured_image
        if self.selected_image_path is not None:
            return self.selected_image_path
        raise ValueError("Load or scan an iris image first.")

    def load_image_file(self):
        path = filedialog.askopenfilename(
            title="Select iris image",
            filetypes=[("Image files", "*.png *.jpg *.jpeg *.bmp *.tif *.tiff")]
        )
        if not path:
            return

        self.selected_image_path = path
        self.captured_image = None
        self.show_preview_image(path)
        self.strip_label.config(image="", text="No processing yet")
        self.write_result(f"Loaded image:\n{path}")
        self.set_status("Image loaded.")

    def scan_from_scanner(self):
        try:
            self.set_status("Capturing from scanner...")
            scanner = IrisScannerSDK()
            img = scanner.capture_image()
            self.captured_image = img
            self.selected_image_path = None
            self.show_preview_np_image(img)
            self.strip_label.config(image="", text="No processing yet")
            self.write_result(f"Captured from scanner.\nShape: {img.shape}")
            self.set_status("Scanner capture complete.")
        except Exception as e:
            messagebox.showerror("Scanner Error", str(e))
            self.set_status("Scanner capture failed.")

    def show_preview_image(self, path):
        img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if img is None:
            return
        self.show_preview_np_image(img)

    def show_preview_np_image(self, img):
        disp = cv2.resize(img, (360, 360))
        pil = Image.fromarray(disp)
        self.preview_photo = ImageTk.PhotoImage(pil)
        self.preview_label.config(image=self.preview_photo, text="")

    def show_debug_outputs(self, base_img, debug):
        try:
            if isinstance(base_img, str):
                base = cv2.imread(base_img, cv2.IMREAD_GRAYSCALE)
            else:
                base = base_img.copy()

            vis = cv2.cvtColor(base, cv2.COLOR_GRAY2BGR)
            p = debug["pupil"]
            i = debug["iris"]

            cv2.circle(vis, (p[0], p[1]), p[2], (0, 255, 0), 2)
            cv2.circle(vis, (i[0], i[1]), i[2], (255, 0, 0), 2)

            vis = cv2.resize(vis, (360, 360))
            vis = cv2.cvtColor(vis, cv2.COLOR_BGR2RGB)
            self.preview_photo = ImageTk.PhotoImage(Image.fromarray(vis))
            self.preview_label.config(image=self.preview_photo, text="")

            strip = debug["strip"]
            strip_disp = cv2.resize(strip, (360, 190))
            self.strip_photo = ImageTk.PhotoImage(Image.fromarray(strip_disp))
            self.strip_label.config(image=self.strip_photo, text="")
        except Exception:
            pass

    def enroll_new_user(self):
        user_id = simpledialog.askstring("New User", "Enter new user ID:")
        if not user_id:
            return
        user_id = user_id.strip()

        if not user_id:
            messagebox.showerror("Enrollment Error", "User ID cannot be empty.")
            return

        target_count = self.ask_enrollment_target()
        if not target_count:
            return

        eye_side = self.eye_var.get()
        image_sources = self.collect_multi_sample_sources(user_id, eye_side, target_count, min_required=MIN_ENROLL_SAMPLES, allow_current=True)
        if not image_sources:
            return

        persisted_sources, saved_files, persist_failed = persist_image_sources_for_user(image_sources, user_id, eye_side)
        if not persisted_sources:
            msg = "No image files could be saved for enrollment."
            if persist_failed:
                msg += "\n\n" + "\n".join(persist_failed[:10])
            messagebox.showerror("Enrollment Error", msg)
            return

        self.set_status("Enrolling new user...")
        success, failed, warning = enroll_user_images(user_id, eye_side, persisted_sources)
        failed = persist_failed + failed

        self.refresh_users(select_user=user_id)

        total_templates = count_user_templates(user_id, eye_side)
        msg = (
            f"Enrolled {success} sample(s) for user '{user_id}' [{eye_side}].\n"
            f"Requested samples: {target_count}\n"
            f"Saved image files: {len(saved_files)}\n"
            f"Total stored templates for this user/eye: {total_templates}"
        )
        if success < MIN_ENROLL_SAMPLES:
            msg += f"\n\nEnrollment is not yet strong enough. Please add more samples until you have at least {MIN_ENROLL_SAMPLES} valid templates."
        if warning:
            msg += f"\n\nWarning:\n{warning}"
        if failed:
            msg += "\n\nFailed files:\n" + "\n".join(failed[:10])

        messagebox.showinfo("Enrollment", msg)
        self.write_result(msg)
        self.set_status("Enrollment finished.")

    def add_to_existing_user(self):
        user_id = self.ask_user_selection(
            title="Select Existing User",
            prompt="Choose the user that should receive the new iris samples."
        )
        if not user_id:
            return

        target_eye = self.eye_var.get().strip().lower()

        verify_eye = self.choose_verification_eye_for_user(user_id, target_eye)
        if not verify_eye:
            return

        try:
            self.set_status("Verifying claimed user before sample addition...")
            verified = self.verify_existing_user_for_sample_addition(user_id, verify_eye)
            if not verified:
                return
        except Exception as e:
            messagebox.showerror("Verification Error", str(e))
            self.set_status("Verification failed. Sample addition blocked.")
            return

        target_count = self.ask_enrollment_target()
        if not target_count:
            return

        if target_eye == verify_eye:
            enroll_msg = (
                f"User '{user_id}' was verified using the '{verify_eye}' eye.\n\n"
                f"You are now adding more samples to the existing '{target_eye}' eye."
            )
        else:
            enroll_msg = (
                f"User '{user_id}' was verified using the '{verify_eye}' eye.\n\n"
                f"You are now enrolling the '{target_eye}' eye for this user.\n"
                f"From this point onward, scan or choose ONLY '{target_eye}' eye images."
            )

        messagebox.showinfo("Add Samples", enroll_msg)

        self.captured_image = None
        self.selected_image_path = None

        image_sources = self.collect_multi_sample_sources(
            user_id,
            target_eye,
            target_count,
            min_required=MIN_ENROLL_SAMPLES,
            allow_current=False
        )
        if not image_sources:
            return

        persisted_sources, saved_files, persist_failed = persist_image_sources_for_user(
            image_sources, user_id, target_eye
        )
        if not persisted_sources:
            msg = "No image files could be saved for this user."
            if persist_failed:
                msg += "\n\n" + "\n".join(persist_failed[:10])
            messagebox.showerror("Add Images Error", msg)
            return

        self.set_status("Adding verified samples to existing user...")
        success, failed, warning = enroll_user_images(user_id, target_eye, persisted_sources)
        failed = persist_failed + failed

        self.refresh_users(select_user=user_id)

        total_templates = count_user_templates(user_id, target_eye)
        msg = (
            f"Added {success} sample(s) to user '{user_id}' [{target_eye}].\n"
            f"Requested samples: {target_count}\n"
            f"Saved image files: {len(saved_files)}\n"
            f"Total stored templates for this user/eye: {total_templates}"
        )

        if success < MIN_ENROLL_SAMPLES:
            msg += (
                f"\n\nOnly {success} valid new sample(s) were added. "
                f"Please try again until at least {MIN_ENROLL_SAMPLES} strong new samples are added."
            )

        if warning:
            msg += f"\n\nWarning:\n{warning}"

        if failed:
            msg += "\n\nFailed files:\n" + "\n".join(failed[:10])

        messagebox.showinfo("Add Images", msg)
        self.write_result(msg)
        self.set_status("User update finished.")

    def delete_user_gui(self):
        user_id = self.ask_user_selection(
            title="Delete User",
            prompt="Select the user you want to disable."
        )
        if not user_id:
            return

        ok = messagebox.askyesno(
            "Delete User",
            f"Disable user '{user_id}'?\n\n"
            "Disabled users are hidden from the UI and excluded from identification/verification."
        )
        if not ok:
            return

        if disable_user(user_id):
            self.refresh_users()
            self.write_result(
                f"Disabled user: {user_id}\n"
                "Note: existing templates are kept in the database, but disabled users are ignored during matching."
            )
            self.set_status("User disabled.")
        else:
            messagebox.showerror("Error", f"User '{user_id}' not found.")

    def bulk_enroll_iitd_gui(self):
        folder = filedialog.askdirectory(title="Select IITD root folder")
        if not folder:
            return

        try:
            self.set_status("Bulk enrolling IITD dataset...")
            data = collect_iitd_images(folder)
            stats = bulk_enroll_dataset(data)
            self.refresh_users()

            result = (
                f"BULK ENROLLMENT RESULT\n"
                f"----------------------\n"
                f"Users enrolled: {stats['total_users']}\n"
                f"Left-eye templates enrolled: {stats['total_left']}\n"
                f"Right-eye templates enrolled: {stats['total_right']}\n"
                f"Total templates: {stats['total_left'] + stats['total_right']}\n"
            )

            if stats["failed_items"]:
                result += "\nFailed items (first 20):\n" + "\n".join(stats["failed_items"][:20])

            self.write_result(result)
            self.set_status("Bulk enrollment complete.")

        except Exception as e:
            messagebox.showerror("Bulk Enrollment Error", str(e))
            self.set_status("Bulk enrollment failed.")

    def train_iitd_gui(self):
        folder = filedialog.askdirectory(title="Select IITD root folder")
        if not folder:
            return

        try:
            self.set_status("Training/evaluating with IITD dataset...")
            reset_database()

            data = collect_iitd_images(folder)
            train_data, test_data = split_iitd_train_test(data, train_ratio=0.7, seed=42)

            enroll_stats = bulk_enroll_dataset(train_data)
            self.refresh_users()

            threshold_stats = None
            try:
                threshold_stats = suggest_thresholds()
            except Exception:
                threshold_stats = None

            eval_stats = evaluate_on_test_set(test_data)

            report = (
                f"IITD TRAINING RESULT\n"
                f"--------------------\n"
                f"Users enrolled from training split: {enroll_stats['total_users']}\n"
                f"Training left templates: {enroll_stats['total_left']}\n"
                f"Training right templates: {enroll_stats['total_right']}\n"
                f"Total training templates: {enroll_stats['total_left'] + enroll_stats['total_right']}\n\n"
                f"EVALUATION ON TEST SET\n"
                f"----------------------\n"
                f"Identification total: {eval_stats['identification_total']}\n"
                f"Identification correct: {eval_stats['identification_correct']}\n"
                f"Identification rejected: {eval_stats['identification_rejected']}\n"
                f"Identification accuracy: {eval_stats['identification_accuracy'] * 100:.2f}%\n\n"
                f"Verification total: {eval_stats['verification_total']}\n"
                f"Verification correct: {eval_stats['verification_correct']}\n"
                f"Verification accuracy: {eval_stats['verification_accuracy'] * 100:.2f}%\n"
            )

            if threshold_stats is not None:
                report += (
                    f"\nTHRESHOLD STATS\n"
                    f"---------------\n"
                    f"Suggested safe verification threshold: {threshold_stats['verification_threshold_safe']:.4f}\n"
                    f"Suggested identification threshold: {threshold_stats['identification_threshold']:.4f}\n"
                )

            if enroll_stats["failed_items"]:
                report += "\n\nEnrollment failures (first 10):\n" + "\n".join(enroll_stats["failed_items"][:10])

            if eval_stats["identification_failed"]:
                report += "\n\nIdentification failures (first 10):\n" + "\n".join(eval_stats["identification_failed"][:10])

            if eval_stats["verification_failed"]:
                report += "\n\nVerification failures (first 10):\n" + "\n".join(eval_stats["verification_failed"][:10])

            self.write_result(report)
            self.set_status("IITD training complete.")

        except Exception as e:
            messagebox.showerror("IITD Training Error", str(e))
            self.set_status("IITD training failed.")

    def identify_current(self):
        try:
            src = self.get_current_image_source()
            eye_side = self.eye_var.get()

            self.set_status("Identifying user...")
            user, score, debug = identify_user(src, eye_side)
            self.last_debug = debug
            self.show_debug_outputs(src, debug)

            nz_ratio = debug.get("strip_nonzero_ratio", 0.0)
            contrast = debug.get("strip_contrast", 0.0)
            mean_val = debug.get("strip_mean", 0.0)

            if user is None:
                result = (
                    f"IDENTIFICATION RESULT\n"
                    f"---------------------\n"
                    f"Eye side: {eye_side}\n"
                    f"Status: UNKNOWN / REJECTED\n"
                    f"Best score: {score:.4f}\n"
                    f"Threshold: {IDENTIFY_REJECT_THRESHOLD:.4f}\n"
                    f"Strip nonzero ratio: {nz_ratio:.2f}\n"
                    f"Strip contrast: {contrast:.2f}\n"
                    f"Strip mean intensity: {mean_val:.2f}\n"
                )
            else:
                result = (
                    f"IDENTIFICATION RESULT\n"
                    f"---------------------\n"
                    f"Eye side: {eye_side}\n"
                    f"Predicted user: {user}\n"
                    f"Match score: {score:.4f}\n"
                    f"Threshold: {IDENTIFY_REJECT_THRESHOLD:.4f}\n"
                    f"Strip nonzero ratio: {nz_ratio:.2f}\n"
                    f"Strip contrast: {contrast:.2f}\n"
                    f"Strip mean intensity: {mean_val:.2f}\n"
                )

            self.write_result(result)
            self.set_status("Identification complete.")

        except Exception as e:
            messagebox.showerror("Identification Error", str(e))
            self.set_status("Identification failed.")

    def verify_current(self):
        try:
            src = self.get_current_image_source()
            claimed_user = self.ask_user_selection(
                title="Verify User",
                prompt="Select the claimed user to verify."
            )
            if not claimed_user:
                return
            claimed_user = claimed_user.strip()

            eye_side = self.eye_var.get()
            self.set_status("Verifying claimed user...")

            accepted, score, debug = verify_user(claimed_user, src, eye_side)
            self.last_debug = debug
            self.show_debug_outputs(src, debug)

            nz_ratio = debug.get("strip_nonzero_ratio", 0.0)
            contrast = debug.get("strip_contrast", 0.0)
            mean_val = debug.get("strip_mean", 0.0)
            nearest_other = debug.get("best_other_user", "N/A")
            nearest_other_score = debug.get("best_other_score", 1.0)
            margin = debug.get("verification_margin", 0.0)

            result = (
                f"VERIFICATION RESULT\n"
                f"-------------------\n"
                f"Claimed user: {claimed_user}\n"
                f"Eye side: {eye_side}\n"
                f"Accepted: {accepted}\n"
                f"Claimed-user score: {score:.4f}\n"
                f"Nearest other user: {nearest_other}\n"
                f"Nearest other score: {nearest_other_score:.4f}\n"
                f"Margin vs nearest other: {margin:.4f}\n"
                f"Threshold: {VERIFY_ACCEPT_THRESHOLD:.4f}\n"
                f"Strip nonzero ratio: {nz_ratio:.2f}\n"
                f"Strip contrast: {contrast:.2f}\n"
                f"Strip mean intensity: {mean_val:.2f}\n"
            )

            self.write_result(result)
            self.set_status("Verification complete.")

        except Exception as e:
            messagebox.showerror("Verification Error", str(e))
            self.set_status("Verification failed.")

    def tune_thresholds_gui(self):
        try:
            self.set_status("Tuning thresholds from enrolled templates...")
            stats = suggest_thresholds()

            result = (
                f"THRESHOLD TUNING RESULT\n"
                f"-----------------------\n"
                f"Genuine min: {stats['genuine_min']:.4f}\n"
                f"Genuine max: {stats['genuine_max']:.4f}\n"
                f"Genuine mean: {stats['genuine_mean']:.4f}\n"
                f"Genuine 95th percentile: {stats['genuine_95']:.4f}\n\n"
                f"Impostor min: {stats['impostor_min']:.4f}\n"
                f"Impostor max: {stats['impostor_max']:.4f}\n"
                f"Impostor mean: {stats['impostor_mean']:.4f}\n\n"
                f"Suggested VERIFY_ACCEPT_THRESHOLD: {stats['verification_threshold_safe']:.4f}\n"
                f"Suggested IDENTIFY_REJECT_THRESHOLD: {stats['identification_threshold']:.4f}\n"
            )

            self.write_result(result)
            self.set_status("Threshold tuning complete.")

        except Exception as e:
            messagebox.showerror("Threshold Tuning Error", str(e))
            self.set_status("Threshold tuning failed.")


# =========================
# Main
# =========================
if __name__ == "__main__":
    ensure_db()
    root = Tk()
    app = IrisSecurityApp(root)
    root.mainloop()