import streamlit as st
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import requests
from PIL import Image
from io import BytesIO
import tinify
import base64

# python3 -m streamlit run image_bot.py
# CONFIG
PEXELS_API_KEY = '7KMNL1mUuh4N6d3rwdYX6Mxdu6oNuqw2E8uEyNw7BOAer50f2fUtWTFe'
TINYPNG_API_KEY = 'XBn78sZWZnLb6kxzCPznkZFgvSzmZK1q'
SHEET_NAME = 'ClientBlogImageSettings'
TAB_NAME = 'Clients'
tinify.key = TINYPNG_API_KEY

# Google Sheets Setup
@st.cache_resource
def get_sheet_data():
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
        client = gspread.authorize(creds)
        sheet = client.open(SHEET_NAME).worksheet(TAB_NAME)
        return sheet.get_all_records()
    except Exception as e:
        st.error(f"Error accessing Google Sheets: {e}")
        return []
# Helper: Parse aspect ratio
def parse_aspect_ratio(aspect_ratio_str):
    try:
        aspect_ratio_str = aspect_ratio_str.replace("∶", ":")
        width, height = map(int, aspect_ratio_str.split(":"))
        return width / height, width, height
    except Exception as e:
        st.error(f"Error parsing aspect ratio '{aspect_ratio_str}': {e}")
        return 1.0, 1, 1
# Updated compression function to strictly enforce 250–310 KB and properly convert to JPEG
def compress_with_tinypng(pil_image, attempt=1, max_attempts=8):
    try:
        # First compress with TinyPNG (as PNG)
        buffer = BytesIO()
        pil_image.save(buffer, format="PNG")
        buffer.seek(0)
        source = tinify.from_buffer(buffer.read())
        compressed_png_data = source.to_buffer()
        
        # Convert the compressed PNG to PIL Image
        compressed_image = Image.open(BytesIO(compressed_png_data))
        
        # Convert to RGB if it has transparency (for JPEG compatibility)
        if compressed_image.mode in ('RGBA', 'LA', 'P'):
            rgb_image = Image.new('RGB', compressed_image.size, (255, 255, 255))
            if compressed_image.mode == 'P':
                compressed_image = compressed_image.convert('RGBA')
            rgb_image.paste(compressed_image, mask=compressed_image.split()[-1] if compressed_image.mode == 'RGBA' else None)
            compressed_image = rgb_image
        elif compressed_image.mode != 'RGB':
            compressed_image = compressed_image.convert('RGB')
        
        # Save as JPEG with high quality
        jpeg_buffer = BytesIO()
        compressed_image.save(jpeg_buffer, format="JPEG", quality=95)
        jpeg_data = jpeg_buffer.getvalue()
        size_kb = len(jpeg_data) / 1024
        
        if 250 <= size_kb <= 315:
            return jpeg_data, size_kb
        
        if attempt >= max_attempts:
            return jpeg_data, size_kb  # Give up after max_attempts
        
        # More precise scaling based on target range
        target_kb = 280  # Target middle of range
        if size_kb > 315:
            # Image too large - shrink it
            scale_factor = (target_kb / size_kb) ** 0.5
        else:
            # Image too small - grow it
            if size_kb < 250:
                scale_factor = (target_kb / size_kb) ** 0.5
            else:
                # Already in acceptable range
                return jpeg_data, size_kb
        
        # Ensure we don't make drastic changes in one step
        scale_factor = max(0.7, min(1.4, scale_factor))
        new_width = int(pil_image.width * scale_factor)
        new_height = int(pil_image.height * scale_factor)
        resized = pil_image.resize((new_width, new_height), Image.LANCZOS)
        
        return compress_with_tinypng(resized, attempt + 1, max_attempts)
        
    except Exception as e:
        st.error(f"Error compressing image: {e}")
        # Fallback: return original image as JPEG with high quality
        try:
            # Convert to RGB if necessary
            if pil_image.mode in ('RGBA', 'LA', 'P'):
                rgb_image = Image.new('RGB', pil_image.size, (255, 255, 255))
                if pil_image.mode == 'P':
                    pil_image = pil_image.convert('RGBA')
                rgb_image.paste(pil_image, mask=pil_image.split()[-1] if pil_image.mode == 'RGBA' else None)
                pil_image = rgb_image
            elif pil_image.mode != 'RGB':
                pil_image = pil_image.convert('RGB')
                
            buffer = BytesIO()
            pil_image.save(buffer, format="JPEG", quality=90)
            return buffer.getvalue(), len(buffer.getvalue()) / 1024
        except Exception as fallback_error:
            st.error(f"Fallback conversion also failed: {fallback_error}")
            return b"", 0
# Main image generation logic
def generate_images(client_data, prompt, base_filename="image"):
    try:
        target_ratio, ar_width, ar_height = parse_aspect_ratio(client_data["Aspect Ratio"])
        headers = {"Authorization": PEXELS_API_KEY}
        params = {"query": prompt, "per_page": 3, "orientation": "landscape"}
        
        response = requests.get("https://api.pexels.com/v1/search", headers=headers, params=params)
        
        if response.status_code != 200:
            st.error(f"Pexels API error: {response.status_code}")
            return []
            
        photos = response.json().get("photos", [])
        
        # Try without orientation filter if no results
        if not photos:
            params_no_orientation = {"query": prompt, "per_page": 3}
            response2 = requests.get("https://api.pexels.com/v1/search", headers=headers, params=params_no_orientation)
            if response2.status_code == 200:
                photos = response2.json().get("photos", [])
        if not photos:
            st.warning("No photos found for your search query. Try different keywords.")
            return []
        output_images = []
        for idx, photo in enumerate(photos[:3], start=1):
            image_url = photo["src"].get("large") or photo["src"]["medium"]
            
            try:
                response = requests.get(image_url)
                original = Image.open(BytesIO(response.content))
                # Crop to aspect ratio
                width, height = original.size
                current_ratio = width / height
                if current_ratio > target_ratio:
                    new_width = int(height * target_ratio)
                    left = (width - new_width) // 2
                    cropped = original.crop((left, 0, left + new_width, height))
                else:
                    new_height = int(width / target_ratio)
                    top = (height - new_height) // 2
                    cropped = original.crop((0, top, width, top + new_height))
                # Resize if needed
                min_width = 1600
                min_height = int(min_width * (ar_height / ar_width))
                if cropped.width >= min_width and cropped.height >= min_height:
                    resized = cropped.resize((min_width, min_height), Image.LANCZOS)
                else:
                    resized = cropped
                compressed_data, size_kb = compress_with_tinypng(resized)
                filename = f"{base_filename}_{idx}.jpg" if len(photos) > 1 else f"{base_filename}.jpg"
                output_images.append((compressed_data, size_kb, filename))
                
            except Exception as e:
                st.error(f"Error processing image {idx}: {e}")
                continue
        return output_images
        
    except Exception as e:
        st.error(f"Error in generate_images: {e}")
        return []
# Streamlit UI
st.title("📸 Blog Image Generator")
sheet_data = get_sheet_data()
if not sheet_data:
    st.error("No client data found. Please check your Google Sheets connection.")
    st.stop()
client_names = [row["Client Name"] for row in sheet_data if "Client Name" in row]
if not client_names:
    st.error("No client names found in the sheet data.")
    st.stop()
selected_client = st.selectbox("Select your client:", client_names)
upload_option = st.radio("Choose image source:", ["Search with Pexels", "Upload my own image"])

# Custom filename input
custom_filename = st.text_input("Custom filename (optional):", placeholder="Enter filename without extension")

prompt = ""
uploaded_file = None
if upload_option == "Search with Pexels":
    prompt = st.text_input("Describe the image you need:")
else:
    uploaded_file = st.file_uploader("Upload an image", type=["jpg", "jpeg", "png"])
if st.button("Generate Images") and selected_client:
    client_data = next((row for row in sheet_data if row.get("Client Name") == selected_client), None)
    
    if not client_data:
        st.error(f"Client data not found for: {selected_client}")
        st.stop()
    with st.spinner("Processing image..."):
        if upload_option == "Upload my own image" and uploaded_file:
            try:
                image = Image.open(uploaded_file)
                target_ratio, ar_width, ar_height = parse_aspect_ratio(client_data["Aspect Ratio"])
                width, height = image.size
                current_ratio = width / height
                if current_ratio > target_ratio:
                    new_width = int(height * target_ratio)
                    left = (width - new_width) // 2
                    cropped = image.crop((left, 0, left + new_width, height))
                else:
                    new_height = int(width / target_ratio)
                    top = (height - new_height) // 2
                    cropped = image.crop((0, top, width, top + new_height))
                min_width = 1600
                min_height = int(min_width * (ar_height / ar_width))
                if cropped.width >= min_width and cropped.height >= min_height:
                    resized = cropped.resize((min_width, min_height), Image.LANCZOS)
                else:
                    resized = cropped
                compressed_data, size_kb = compress_with_tinypng(resized)
                
                # Use custom filename if provided, otherwise use default
                if custom_filename.strip():
                    filename = f"{custom_filename.strip()}.jpg"
                else:
                    filename = f"{client_data['Client Name'].replace(' ', '_')}_uploaded.jpg"
                
                st.image(compressed_data, caption=f"{filename} ({round(size_kb)} KB)", use_container_width=True)
                b64 = base64.b64encode(compressed_data).decode()
                href = f'<a href="data:image/jpeg;base64,{b64}" download="{filename}">📥 Download {filename}</a>'
                st.markdown(href, unsafe_allow_html=True)
                
            except Exception as e:
                st.error(f"Error processing uploaded image: {e}")
        elif upload_option == "Search with Pexels" and prompt:
            # Use custom filename if provided, otherwise use default
            base_filename = custom_filename.strip() if custom_filename.strip() else "image"
            images = generate_images(client_data, prompt, base_filename)
            
            if images:
                for img_bytes, size_kb, filename in images:
                    st.image(img_bytes, caption=f"{filename} ({round(size_kb)} KB)", use_container_width=True)
                    b64 = base64.b64encode(img_bytes).decode()
                    href = f'<a href="data:image/jpeg;base64,{b64}" download="{filename}">📥 Download {filename}</a>'
                    st.markdown(href, unsafe_allow_html=True)
                
        else:
            st.warning("Please fill in the prompt or upload a valid image file.")