"""Encode notice JPEGs with the platform codec when the owner publishes them."""
import ctypes
import io
import os
from pathlib import Path
import subprocess


def _windows(data, width, height):
    script = Path(__file__).with_name("notice_jpeg.ps1")
    powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    # Pass the reviewed script text without changing the host's execution policy.
    command = script.read_text("ascii").replace("@WIDTH@", str(width)).replace("@HEIGHT@", str(height))
    try:
        result = subprocess.run(
            [str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
            input=data, capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("Windows could not convert the notice JPEG to PNG") from exc
    if result.returncode:
        raise ValueError("Windows could not decode the notice JPEG")
    return result.stdout


def _linux(data, original_size, size):
    try:
        gd = ctypes.CDLL("libgd.so.3")
    except OSError:
        try:
            from PIL import Image
        except ImportError as exc:
            raise ValueError("JPEG notices require libgd3 or Pillow on Linux. A PNG can be published directly.") from exc
        with Image.open(io.BytesIO(data)) as image:
            image.draft("RGB", size)
            image = image.convert("RGB")
            if image.size != size:
                image = image.resize(size, Image.Resampling.LANCZOS)
            target = io.BytesIO()
            image.save(target, "PNG")
            return target.getvalue()
    pointer, integer = ctypes.c_void_p, ctypes.c_int
    gd.gdImageCreateFromJpegPtrEx.argtypes = (integer, pointer, integer)
    gd.gdImageCreateFromJpegPtrEx.restype = pointer
    gd.gdImageDestroy.argtypes = (pointer,)
    gd.gdImageDestroy.restype = None
    gd.gdImageScale.argtypes = (pointer, ctypes.c_uint, ctypes.c_uint)
    gd.gdImageScale.restype = pointer
    gd.gdImagePngPtrEx.argtypes = (pointer, ctypes.POINTER(integer), integer)
    gd.gdImagePngPtrEx.restype = pointer
    gd.gdFree.argtypes = (pointer,)
    gd.gdFree.restype = None
    image = gd.gdImageCreateFromJpegPtrEx(len(data), data, 0)
    if not image:
        raise ValueError("Could not decode the notice JPEG")
    try:
        if original_size != size:
            scaled = gd.gdImageScale(image, *size)
            if not scaled:
                raise ValueError("Could not resize the notice JPEG")
            gd.gdImageDestroy(image)
            image = scaled
        length = integer()
        buffer = gd.gdImagePngPtrEx(image, ctypes.byref(length), 6)
        if not buffer:
            raise ValueError("Could not encode the notice PNG")
        try:
            if not 0 < length.value <= 4 * 1024 * 1024:
                raise ValueError("Converted notice image exceeds 4 MiB")
            return ctypes.string_at(buffer, length.value)
        finally:
            gd.gdFree(buffer)
    finally:
        gd.gdImageDestroy(image)


def encode(data, width, height):
    # Even incompressible RGB pixels at these bounds fit the 4 MiB player transport.
    ratio = min(1, 1770 / width, 732 / height)
    size = (max(1, round(width * ratio)), max(1, round(height * ratio)))
    if os.name == "nt":
        return _windows(data, *size)
    return _linux(data, (width, height), size)
