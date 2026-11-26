import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

import config

ROOT = Path(__file__).resolve().parent
FFMPEG_DIR = ROOT / "ffmpeg"
FFMPEG_EXE = config.FFMPEG_PATH
MODEL_CONFIG = Path(str(config.TTS_MODEL_PATH) + ".json")
FFMPEG_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"


def install_ffmpeg():
    if FFMPEG_EXE.is_file():
        return
    if sys.platform != "win32":
        raise RuntimeError("FFmpeg not found. Install it with your package manager (e.g. `sudo apt install ffmpeg`) and re-run.")
    FFMPEG_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="void-ffmpeg-") as temp_dir:
        archive_path = Path(temp_dir) / "ffmpeg.zip"
        print("Downloading the Windows FFmpeg build (one-time setup)...")
        urllib.request.urlretrieve(FFMPEG_URL, archive_path)
        with zipfile.ZipFile(archive_path) as archive:
            members = archive.infolist()
            binary_members = [item for item in members if "/bin/" in item.filename.replace("\\", "/") and not item.is_dir()]
            license_members = [item for item in members if not item.is_dir() and "/bin/" not in item.filename.replace("\\", "/") and Path(item.filename).name.casefold() in {"license.txt", "readme.txt", "copying.txt"}]
            if not any(Path(item.filename).name.casefold() == "ffmpeg.exe" for item in binary_members):
                raise RuntimeError("The downloaded FFmpeg archive did not contain ffmpeg.exe.")
            for item in binary_members + license_members:
                normalized = item.filename.replace("\\", "/")
                parts = normalized.split("/")
                if "bin" in parts:
                    relative = Path("bin", *parts[parts.index("bin") + 1:])
                else:
                    relative = Path("licenses", Path(normalized).name)
                target = (FFMPEG_DIR / relative).resolve()
                if not target.is_relative_to(FFMPEG_DIR.resolve()):
                    raise RuntimeError("Unexpected path in the FFmpeg archive.")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(item))
    if not FFMPEG_EXE.is_file():
        raise RuntimeError("FFmpeg installation did not produce ffmpeg.exe.")


def install_voice_model():
    if config.TTS_MODEL_PATH.is_file() and MODEL_CONFIG.is_file():
        return
    config.TTS_MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    print("Downloading the offline English Piper voice model (one-time setup)...")
    subprocess.run(
        [sys.executable, "-m", "piper.download_voices", "en_US-lessac-medium", "--data-dir", str(config.TTS_MODEL_PATH.parent)],
        cwd=ROOT,
        check=True,
    )
    if not config.TTS_MODEL_PATH.is_file() or not MODEL_CONFIG.is_file():
        raise RuntimeError("Piper did not install the expected voice model files.")


if __name__ == "__main__":
    try:
        install_ffmpeg()
        install_voice_model()
        print("TTS voice model and FFmpeg are ready.")
    except (OSError, RuntimeError, subprocess.CalledProcessError, zipfile.BadZipFile) as error:
        print(f"Asset setup failed: {error}")
        print("Check your internet connection and run setup.bat again.")
        raise SystemExit(1)
