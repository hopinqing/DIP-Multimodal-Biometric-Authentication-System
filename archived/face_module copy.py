import cv2
import numpy as np
import os
import random
import time
import winsound
from deepface import DeepFace

import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision

# ===============================
# Configuration
# ===============================

MODEL = "ArcFace"
MATCH_THRESHOLD = 0.4

ENROLL_FRAMES = 5
AUTH_FRAMES = 3

EMBEDDING_FILE = "stored_face_embeddings.npy"
FACE_MODEL_PATH = "modules/face/face_landmarker.task"

# Size Constraint
MIN_FACE_HEIGHT_RATIO = 0.45  # Face must occupy at least 45% of the frame
MAX_FACE_HEIGHT_RATIO = 0.75  # Prevent getting too close (lens distortion)

# Liveness & Tracking
LIVENESS_TIMEOUT = 15
ANGLE_THRESHOLD_YAW = 18
ANGLE_THRESHOLD_PITCH = 15
REQUIRED_SUCCESS_FRAMES = 5
SMOOTHING_WINDOW = 5
LIVENESS_STEPS = 3
AUTO_STABLE_FRAMES = 3

# Security constraints
FACE_TRACKING_THRESHOLD = 0.15  # Max allowed jump distance
MAX_LOST_FRAMES = 3             # Max frames the face can be missing

# ===============================
# ArcFace 5-Point Template & Landmarks
# ===============================

ARC_FACE_TEMPLATE_5 = np.array([
    [38.2946, 51.6963],   # left eye center
    [73.5318, 51.5014],   # right eye center
    [56.0252, 71.7366],   # nose tip
    [41.5493, 92.3655],   # left mouth corner
    [70.7299, 92.2041]    # right mouth corner
], dtype=np.float32)

# Using multiple points to average the center of the eye
LEFT_EYE_INDICES = [33, 133, 159, 145]
RIGHT_EYE_INDICES = [362, 263, 386, 374]

NOSE_TIP = 1
LEFT_MOUTH = 61
RIGHT_MOUTH = 291

# ===============================
# Load Models
# ===============================
print("[INFO] Loading DeepFace model...")
DeepFace.build_model(MODEL)

print("[INFO] Loading MediaPipe Face Landmarker...")
base_options = python.BaseOptions(model_asset_path=FACE_MODEL_PATH)
face_options = vision.FaceLandmarkerOptions(
    base_options=base_options,
    num_faces=1,
    output_facial_transformation_matrixes=True,
    running_mode=vision.RunningMode.IMAGE
)
face_landmarker = vision.FaceLandmarker.create_from_options(face_options)
print("[INFO] Models loaded successfully. Face module ready.")

# ===============================
# Utilities & Proximity Logic
# ===============================

def open_camera():
    cap = cv2.VideoCapture(0)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return cap

def is_blurry(frame, threshold=100):
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return cv2.Laplacian(gray, cv2.CV_64F).var() < threshold

def get_landmark_dist(pt1, pt2):
    return np.sqrt((pt1[0] - pt2[0])**2 + (pt1[1] - pt2[1])**2)

def rotation_matrix_to_angles(R):
    sy = np.sqrt(R[0,0]**2 + R[1,0]**2)
    singular = sy < 1e-6
    if not singular:
        x = np.arctan2(R[2,1], R[2,2])
        y = np.arctan2(-R[2,0], sy)
        z = np.arctan2(R[1,0], R[0,0])
    else:
        x = np.arctan2(-R[1,2], R[1,1])
        y = np.arctan2(-R[2,0], sy)
        z = 0
    return np.degrees(x), np.degrees(y), np.degrees(z)

def get_head_pose(result):
    if not result or not result.facial_transformation_matrixes: return None, None
    matrix = result.facial_transformation_matrixes[0]
    mat = np.array(matrix).reshape(4,4)
    R = mat[:3, :3]
    pitch, yaw, roll = rotation_matrix_to_angles(R)
    return yaw, pitch

def get_face_status(landmarks, w, h):
    # 1. Check if face is centered in the oval
    nose = landmarks[NOSE_TIP]
    nose_x, nose_y = nose.x * w, nose.y * h
    center_x, center_y = w / 2, h / 2
    
    # Require the nose to be within a tight 15% bounding box of the screen center
    if abs(nose_x - center_x) > w * 0.15 or abs(nose_y - center_y) > h * 0.15:
        return "CENTER YOUR FACE IN OVAL"

    # 2. Convert normalized forehead (10) and chin (152) to pixels
    x_top, y_top = landmarks[10].x * w, landmarks[10].y * h
    x_chin, y_chin = landmarks[152].x * w, landmarks[152].y * h
    
    face_length_px = np.sqrt((x_chin - x_top)**2 + (y_chin - y_top)**2)
    face_size_ratio = face_length_px / h
    
    # 3. Check Size constraints
    if face_size_ratio < MIN_FACE_HEIGHT_RATIO:
        return "MOVE CLOSER"
    if face_size_ratio > MAX_FACE_HEIGHT_RATIO:
        return "MOVE FURTHER AWAY"

    # 4. Prevent Out-of-Frame cropping
    xs = [lm.x for lm in landmarks]
    ys = [lm.y for lm in landmarks]
    if min(xs) < 0.02 or max(xs) > 0.98 or min(ys) < 0.02 or max(ys) > 0.98:
        return "FACE TOO CLOSE TO EDGE"

    return "GOOD"

# ===============================
# Face Overlay & Guide
# ===============================

def draw_face_guide(frame, status):
    h, w, _ = frame.shape
    center = (w // 2, h // 2)
    axes = (int(w * 0.25), int(h * 0.38)) 
    
    color = (0, 255, 0) if status == "GOOD" else (0, 0, 255)
    cv2.ellipse(frame, center, axes, 0, 0, 360, color, 2)
    
    if status != "GOOD":
        text_size = cv2.getTextSize(status, cv2.FONT_HERSHEY_SIMPLEX, 0.8, 2)[0]
        cv2.putText(frame, status, ((w - text_size[0]) // 2, h - 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
        
    return frame

def draw_live_facemesh(frame, result, mesh_color=(0, 255, 0)):
    if not result or not result.face_landmarks:
        return frame
        
    h, w, _ = frame.shape
    landmarks = result.face_landmarks[0]
    
    for lm in landmarks:
        x, y = int(lm.x * w), int(lm.y * h)
        cv2.circle(frame, (x, y), 1, mesh_color, -1)
        
    left_x = int(sum(landmarks[idx].x for idx in LEFT_EYE_INDICES) / len(LEFT_EYE_INDICES) * w)
    left_y = int(sum(landmarks[idx].y for idx in LEFT_EYE_INDICES) / len(LEFT_EYE_INDICES) * h)
    
    right_x = int(sum(landmarks[idx].x for idx in RIGHT_EYE_INDICES) / len(RIGHT_EYE_INDICES) * w)
    right_y = int(sum(landmarks[idx].y for idx in RIGHT_EYE_INDICES) / len(RIGHT_EYE_INDICES) * h)
    
    nose_x, nose_y = int(landmarks[NOSE_TIP].x * w), int(landmarks[NOSE_TIP].y * h)
    l_mouth_x, l_mouth_y = int(landmarks[LEFT_MOUTH].x * w), int(landmarks[LEFT_MOUTH].y * h)
    r_mouth_x, r_mouth_y = int(landmarks[RIGHT_MOUTH].x * w), int(landmarks[RIGHT_MOUTH].y * h)

    arcface_points = [(left_x, left_y), (right_x, right_y), (nose_x, nose_y), (l_mouth_x, l_mouth_y), (r_mouth_x, r_mouth_y)]
    for pt in arcface_points:
        cv2.circle(frame, pt, 4, (0, 0, 255), -1) 
        
    return frame

def countdown_with_feed(cap, window_name, seconds, message):
    valid_time = 0
    last_time = time.time()
    
    while valid_time < seconds:
        current_time = time.time()
        dt = current_time - last_time
        last_time = current_time
        
        ret, frame = cap.read()
        if not ret: continue
        
        h, w, _ = frame.shape 
        
        frame = cv2.flip(frame, 1)
        display = frame.copy()
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        result = face_landmarker.detect(mp_image)
        
        status = "NO FACE DETECTED"
        if result and result.face_landmarks:
            status = get_face_status(result.face_landmarks[0], w, h)
            
        display = draw_face_guide(display, status)
        display = draw_live_facemesh(display, result, mesh_color=(200, 200, 200))
        
        cv2.putText(display, message, (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
        
        if status == "GOOD":
            valid_time += dt
            remaining = int(seconds - valid_time) + 1
            text = str(remaining)
            text_size = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 4.0, 5)[0]
            text_x = (display.shape[1] - text_size[0]) // 2
            text_y = (display.shape[0] + text_size[1]) // 2
            cv2.putText(display, text, (text_x, text_y), cv2.FONT_HERSHEY_SIMPLEX, 4.0, (0, 255, 0), 5)
        else:
            valid_time = 0 
            
        cv2.imshow(window_name, display)
        if cv2.waitKey(1) & 0xFF == 27: return False 
    return True

def stabilize_feed(cap, window_name, seconds, message, tracked_center):
    valid_time = 0
    last_time = time.time()
    current_center = tracked_center
    lost_frames = 0
    
    while valid_time < seconds:
        current_time = time.time()
        dt = current_time - last_time
        last_time = current_time
            
        ret, frame = cap.read()
        if not ret: continue
        
        h, w, _ = frame.shape 
        
        frame = cv2.flip(frame, 1)
        display = frame.copy()
        
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        result = face_landmarker.detect(mp_image)
        
        status = "NO FACE DETECTED"
        if result and result.face_landmarks:
            status = get_face_status(result.face_landmarks[0], w, h)
            
        display = draw_face_guide(display, status)
        display = draw_live_facemesh(display, result, mesh_color=(0, 255, 255))
        
        if not result.face_landmarks:
            lost_frames += 1
            if lost_frames > MAX_LOST_FRAMES:
                return False, None
        else:
            lost_frames = 0
            landmarks = result.face_landmarks[0]
            new_center = (landmarks[NOSE_TIP].x, landmarks[NOSE_TIP].y)
            
            if current_center is not None:
                if get_landmark_dist(new_center, current_center) > FACE_TRACKING_THRESHOLD:
                    return False, None
            current_center = new_center
        
        cv2.putText(display, message, (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
        
        if status == "GOOD":
            valid_time += dt
            remaining = int(seconds - valid_time) + 1
            text = str(remaining)
            text_size = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 4.0, 5)[0]
            text_x = (display.shape[1] - text_size[0]) // 2
            text_y = (display.shape[0] + text_size[1]) // 2
            cv2.putText(display, text, (text_x, text_y), cv2.FONT_HERSHEY_SIMPLEX, 4.0, (0, 255, 0), 5)
        else:
            valid_time = 0 
            
        cv2.imshow(window_name, display)
        if cv2.waitKey(1) & 0xFF == 27: return False, None
    return True, current_center

# ===============================
# 5-Point Alignment
# ===============================

def extract_aligned_face(frame, landmarks):
    h, w, _ = frame.shape
    
    left_x = sum(landmarks[idx].x for idx in LEFT_EYE_INDICES) / len(LEFT_EYE_INDICES) * w
    left_y = sum(landmarks[idx].y for idx in LEFT_EYE_INDICES) / len(LEFT_EYE_INDICES) * h
    
    right_x = sum(landmarks[idx].x for idx in RIGHT_EYE_INDICES) / len(RIGHT_EYE_INDICES) * w
    right_y = sum(landmarks[idx].y for idx in RIGHT_EYE_INDICES) / len(RIGHT_EYE_INDICES) * h

    src = np.array([
        [left_x, left_y],
        [right_x, right_y],
        [landmarks[NOSE_TIP].x * w, landmarks[NOSE_TIP].y * h],
        [landmarks[LEFT_MOUTH].x * w, landmarks[LEFT_MOUTH].y * h],
        [landmarks[RIGHT_MOUTH].x * w, landmarks[RIGHT_MOUTH].y * h]
    ], dtype=np.float32)

    M, _ = cv2.estimateAffinePartial2D(src, ARC_FACE_TEMPLATE_5)
    if M is None: raise ValueError("Alignment failed")

    aligned = cv2.warpAffine(frame, M, (112, 112), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
    if is_blurry(aligned): raise ValueError("Blurry face")
    return aligned

# ===============================
# Head Pose for Liveness
# ===============================

CHALLENGES = ["LEFT", "RIGHT", "UP", "DOWN"]

def perform_liveness_check(cap, window_name="Face Authentication"):
    sequence = random.sample(CHALLENGES, LIVENESS_STEPS)
    baseline = []
    
    last_face_center = None
    lost_frames = 0

    while len(baseline) < 15:
        ret, frame = cap.read()
        if not ret: continue

        frame = cv2.flip(frame, 1)
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        result = face_landmarker.detect(mp_image)

        frame = draw_live_facemesh(frame, result, mesh_color=(0, 255, 0))

        if result.facial_transformation_matrixes:
            yaw, pitch = get_head_pose(result)
            if yaw is not None: baseline.append((yaw, pitch))

        cv2.putText(frame, "Look Straight to Calibrate...", (20,40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255,255,0), 2)
        cv2.imshow(window_name, frame)
        if cv2.waitKey(1) & 0xFF == 27: return False, None

    baseline_yaw = np.mean([b[0] for b in baseline])
    baseline_pitch = np.mean([b[1] for b in baseline])

    step_index = 0
    success_frames = 0
    history = []
    
    waiting_for_center = False

    start = cv2.getTickCount()
    freq = cv2.getTickFrequency()

    while True:
        ret, frame = cap.read()
        if not ret: continue

        frame = cv2.flip(frame, 1)
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        result = face_landmarker.detect(mp_image)

        mesh_color = (0, 255, 255) if waiting_for_center else (0, 255, 0)
        frame = draw_live_facemesh(frame, result, mesh_color=mesh_color)

        if result.facial_transformation_matrixes and result.face_landmarks:
            lost_frames = 0
            landmarks = result.face_landmarks[0]
            new_center = (landmarks[NOSE_TIP].x, landmarks[NOSE_TIP].y)

            if last_face_center is not None:
                if get_landmark_dist(new_center, last_face_center) > FACE_TRACKING_THRESHOLD:
                    return False, None
            last_face_center = new_center

            yaw, pitch = get_head_pose(result)
            if yaw is None: continue

            delta_yaw = yaw - baseline_yaw
            delta_pitch = pitch - baseline_pitch

            history.append((delta_yaw, delta_pitch))
            if len(history) > SMOOTHING_WINDOW: history.pop(0)

            smoothed_yaw = np.mean([h[0] for h in history])
            smoothed_pitch = np.mean([h[1] for h in history])

            if waiting_for_center:
                cv2.putText(frame, "Return to CENTER", (20,40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0,255,255), 2)
                
                if abs(smoothed_yaw) < 10 and abs(smoothed_pitch) < 10:
                    success_frames += 1
                else:
                    success_frames = 0

                if success_frames >= REQUIRED_SUCCESS_FRAMES:
                    waiting_for_center = False
                    success_frames = 0
                    history.clear()
            else:
                direction = sequence[step_index]
                cv2.putText(frame, f"Step {step_index+1}/{LIVENESS_STEPS}: TURN {direction}",
                            (20,40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0,255,0), 2)
                
                turned_left = smoothed_yaw < -ANGLE_THRESHOLD_YAW
                turned_right = smoothed_yaw > ANGLE_THRESHOLD_YAW
                turned_up = smoothed_pitch < -ANGLE_THRESHOLD_PITCH
                turned_down = smoothed_pitch > ANGLE_THRESHOLD_PITCH

                wrong_movement = False
                if direction == "LEFT" and (turned_right or turned_up or turned_down):
                    wrong_movement = True
                elif direction == "RIGHT" and (turned_left or turned_up or turned_down):
                    wrong_movement = True
                elif direction == "UP" and (turned_down or turned_left or turned_right):
                    wrong_movement = True
                elif direction == "DOWN" and (turned_up or turned_left or turned_right):
                    wrong_movement = True

                if wrong_movement:
                    winsound.Beep(400, 500) 
                    text_size = cv2.getTextSize("WRONG MOVEMENT", cv2.FONT_HERSHEY_SIMPLEX, 1.2, 4)[0]
                    text_x = (frame.shape[1] - text_size[0]) // 2
                    cv2.putText(frame, "WRONG MOVEMENT", (text_x, 200), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 255), 4)
                    cv2.imshow(window_name, frame)
                    cv2.waitKey(1200) 
                    return False, None

                condition_met = False
                if direction == "LEFT" and smoothed_yaw < -ANGLE_THRESHOLD_YAW and abs(smoothed_pitch) < 10:
                    condition_met = True
                elif direction == "RIGHT" and smoothed_yaw > ANGLE_THRESHOLD_YAW and abs(smoothed_pitch) < 10:
                    condition_met = True
                elif direction == "UP" and smoothed_pitch < -ANGLE_THRESHOLD_PITCH and abs(smoothed_yaw) < 10:
                    condition_met = True
                elif direction == "DOWN" and smoothed_pitch > ANGLE_THRESHOLD_PITCH and abs(smoothed_yaw) < 10:
                    condition_met = True

                if condition_met:
                    success_frames += 1
                else:
                    success_frames = 0

                if success_frames >= REQUIRED_SUCCESS_FRAMES:
                    winsound.Beep(1500, 250) 
                    step_index += 1
                    success_frames = 0
                    history.clear()

                    if step_index >= LIVENESS_STEPS:
                        return True, last_face_center
                    else:
                        waiting_for_center = True

        else:
            lost_frames += 1
            if lost_frames > MAX_LOST_FRAMES: return False, None

        cv2.imshow(window_name, frame)

        if (cv2.getTickCount() - start)/freq > LIVENESS_TIMEOUT: return False, None
        if cv2.waitKey(1) & 0xFF == 27: return False, None

# ===============================
# Embedding Capture
# ===============================

def capture_embeddings(cap, num_frames, tracked_center=None, window_name="Face Authentication"):
    embeddings = []
    stable = 0
    lost_frames = 0
    current_center = tracked_center

    while len(embeddings) < num_frames:
        ret, frame = cap.read()
        if not ret: continue

        h, w, _ = frame.shape 

        frame = cv2.flip(frame, 1)
        display = frame.copy()

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        result = face_landmarker.detect(mp_image)

        status = "NO FACE DETECTED"
        if result and result.face_landmarks:
            status = get_face_status(result.face_landmarks[0], w, h)

        display = draw_face_guide(display, status)
        display = draw_live_facemesh(display, result, mesh_color=(0, 255, 0))

        if not result.face_landmarks:
            lost_frames += 1
            if lost_frames > MAX_LOST_FRAMES and tracked_center is not None: return None 
            continue
        
        lost_frames = 0
        landmarks = result.face_landmarks[0]
        new_center = (landmarks[NOSE_TIP].x, landmarks[NOSE_TIP].y)

        if current_center is not None:
            if get_landmark_dist(new_center, current_center) > FACE_TRACKING_THRESHOLD: return None
        current_center = new_center

        if status != "GOOD":
            stable = 0
            cv2.imshow(window_name, display)
            if cv2.waitKey(1) & 0xFF == 27: break
            continue

        if result.facial_transformation_matrixes:
            yaw, pitch = get_head_pose(result)
            if yaw is None or abs(yaw) > 10 or abs(pitch) > 15:
                stable = 0
                cv2.putText(display, "Look Straight to Capture!", (20, 80), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
                cv2.imshow(window_name, display)
                if cv2.waitKey(1) & 0xFF == 27: break
                continue

        try:
            aligned_face = extract_aligned_face(frame, landmarks)
            stable += 1
            if stable >= AUTO_STABLE_FRAMES:
                embedding = DeepFace.represent(img_path=aligned_face, model_name=MODEL, detector_backend="skip", enforce_detection=False)[0]["embedding"]
                
                emb_array = np.array(embedding)
                emb_norm = emb_array / np.linalg.norm(emb_array)
                
                embeddings.append(emb_norm)
                stable = 0
        except Exception as e:
            stable = 0

        cv2.putText(display, f"Capturing {len(embeddings)}/{num_frames}...", (20,40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0,255,0), 2)
        cv2.imshow(window_name, display)

        if cv2.waitKey(1) & 0xFF == 27: break

    if len(embeddings) < num_frames: return None
    
    return np.array(embeddings)

# ===============================
# PUBLIC ENDPOINTS for main.py
# ===============================

def run_face_enrollment():
    print("[INFO] Preparing camera for Face Enrollment...")
    cap = open_camera()
    window = "Face Enrollment"
    
    if not cap.isOpened():
        print("[ERROR] Could not open camera.")
        return False
        
    try:
        if not countdown_with_feed(cap, window, 3, "Align Face in Oval..."):
            return False

        print("[INFO] Capturing facial features...")
        embeddings_array = capture_embeddings(cap, ENROLL_FRAMES, window_name=window)
        
        if embeddings_array is not None:
            print("[INFO] Processing Master Centroid...")
            raw_mean = np.mean(embeddings_array, axis=0)
            master_embedding = raw_mean / np.linalg.norm(raw_mean)
            
            np.save(EMBEDDING_FILE, np.array([master_embedding]))
            print("[SUCCESS] Face enrolled successfully.")
            return True
        else:
            print("[ERROR] Face Enrollment cancelled or failed.")
            return False
            
    except Exception as e:
        print(f"[ERROR] An unexpected error occurred during enrollment: {e}")
        return False
    finally:
        cap.release()
        cv2.destroyAllWindows()

def run_face_verification():
    if not os.path.exists(EMBEDDING_FILE):
        print("[ERROR] No enrolled face template found.")
        return False

    print("[INFO] Preparing camera for Face Authentication...")
    cap = open_camera()
    window = "Face Authentication"
    
    if not cap.isOpened():
        print("[ERROR] Could not open camera.")
        return False
        
    try:
        if not countdown_with_feed(cap, window, 3, "Align Face in Oval..."):
            return False

        print("[INFO] Starting Liveness Challenge...")
        passed_liveness, tracked_center = perform_liveness_check(cap, window_name=window)

        if not passed_liveness:
            print("[ERROR] Liveness check failed. Wrong movement or timeout.")
            return False

        print("[INFO] Stabilizing for feature extraction...")
        passed_stab, tracked_center = stabilize_feed(cap, window, 2, "Look Straight & Hold Still...", tracked_center)

        if not passed_stab:
            print("[ERROR] Authentication aborted. Face lost or swapped during stabilization!")
            return False

        print("[INFO] Capturing live face features...")
        live_embeddings = capture_embeddings(cap, AUTH_FRAMES, tracked_center, window_name=window)

        if live_embeddings is None:
            print("[ERROR] Authentication aborted. Face lost or swapped during capture!")
            return False

        stored_embeddings = np.load(EMBEDDING_FILE)
        
        # Calculate Distance
        similarity_matrix = np.dot(live_embeddings, stored_embeddings.T)
        distance_matrix = 1.0 - similarity_matrix
        min_distance = np.min(distance_matrix)
        match = min_distance < MATCH_THRESHOLD

        if match:
            print(f"[SUCCESS] Face Verified. Best Match Distance: {min_distance:.4f}")
            return True
        else:
            print(f"[FAIL] Face Denied. Best Match Distance: {min_distance:.4f}")
            return False

    except Exception as e:
        print(f"[ERROR] An unexpected error occurred during verification: {e}")
        return False
    finally:
        cap.release()
        cv2.destroyAllWindows()