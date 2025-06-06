from flask import Flask, request, jsonify
import os
import requests
import tempfile
import fal_client
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# === WATI Config ===
WATI_BEARER_TOKEN = os.getenv("WATI_BEARER_TOKEN") or "YOUR_WATI_BEARER_TOKEN"

# === Google Drive Config ===
SERVICE_ACCOUNT_FILE = 'service_account.json'
DRIVE_FOLDER_ID = '1CxYhtopcXOofh0UGgVLyL3zyN5-wmiLE'

# === Set FAL API Key ===
FAL_API_KEY = os.getenv("FAL_API_KEY") or "d0ef57c7-5a0e-4a87-aa66-281b437bc0ae:3aaa35e26a361b9783c55d6b2781fc48"
os.environ["FAL_KEY"] = FAL_API_KEY

# === Flask App ===
app = Flask(__name__)
user_states = {}


def upload_to_drive(file_path, filename):
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE,
        scopes=["https://www.googleapis.com/auth/drive"]
    )
    service = build("drive", "v3", credentials=creds)

    file_metadata = {'name': filename, 'parents': [DRIVE_FOLDER_ID]}
    media = MediaFileUpload(file_path, mimetype='image/jpeg', resumable=True)
    uploaded_file = service.files().create(body=file_metadata, media_body=media, fields='id').execute()

    service.permissions().create(
        fileId=uploaded_file['id'],
        body={'type': 'anyone', 'role': 'reader'}
    ).execute()

    return f"https://drive.google.com/uc?id={uploaded_file['id']}"


def send_wati_message(phone, message):
    headers = {
        "Authorization": f"Bearer {WATI_BEARER_TOKEN}",
        "Content-Type": "application/json"
    }
    data = {
        "whatsappNumber": phone,
        "messageText": message
    }
    requests.post("https://app.wati.io/api/v1/sendSessionMessage", headers=headers, json=data)


def send_wati_image(phone, image_url, caption=""):
    headers = {
        "Authorization": f"Bearer {WATI_BEARER_TOKEN}",
        "Content-Type": "application/json"
    }
    data = {
        "whatsappNumber": phone,
        "fileName": "image.jpg",
        "fileUrl": image_url,
        "caption": caption
    }
    requests.post("https://app.wati.io/api/v1/sendMediaMessage", headers=headers, json=data)


@app.route("/", methods=["GET"])
def home():
    return "✅ WhatsApp bot is live!"


@app.route("/wati-webhook", methods=["POST"])
def receive_wati_message():
    data = request.get_json()
    phone = data.get("waId")
    text = data.get("text", "").strip()

    if not phone:
        return jsonify({"status": "error", "message": "No phone number found"}), 400

    state = user_states.get(phone, {})

    if not state:
        user_states[phone] = {"step": "awaiting_image"}
        send_wati_message(phone, "📸 Please upload an image to begin.")
    elif state["step"] == "awaiting_prompt":
        user_states[phone]["prompt"] = text
        user_states[phone]["step"] = "processing"
        send_wati_message(phone, "🎨 Editing image with your prompt. Please wait...")

        try:
            drive_url = state["image_url"]
            prompt = state["prompt"]

            result = fal_client.submit(
                "fal-ai/flux-pro/kontext",
                arguments={
                    "prompt": prompt,
                    "guidance_scale": 3.5,
                    "num_images": 1,
                    "safety_tolerance": "2",
                    "output_format": "jpeg",
                    "image_url": drive_url
                }
            ).get()

            image_url = result["images"][0]["url"]
            send_wati_image(phone, image_url, "✅ Here's your edited image!")
            send_wati_message(phone, "✨ Want to generate more images? Send another image.")
            user_states.pop(phone, None)

        except Exception as e:
            send_wati_message(phone, f"⚠️ Error: {str(e)}")
            user_states.pop(phone, None)
    else:
        send_wati_message(phone, "📸 Please upload an image first.")

    return jsonify({"status": "ok"})


@app.route("/wati-media", methods=["POST"])
def receive_wati_media():
    data = request.get_json()
    phone = data.get("waId")
    media_url = data.get("mediaUrl")

    if not phone or not media_url:
        return jsonify({"status": "error", "message": "Missing phone or media"}), 400

    # Download image
    r = requests.get(media_url)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as temp_file:
        temp_file.write(r.content)
        temp_file_path = temp_file.name

    try:
        uploaded_url = upload_to_drive(temp_file_path, os.path.basename(temp_file_path))
        user_states[phone] = {"step": "awaiting_prompt", "image_url": uploaded_url}
        send_wati_message(phone, "📝 Image received. Now send your prompt.")
    except Exception as e:
        send_wati_message(phone, f"❌ Failed to process image: {str(e)}")
    finally:
        os.remove(temp_file_path)

    return jsonify({"status": "ok"})


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
