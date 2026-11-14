"""Download the public ZONOS3-Mini runtime bundle into the ignored data directory."""

import hashlib
import shutil
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import quote
from urllib.request import urlopen


SOURCE = "https://huggingface.co/Zyphra/ZONOS3-Mini/resolve/d2d142a86b1e6aeb4252679673c86ecd86177bf8"
DESTINATION = Path(__file__).resolve().parents[1] / "data" / "zonos"
FILES = (
    (
        "gguf/backbone-ptq.gguf", "backbone-ptq.gguf",
        "66fceb6d73a8c30bde80249e4112bb308380ab7f3384fa11c8e4048115e4ceec",
    ),
    (
        "gguf/zac-keep2.gguf", "zac-keep2.gguf",
        "834e5b70b88294afc3c702a478487e0664dcc6b574c16fc6167d72444aabeafd",
    ),
    (
        "gguf/voices/English/Savannah_en-American_F_1.embedding.npy", "voices/Savannah.npy",
        "dd81587e66f96fb62da47ef13d5d867de464ca386a83f01b4403cdd19629ca6c",
    ),
)


def checksum(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def download(source, target, expected):
    if target.is_file() and checksum(target) == expected:
        print(f"Verified {target.relative_to(DESTINATION)}", flush=True)
        return

    target.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {target.relative_to(DESTINATION)}", flush=True)
    with NamedTemporaryFile(dir=target.parent, suffix=".part", delete=False) as output:
        temporary_path = Path(output.name)
        try:
            with output, urlopen(f"{SOURCE}/{quote(source)}?download=true", timeout=90) as response:
                shutil.copyfileobj(response, output, length=1024 * 1024)
            if checksum(temporary_path) != expected:
                raise ValueError(f"Unexpected SHA-256 for {source}")
            temporary_path.replace(target)
        finally:
            temporary_path.unlink(missing_ok=True)


if __name__ == "__main__":
    for remote_path, local_path, digest in FILES:
        download(remote_path, DESTINATION / local_path, digest)
