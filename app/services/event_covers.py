"""Bounded image validation and app-owned event-cover storage."""
from io import BytesIO
from pathlib import Path
import re
from uuid import uuid4
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError

COVER_DIR = Path(__file__).resolve().parents[1] / 'static/uploads/event-covers'
URL_PREFIX = '/static/uploads/event-covers/'
DEFAULT_COVER = '/static/img/home-bg.jpg'
MAX_BYTES = 5 * 1024 * 1024
MAX_PIXELS = 20_000_000
NAME = re.compile(r'event-cover-[0-9a-f]{32}\.webp')


class CoverValidationError(ValueError):
    pass


def validate_cover(upload):
    if upload is None or not upload.filename:
        return None
    extensions = {'.jpg': 'JPEG', '.jpeg': 'JPEG', '.png': 'PNG', '.webp': 'WEBP'}
    expected = extensions.get(Path(upload.filename).suffix.lower())
    if expected is None or upload.content_type not in ('image/jpeg', 'image/png', 'image/webp'):
        raise CoverValidationError('admin.error.event_cover_type')
    data = upload.file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise CoverValidationError('admin.error.event_cover_size')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                if image.format != expected or Image.MIME.get(image.format) != upload.content_type:
                    raise CoverValidationError('admin.error.event_cover_type')
                if image.width * image.height > MAX_PIXELS or getattr(image, 'n_frames', 1) != 1:
                    raise CoverValidationError('admin.error.event_cover_invalid')
                image.verify()
            with Image.open(BytesIO(data)) as image:
                image.load()
                normalized = ImageOps.exif_transpose(image).convert('RGB')
                output = BytesIO()
                normalized.save(output, format='WEBP', quality=85)
                return output.getvalue()
    except CoverValidationError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError,
            Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise CoverValidationError('admin.error.event_cover_invalid') from exc


def cover_image_url(value):
    if isinstance(value, str) and value.startswith(URL_PREFIX) and NAME.fullmatch(value[len(URL_PREFIX):]):
        return value
    return DEFAULT_COVER


def save_cover(data):
    if data is None:
        return None
    COVER_DIR.mkdir(parents=True, exist_ok=True)
    name = f'event-cover-{uuid4().hex}.webp'
    path = COVER_DIR / name
    # Exclusive creation prevents accidental overwrites, including collisions.
    created = False
    try:
        with path.open('xb') as stream:
            created = True
            stream.write(data)
    except Exception:
        if created:
            path.unlink(missing_ok=True)
        raise
    return URL_PREFIX + name


def delete_cover(value):
    if not value or cover_image_url(value) == DEFAULT_COVER:
        return
    try:
        path = COVER_DIR / value[len(URL_PREFIX):]
        # Never follow a DB-provided path or a symlink outside owned storage.
        if path.is_symlink() or path.resolve().parent != COVER_DIR.resolve():
            return
        path.unlink(missing_ok=True)
    except OSError:
        # Cleanup must not undo a committed event deletion.
        import logging
        logging.getLogger(__name__).warning('Unable to remove an event cover file')
