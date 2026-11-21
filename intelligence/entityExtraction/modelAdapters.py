"""Load one local CPU model and normalize its entity predictions."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

from .contract import MODEL_KEYS


PERSON = (
    "Names of real individual people, including personal aliases and surnames. "
    "Exclude titles and job roles."
)

ORGANIZATION = (
    "Proper names and abbreviations of companies, banks, government agencies, "
    "ministries, courts, universities, political parties and named groups. "
    "Exclude products, apps, places, laws and generic references such as "
    "the Commission."
)

LABEL_MAP = {
    "person": "INDIVIDUAL",
    "organization": "ORGANIZATION",
}


def configure_local_inference():
    """Disable optional usage telemetry before importing model libraries."""

    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["DO_NOT_TRACK"] = "1"

    from huggingface_hub import constants

    constants.HF_HUB_DISABLE_TELEMETRY = True

    try:
        import onnxruntime
    except ImportError:
        return

    onnxruntime.disable_telemetry_events()


class ModelAdapter:
    def __init__(
        self,
        key: str,
        model_root: Path,
        spec: dict,
    ):
        if key not in MODEL_KEYS:
            raise ValueError(f"Unknown entity model: {key}")

        self.key = key
        self.model = None
        self.tokenizer = None
        self._runtime = None

        model_root = Path(model_root)
        path = model_root / spec["directory"]

        if not path.is_dir():
            raise FileNotFoundError(
                f"Local model directory not found: {path}. "
                "Run prepare_entity_models.py first."
            )

        configure_local_inference()

        try:
            if key == "gliner25_policy":
                from gliner2 import AutoExtractor

                self.model = AutoExtractor.from_pretrained(
                    str(path),
                    map_location="cpu",
                    local_files_only=True,
                ).eval()

                self.tokenizer = self.model.processor.tokenizer

            elif key == "gliner21":
                from gliner import GLiNER

                # Supply local backbone files through a temporary view.
                backbone = model_root / "mdeberta"

                self._runtime = tempfile.TemporaryDirectory(
                    prefix="vv-ner-gliner21-",
                    ignore_cleanup_errors=True,
                )

                view = Path(self._runtime.name)

                config = json.loads(
                    (path / "gliner_config.json").read_text(
                        encoding="utf-8"
                    )
                )

                config["model_name"] = str(backbone.resolve())

                (view / "gliner_config.json").write_text(
                    json.dumps(config),
                    encoding="utf-8",
                )

                original = (
                    path / "model.safetensors"
                ).resolve()

                destination = view / "model.safetensors"

                try:
                    os.link(original, destination)

                except OSError:
                    try:
                        os.symlink(original, destination)

                    except OSError:
                        shutil.copyfile(original, destination)

                for name in (
                    "config.json",
                    "spm.model",
                ):
                    shutil.copyfile(
                        backbone / name,
                        view / name,
                    )

                tokenizer_config = json.loads(
                    (backbone / "tokenizer_config.json").read_text(
                        encoding="utf-8"
                    )
                )

                # Preserve the original SentencePiece tokenizer.
                tokenizer_config["fix_mistral_regex"] = False

                (view / "tokenizer_config.json").write_text(
                    json.dumps(tokenizer_config),
                    encoding="utf-8",
                )

                self.model = GLiNER.from_pretrained(
                    str(view),
                    map_location="cpu",
                    load_tokenizer=True,
                    local_files_only=True,
                    low_cpu_mem_usage=True,
                ).eval()

                self.tokenizer = (
                    self.model.data_processor.transformer_tokenizer
                )

            elif key == "xlmr":
                from transformers import (
                    AutoModelForTokenClassification,
                    AutoTokenizer,
                    pipeline,
                )

                self.tokenizer = AutoTokenizer.from_pretrained(
                    str(path),
                    use_fast=True,
                    local_files_only=True,
                    fix_mistral_regex=False,
                )

                model = (
                    AutoModelForTokenClassification.from_pretrained(
                        str(path),
                        local_files_only=True,
                    )
                    .float()
                    .eval()
                )

                self.model = pipeline(
                    "token-classification",
                    model=model,
                    tokenizer=self.tokenizer,
                    aggregation_strategy="simple",
                    device=-1,
                )

            if (
                not getattr(self.tokenizer, "is_fast", False)
                or len(self.tokenizer) < 100000
            ):
                raise RuntimeError(
                    "The multilingual tokenizer is missing or incompatible."
                )

        except BaseException:
            self.close()
            raise

    def extract(self, text: str) -> list[dict]:
        if self.model is None:
            raise RuntimeError(
                "This model adapter is not loaded."
            )

        if self.key == "gliner25_policy":
            raw = self.model.extract_entities(
                text,
                {
                    "person": PERSON,
                    "organization": ORGANIZATION,
                },
                threshold=0.2,
                include_spans=True,
                include_confidence=True,
            )

            return [
                {
                    "start": int(item["start"]),
                    "end": int(item["end"]),
                    "type": LABEL_MAP[label],
                    "score": float(item["confidence"]),
                }
                for label, items in raw["entities"].items()
                if label in LABEL_MAP
                for item in items
            ]

        if self.key == "gliner21":
            raw = self.model.predict_entities(
                text,
                ["person", "organization"],
                threshold=0.2,
            )

            return [
                {
                    "start": int(item["start"]),
                    "end": int(item["end"]),
                    "type": LABEL_MAP[item["label"]],
                    "score": float(item["score"]),
                }
                for item in raw
                if item["label"] in LABEL_MAP
            ]

        mapping = {
            "PER": "INDIVIDUAL",
            "PERSON": "INDIVIDUAL",
            "ORG": "ORGANIZATION",
        }

        return [
            {
                "start": int(item["start"]),
                "end": int(item["end"]),
                "type": mapping[item["entity_group"]],
                "score": float(item["score"]),
            }
            for item in self.model(text)
            if item["entity_group"] in mapping
        ]

    def close(self):
        self.model = None
        self.tokenizer = None

        if self._runtime is not None:
            self._runtime.cleanup()
            self._runtime = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()