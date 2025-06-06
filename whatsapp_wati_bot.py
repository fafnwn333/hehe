import os
import json
import tempfile
import requests
from flask import Flask, request, jsonify
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import fal_client

# === Configuration ===
WATI_API_URL = "https://app.wati.io/api/v1"
WATI_TOKEN = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJqdGkiOiI1N2JiZjgwNS0wYjM2LTQ1MTAtYjg4ZS03NzZlMjg2OTE3NDEiLCJ..."
FAL_API_KEY = "d0ef57c7-5a0e-4a87-aa66-281b437bc0ae:3aaa35e26a361b9783c55d6b2781fc48"
os.environ["FAL_KEY"] = FAL_API_KEY

SERVICE_ACCOUNT_FILE = "service_account.json"
DRIVE_FOLDER_ID = "1CxYhtopcXOofh0UGgVLyL3zyN5-wmiLE"

# === User State ===
user_state = {}

# === Flask App ===
app = Flask(__name__)

def upload_to_drive(file_path, filename):
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE,
        scopes=["https://www.googleapis.com/auth/drive"]
    )
    service = build("drive", "v3", credentials=creds)

    file_metadata = {'name': filename, 'parents': [DRIVE_FOLDER_ID]}
    media = MediaFileUpload(file_path, mimetype='image/jpeg')
    uploaded_file = service.files().create(body=file_metadata, media_body=media, fields='id').execute()

    service.permissions().create(
        fileId=uploaded_file['id'],
        body={'type': 'anyone', 'role': 'reader'}
    ).execute()

    return f"https://drive.google.com/uc?id={uploaded_file['id']}"

def send_wati_message(phone, text):
    headers = {
        "Authorization": WATI_TOKEN,
        "Content-Type": "application/json"
    }
    payload = {
        "whatsappNumber": phone,
        "messageText": text
    }
    requests.post(f"{WATI_API_URL}/sendSessionMessage", headers=headers, json=payload)

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    phone = data["waId"]
    message = data.get("text", "").strip()
    media = data.get("media", [])

    state = user_state.get(phone, {})

    # 1. Handle image upload
    if media and media[0].get("type") == "image":
        image_url = media[0]["url"]
        temp_image = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
        img_data = requests.get(image_url, headers={"Authorization": WATI_TOKEN}).content
        temp_image.write(img_data)
        temp_image.close()

        state["image_path"] = temp_image.name
        user_state[phone] = state

        send_wati_message(phone, "✅ Image received. Now send me your prompt.")
        return jsonify(success=True)

    # 2. Handle prompt input
    if "image_path" in state and "prompt" not in state:
        state["prompt"] = message
        user_state[phone] = state
        send_wati_message(phone, f"🎨 Generating image with prompt: *{message}*")

        try:
            # Upload image to drive
            drive_url = upload_to_drive(state["image_path"], os.path.basename(state["image_path"]))

            result = fal_client.submit(
                "fal-ai/flux-pro/kontext",
                arguments={
                    "prompt": message,
                    "guidance_scale": 3.5,
                    "num_images": 1,
                    "safety_tolerance": "2",
                    "output_format": "jpeg",
                    "image_url": drive_url
                }
            ).get()

            img_url = result["images"][0]["url"]
            send_wati_message(phone, f"✅ Here is your image: {img_url}")
            send_wati_message(phone, "🌀 Want to generate another? Send me a new image!")

        except Exception as e:
            send_wati_message(phone, f"⚠️ Error: {str(e)}")

        # Reset state
        user_state.pop(phone, None)

    else:
        send_wati_message(phone, "👋 Welcome! Please send an image to get started.")

    return jsonify(success=True)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

