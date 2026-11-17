import io
import logging
import uuid
import warnings
from urllib.parse import urlparse

import requests
from django.core.files.base import ContentFile
from PIL import Image, UnidentifiedImageError

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 8 * 1024 * 1024
MAX_GOOGLE_BYTES = 5 * 1024 * 1024
MAX_SOURCE_DIMENSION = 4096
MAX_SOURCE_PIXELS = 16_000_000
MAX_STORED_DIMENSION = 1024
GOOGLE_REQUEST_TIMEOUT = (3.05, 6)
GOOGLE_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}
ALLOWED_FORMATS = {
    "JPEG": ("jpg", "image/jpeg"),
    "PNG": ("png", "image/png"),
    "WEBP": ("webp", "image/webp"),
}


class ProfileImageError(ValueError):
    pass


def is_google_picture_url(url):
    if not isinstance(url, str):
        return False
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme != "https" or not parsed.hostname:
        return False
    host = parsed.hostname.lower().rstrip(".")
    return host == "googleusercontent.com" or host.endswith(".googleusercontent.com")


def normalize_image_bytes(content, max_bytes):
    if not content:
        raise ProfileImageError("The image file is empty.")
    if len(content) > max_bytes:
        raise ProfileImageError("The image file is too large.")

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as image:
                image_format = (image.format or "").upper()
                if image_format not in ALLOWED_FORMATS:
                    raise ProfileImageError("Choose a JPG, PNG or WebP image.")
                width, height = image.size
                if width < 1 or height < 1:
                    raise ProfileImageError("The image has invalid dimensions.")
                if width > MAX_SOURCE_DIMENSION or height > MAX_SOURCE_DIMENSION:
                    raise ProfileImageError("The image dimensions are too large.")
                if width * height > MAX_SOURCE_PIXELS:
                    raise ProfileImageError("The image dimensions are too large.")
                image.load()
                if getattr(image, "is_animated", False):
                    raise ProfileImageError("Animated images are not supported.")
                image.thumbnail(
                    (MAX_STORED_DIMENSION, MAX_STORED_DIMENSION),
                    Image.Resampling.LANCZOS,
                )
                if image_format == "JPEG" and image.mode not in {"RGB", "L"}:
                    image = image.convert("RGB")
                elif image_format == "PNG" and image.mode == "P":
                    image = image.convert("RGBA")
                elif image_format == "WEBP" and image.mode not in {"RGB", "RGBA"}:
                    image = image.convert(
                        "RGBA" if "A" in image.getbands() else "RGB"
                    )

                output = io.BytesIO()
                if image_format == "JPEG":
                    image.save(
                        output,
                        format="JPEG",
                        quality=88,
                        optimize=True,
                        progressive=True,
                    )
                elif image_format == "PNG":
                    image.save(output, format="PNG", optimize=True)
                else:
                    image.save(output, format="WEBP", quality=85, method=6)
    except ProfileImageError:
        raise
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        OSError,
        ValueError,
    ) as exc:
        raise ProfileImageError(
            "The selected file is not a valid JPG, PNG or WebP image."
        ) from exc

    extension = ALLOWED_FORMATS[image_format][0]
    return output.getvalue(), extension


def normalize_uploaded_image(uploaded_file):
    if uploaded_file.size > MAX_UPLOAD_BYTES:
        raise ProfileImageError("Choose an image smaller than 8 MB.")
    uploaded_file.seek(0)
    return normalize_image_bytes(uploaded_file.read(), MAX_UPLOAD_BYTES)


def _delete_stored_file(storage, name):
    if not name:
        return
    try:
        storage.delete(name)
    except Exception:
        logger.warning("A replaced profile image could not be removed.", exc_info=True)


def save_manual_profile_image(user, uploaded_file):
    content, extension = normalize_uploaded_image(uploaded_file)
    storage = user.profile_image.storage
    old_name = user.profile_image.name if user.profile_image else ""
    user.profile_image.save(
        f"manual-{uuid.uuid4().hex}.{extension}",
        ContentFile(content),
        save=False,
    )
    user.save(update_fields=["profile_image"])
    if old_name and old_name != user.profile_image.name:
        _delete_stored_file(storage, old_name)


def remove_profile_images(user):
    manual_image = user.profile_image
    storage = manual_image.storage if manual_image else None
    manual_name = manual_image.name if manual_image else ""
    user.profile_image = None
    user.save(update_fields=["profile_image"])
    if storage and manual_name:
        _delete_stored_file(storage, manual_name)


def _download_google_image(url):
    response = requests.get(
        url,
        timeout=GOOGLE_REQUEST_TIMEOUT,
        stream=True,
        allow_redirects=False,
        headers={"User-Agent": "GabswebCashFlow/1.0"},
    )
    try:
        if response.status_code != 200:
            raise ProfileImageError("Google profile image download failed.")
        content_type = (
            response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        )
        if content_type not in GOOGLE_CONTENT_TYPES:
            raise ProfileImageError("Google returned an unsupported image type.")
        declared_length = response.headers.get("Content-Length")
        if declared_length:
            try:
                if int(declared_length) > MAX_GOOGLE_BYTES:
                    raise ProfileImageError("Google returned an image that is too large.")
            except ValueError:
                pass

        chunks = []
        total = 0
        iter_content = getattr(response, "iter_content", None)
        if iter_content is None:
            chunks.append(response.content)
            total = len(response.content)
            if total > MAX_GOOGLE_BYTES:
                raise ProfileImageError("Google returned an image that is too large.")
        else:
            for chunk in iter_content(chunk_size=8192):
                if not chunk:
                    continue
                total += len(chunk)
                if total > MAX_GOOGLE_BYTES:
                    raise ProfileImageError("Google returned an image that is too large.")
                chunks.append(chunk)
        return b"".join(chunks)
    finally:
        close = getattr(response, "close", None)
        if close:
            close()


def import_google_profile_image(user, picture_url):
    if not user or not getattr(user, "pk", None):
        return False
    if not user.google_profile_image_enabled:
        return False
    if not is_google_picture_url(picture_url):
        return False
    if user.google_profile_image and user.google_profile_image_source == picture_url:
        return False

    try:
        downloaded = _download_google_image(picture_url)
        content, extension = normalize_image_bytes(downloaded, MAX_GOOGLE_BYTES)
    except Exception:
        logger.warning("A Google profile image could not be imported.", exc_info=True)
        return False

    storage = user.google_profile_image.storage
    old_name = user.google_profile_image.name if user.google_profile_image else ""
    try:
        user.google_profile_image.save(
            f"google-{uuid.uuid4().hex}.{extension}",
            ContentFile(content),
            save=False,
        )
        user.google_profile_image_source = picture_url
        user.save(
            update_fields=["google_profile_image", "google_profile_image_source"]
        )
    except Exception:
        logger.warning("A Google profile image could not be stored.", exc_info=True)
        return False

    if old_name and old_name != user.google_profile_image.name:
        _delete_stored_file(storage, old_name)
    return True
