"""Prepare the exact local model files listed in manifest.json."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

DEFAULT_MODEL_DIR = (
    ROOT / "intelligence/entityExtraction/models/ensemble-v1"
)


def file_matches(path, asset):
    if (
        not path.is_file()
        or path.stat().st_size != asset["size"]
    ):
        return False

    with path.open("rb") as stream:
        actual_hash = hashlib.file_digest(
            stream,
            "sha256",
        ).hexdigest()

    return actual_hash == asset["sha256"]


def cache_locations(custom_cache=None):
    locations = []

    if custom_cache is not None:
        custom_cache = Path(custom_cache).expanduser()

        if not custom_cache.is_dir():
            raise FileNotFoundError(
                f"Cache directory not found: {custom_cache}"
            )

        locations.append(custom_cache)

    locations.append(
        Path.home()
        / "Downloads/NER_Local_Trial_Windows/NER_Local_Trial/model_cache"
    )

    hf_base = Path(
        os.getenv("HF_HOME")
        or Path.home() / ".cache/huggingface"
    ).expanduser()

    locations.append(
        Path(
            os.getenv("HF_HUB_CACHE")
            or os.getenv("HUGGINGFACE_HUB_CACHE")
            or hf_base / "hub"
        ).expanduser()
    )

    return list(
        dict.fromkeys(
            path.resolve()
            for path in locations
            if path.is_dir()
        )
    )


def download_asset(asset, temporary):
    url = (
        "https://huggingface.co/"
        + asset["repo"]
        + "/resolve/"
        + asset["revision"]
        + "/"
        + asset["file"]
        + "?download=true"
    )

    for attempt in range(1, 4):
        try:
            print(
                f"DOWNLOADING {asset['path']} "
                f"({asset['size'] / 1024**2:.1f} MiB), "
                f"attempt {attempt}/3",
                flush=True,
            )

            with urllib.request.urlopen(
                url,
                timeout=120,
            ) as response:
                with temporary.open("wb") as output:
                    downloaded = 0
                    next_report = 64 * 1024**2

                    while block := response.read(4 * 1024**2):
                        output.write(block)
                        downloaded += len(block)

                        if downloaded >= next_report:
                            percent = (
                                100 * downloaded
                                / max(asset["size"], 1)
                            )
                            print(
                                f"  {percent:.1f}%",
                                flush=True,
                            )
                            next_report += 64 * 1024**2

            if not file_matches(temporary, asset):
                raise ValueError(
                    f"Checksum mismatch: {asset['path']}"
                )

            return

        except Exception as error:
            if attempt == 3:
                raise

            print(f"RETRY: {error}", flush=True)


def prepare(model_dir, custom_cache=None, offline=False):
    model_dir = Path(model_dir).expanduser()

    if not model_dir.is_absolute():
        model_dir = ROOT / model_dir

    model_dir = model_dir.resolve()

    manifest_path = model_dir / "manifest.json"

    manifest = json.loads(
        manifest_path.read_text(encoding="utf-8-sig")
    )

    assets = manifest.get("assets")

    if not isinstance(assets, list) or not assets:
        raise ValueError(
            "Manifest must contain a non-empty assets list."
        )

    caches = cache_locations(custom_cache)

    for cache in caches:
        print(f"CACHE: {cache}", flush=True)

    for asset in assets:
        for key in ("path", "file"):
            relative = Path(asset[key])

            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(
                    f"Invalid relative asset path: {asset[key]}"
                )

        if not re.fullmatch(
            r"[0-9a-f]{40}",
            asset["revision"],
        ):
            raise ValueError(
                "Each asset must use an exact commit revision."
            )

        if not re.fullmatch(
            r"[0-9a-f]{64}",
            asset["sha256"],
        ):
            raise ValueError(
                f"Invalid SHA-256: {asset['path']}"
            )

        target = model_dir / asset["path"]

        print(
            f"CHECKING {asset['path']}",
            flush=True,
        )

        if file_matches(target, asset):
            print(
                f"VERIFIED {asset['path']}",
                flush=True,
            )
            continue

        target.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        with tempfile.NamedTemporaryFile(
            dir=target.parent,
            prefix=target.name + ".",
            suffix=".part",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)

        try:
            reused = False

            for cache in caches:
                source = (
                    cache
                    / (
                        "models--"
                        + asset["repo"].replace("/", "--")
                    )
                    / "snapshots"
                    / asset["revision"]
                    / asset["file"]
                )

                if (
                    not source.is_file()
                    or source.stat().st_size != asset["size"]
                ):
                    continue

                print(
                    f"CHECKING CACHE {asset['path']}",
                    flush=True,
                )

                shutil.copyfile(source, temporary)

                if file_matches(temporary, asset):
                    temporary.replace(target)

                    print(
                        f"REUSED {asset['path']}",
                        flush=True,
                    )

                    reused = True
                    break

                print(
                    f"IGNORED INVALID CACHE {asset['path']}",
                    flush=True,
                )

            if reused:
                continue

            if offline:
                raise FileNotFoundError(
                    f"Missing verified asset: {asset['path']}"
                )

            download_asset(asset, temporary)
            temporary.replace(target)

            print(
                f"VERIFIED {asset['path']}",
                flush=True,
            )

        finally:
            temporary.unlink(missing_ok=True)

    print(
        f"MODEL DIRECTORY: {model_dir}",
        flush=True,
    )
    print("STEP 8 OK", flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__,
    )

    parser.add_argument(
        "--model-dir",
        type=Path,
        default=(
            os.getenv("MEDIA_ENTITY_MODEL_DIR")
            or DEFAULT_MODEL_DIR
        ),
    )

    parser.add_argument(
        "--reuse-hf-cache",
        type=Path,
    )

    parser.add_argument(
        "--offline",
        action="store_true",
    )

    args = parser.parse_args(argv)

    prepare(
        args.model_dir,
        args.reuse_hf_cache,
        args.offline,
    )

    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(
            encoding="utf-8",
            errors="replace",
        )

    try:
        raise SystemExit(main())

    except Exception as error:
        print(
            f"MODEL SETUP FAILED: {error}",
            file=sys.stderr,
        )
        raise SystemExit(1)