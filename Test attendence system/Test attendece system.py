import os
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import cv2
    import face_recognition
    import numpy as np
    import pandas as pd
    from openpyxl import load_workbook
    from openpyxl.styles import PatternFill
except (ImportError, OSError) as error:
    try:
        from tkinter import messagebox

        messagebox.showerror(
            "Attendance setup needed",
            "The face-recognition dependencies are missing. Run "
            "install_attendance_dependencies.ps1 in the attendance folder, "
            f"then reopen Campus Suite.\n\nDetails: {error}",
        )
    except Exception:
        print(f"Attendance dependencies are missing: {error}", file=sys.stderr)
    raise SystemExit(1) from error

# This app stores its face images and attendance workbook beside this script.
os.chdir(Path(__file__).resolve().parent)
# Configuration
FACE_DIR = "faces"
EXCEL_FILE = "attendance.xlsx"
CUTOFF_HOUR = 7
CUTOFF_MIN = 10
DISPLAY_DURATION = 3
FONT = cv2.FONT_HERSHEY_SIMPLEX
FACE_MATCH_TOLERANCE = 0.6
ANALYZE_EVERY_N_FRAMES = 2
MAX_SKIPPED_FACES = 64

# Load known faces
known_encodings = []
known_info = []

if not os.path.exists(FACE_DIR):
    os.makedirs(FACE_DIR)

for file in os.listdir(FACE_DIR):
    path = os.path.join(FACE_DIR, file)
    image = face_recognition.load_image_file(path)
    enc = face_recognition.face_encodings(image)
    if enc:
        # Expecting filenames like: Name Roll Gr
        name_parts = os.path.splitext(file)[0].split()
        if len(name_parts) >= 3:
            name, roll, gr = name_parts[0], name_parts[1], name_parts[2]
        else:
            name, roll, gr = "Unknown", "000", "000"
        known_encodings.append(enc[0])
        known_info.append((name, roll, gr))

# Keep known encodings in one contiguous array so matching does not rebuild it
# on every camera frame.
known_encoding_matrix = np.asarray(known_encodings)

# Load or initialize Excel
if os.path.exists(EXCEL_FILE):
    # ensuring that the  Roll no. is read as string to prevent issues if it contains leading zeros
    df = pd.read_excel(EXCEL_FILE, dtype={'Roll no.': str})
else:
    df = pd.DataFrame(columns=["Roll no.", "Name", "Gr no.", "Time", "Status"])


def get_marked_rolls(day):
    if df.empty or "Roll no." not in df.columns or "Time" not in df.columns:
        return set()
    times = pd.to_datetime(df["Time"], errors="coerce")
    return set(df.loc[times.dt.date == day, "Roll no."].astype(str))


attendance_date = datetime.now().date()
marked_today = get_marked_rolls(attendance_date)

# --- CORE ATTENDANCE LOGIC ---
# Mark attendance and ensure the DataFrame is sorted by Roll no.
def mark_attendance(name, roll, gr):
    global df, attendance_date, marked_today
    now = datetime.now()
    today = now.date()

    if today != attendance_date:
        attendance_date = today
        marked_today = get_marked_rolls(today)

    # Avoid scanning and converting the workbook for every recognized face.
    if str(roll) in marked_today:
        return "skip"

    # Determine status (On time or Late)
    status = "On time" if now.hour < CUTOFF_HOUR or (now.hour == CUTOFF_HOUR and now.minute <= CUTOFF_MIN) else "Late"
    
    # Add the new entry
    df.loc[len(df)] = [roll, name, gr, now, status]
    marked_today.add(str(roll))
    
    # --- SORT DATAFRAME BY ROLL NO. ---
    # 1. Create a temporary numeric column for correct sorting of Roll numbers.
    #    'errors="coerce"' turns non-numeric values into NaN, which pandas handles gracefully.
    df['Roll_Sort_Key'] = pd.to_numeric(df['Roll no.'], errors='coerce')
    
    # 2. Sort the DataFrame: Primary sort is 'Roll_Sort_Key', secondary is 'Time'.
    #    The global df is updated here.
    df = df.sort_values(by=['Roll_Sort_Key', 'Time'], ascending=[True, True])
    
    # 3. Remove the temporary column before saving.
    df = df.drop(columns=['Roll_Sort_Key'])
    # --- END OD SORTH DATAFRAME BY ROLL NO. ---
    
    try:
        # Save the sorted DataFrame to Excel
        df.to_excel(EXCEL_FILE, index=False)
        
        # Openpyxl for formatting (coloring the Status column)
        wb = load_workbook(EXCEL_FILE)
        ws = wb.active
        headers = [cell.value for cell in ws[1]]
        status_col = headers.index("Status") + 1 if "Status" in headers else None

        green = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
        red = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")

        if status_col:
            for row in range(2, ws.max_row + 1):
                cell = ws.cell(row=row, column=status_col)
                if cell.value == "On time":
                    cell.fill = green
                elif cell.value == "Late":
                    cell.fill = red

        wb.save(EXCEL_FILE)

    except Exception as e:
        print("Error saving or formatting Excel:", e)

    return status

# --- VIDEO PROCESSING LOOP ---
# Camera init status
cap = None
for camera_index in (0, 1):
    camera = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW if os.name == "nt" else cv2.CAP_ANY)
    if camera.isOpened():
        for _ in range(10):
            ready, _frame = camera.read()
            if ready:
                cap = camera
                break
            time.sleep(0.15)
    if cap is not None:
        break
    camera.release()

if cap is None:
    try:
        from tkinter import messagebox

        messagebox.showerror(
            "Camera unavailable",
            "Campus Suite could not read a camera. Check camera permissions and "
            "close any other app using the camera, then try again.",
        )
    except Exception:
        print("No readable camera was found.", file=sys.stderr)
    raise SystemExit(1)

# Some camera backends ignore this setting; when supported it keeps a fresh
# preview frame available while face recognition is running.
cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

confirming = False
confirm_name = confirm_roll = confirm_gr = None
confirm_encoding = None
confirm_box = None

msg_text = "Scanning..."
msg_color = (150, 150, 150)
msg_timer = time.time()

skipped_encodings = np.empty((0, 128), dtype=float)
frame_number = 0
last_faces = []
last_encodings = []

try:
    while True:
        ret, frame = cap.read()
        if not ret:
            break

        frame_number += 1
        analyze_faces = not confirming and frame_number % ANALYZE_EVERY_N_FRAMES == 0
        if analyze_faces:
            # Run the expensive detector and encoder every other camera frame;
            # reuse the latest results for the intervening preview frame.
            small = cv2.resize(frame, (0, 0), fx=0.25, fy=0.25)
            rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
            last_faces = face_recognition.face_locations(rgb)
            last_encodings = face_recognition.face_encodings(rgb, last_faces)

        faces = last_faces
        encodings = last_encodings

        current_date = datetime.now().date()
        if current_date != attendance_date:
            attendance_date = current_date
            marked_today = get_marked_rolls(current_date)

        # Handle Confirmation State
        if confirming:
            key = cv2.waitKey(1) & 0xFF
            if key == ord('y'):
                result = mark_attendance(confirm_name, confirm_roll, confirm_gr)
                if result == "skip":
                    msg_text = f"{confirm_name} already marked."
                    msg_color = (0, 0, 255) # Blue/Red for already marked
                else:
                    msg_text = f"Welcome {confirm_name} - {result}"
                    msg_color = (0, 255, 0) if result == "On time" else (0, 0, 255)
                msg_timer = time.time()
                confirming = False
            elif key == ord('n'):
                # Skip this face temporarily
                skipped_encodings = np.vstack((skipped_encodings, confirm_encoding))[-MAX_SKIPPED_FACES:]
                msg_text = "Scanning..."
                msg_color = (150, 150, 150)
                msg_timer = time.time()
                confirming = False
        elif analyze_faces:
            # Check for matches
            for enc, loc in zip(encodings, faces):
                # Skip if recently skipped
                if skipped_encodings.size and np.any(
                    np.linalg.norm(skipped_encodings - enc, axis=1) < FACE_MATCH_TOLERANCE
                ):
                    continue

                if known_encoding_matrix.size:
                    distances = np.linalg.norm(known_encoding_matrix - enc, axis=1)
                    matches = np.flatnonzero(distances <= FACE_MATCH_TOLERANCE)
                else:
                    matches = []

                if len(matches):
                    i = int(matches[0])
                    name, roll, gr = known_info[i]
                    
                    # Check if attendance is already taken
                    if str(roll) in marked_today:
                        # Already marked, reset message but don't enter confirmation
                        msg_text = "Scanning..."
                        msg_color = (150, 150, 150)
                        msg_timer = time.time()
                        break
                    else:
                        # Found a match, enter confirmation state
                        confirming = True
                        confirm_encoding = enc
                        confirm_name, confirm_roll, confirm_gr = name, roll, gr
                        confirm_box = [v * 4 for v in loc]
                        break

        # Draw detected face boxes
        for loc in faces:
            top, right, bottom, left = [v * 4 for v in loc]
            cv2.rectangle(frame, (left, top), (right, bottom), (0, 255, 0), 2)

        # Draw Confirmation Box (if in confirmation state)
        if confirming and confirm_box and len(confirm_box) == 4:
            try:
                # Use a specific area for the confirmation modal
                cv2.rectangle(frame, (60, 60), (600, 220), (0, 0, 0), -1)
                cv2.putText(frame, f"Are you {confirm_name}?", (70, 110), FONT, 0.9, (255, 255, 255), 2)
                cv2.putText(frame, f"Roll No: {confirm_roll}", (70, 150), FONT, 0.8, (255, 255, 255), 1)
                cv2.putText(frame, "Press Y for YES or N for NO", (70, 190), FONT, 0.7, (0, 255, 0), 2)

                # Draw buttons for visual feedback
                cv2.rectangle(frame, (420, 125), (480, 180), (0, 255, 0), -1)
                cv2.putText(frame, "Y", (435, 165), FONT, 1.2, (0, 0, 0), 3)

                cv2.rectangle(frame, (510, 125), (570, 180), (0, 0, 255), -1)
                cv2.putText(frame, "N", (525, 165), FONT, 1.2, (255, 255, 255), 3)
            except:
                # Fallback if drawing confirmation box fails
                confirming = False
                confirm_box = None

        # Draw status message
        if not confirming:
            if time.time() - msg_timer <= DISPLAY_DURATION:
                cv2.rectangle(frame, (40, 40), (600, 100), msg_color, -1)
                cv2.putText(frame, msg_text, (50, 80), FONT, 0.85, (0, 0, 0), 2)
            else:
                # Reset to default scanning message
                msg_text = "Scanning..."
                msg_color = (150, 150, 150)
                msg_timer = time.time()

        cv2.imshow("Face Recognition Attendance", frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

finally:
    cap.release()
    cv2.destroyAllWindows()
