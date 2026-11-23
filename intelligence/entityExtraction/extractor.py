"""Run the local models, vote, group names and format the result."""

from __future__ import annotations

import contextlib
import copy
import gc
import hashlib
import importlib.metadata
import json
import logging
import os
import threading
import time
from pathlib import Path

from .contract import (
    MODEL_KEYS,
    EntityExtractionError,
    ExtractionResult,
)
from .grouping import group_mentions
from .text import (
    collect_mentions,
    prepare_news,
    validate_mention,
)
from .voting import accept_votes


ROOT_DIR = Path(__file__).resolve().parents[2]

DEFAULT_MODEL_DIR = (
    Path(__file__).parent / "models" / "ensemble-v1"
)

logger = logging.getLogger(__name__)


def _digest_file(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(
            stream,
            "sha256",
        ).hexdigest()


def _read_manifest(directory):
    try:
        manifest = json.loads(
            (directory / "manifest.json").read_text(
                encoding="utf-8-sig"
            )
        )

        expected_policy = {
            "minimum_votes": 2,
            "voting_score_cutoff": 0.8,
            "candidate_decode_threshold": 0.2,
            "chunk_tokens": 320,
            "chunk_overlap": 64,
            "grouping": "conservative",
            "gliner25_policy_descriptions": True,
        }

        if manifest["schemaVersion"] != "1.0":
            raise ValueError(
                "Unsupported manifest schema version."
            )

        if set(manifest["models"]) != set(MODEL_KEYS):
            raise ValueError(
                "The manifest must specify the three selected models."
            )

        for key, value in expected_policy.items():
            if manifest["policy"][key] != value:
                raise ValueError(
                    f"Unsupported model policy: {key}"
                )

        if not manifest["assets"]:
            raise ValueError("Model asset list is empty.")

        for asset in manifest["assets"]:
            relative = Path(asset["path"])

            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(
                    "Model asset paths must be relative."
                )

        return manifest

    except (OSError, ValueError, KeyError, TypeError) as error:
        raise EntityExtractionError(
            f"Cannot read entity model manifest at {directory}: "
            f"{error}"
        ) from error


class EntityExtractor:
    """Reuse each CPU model across a bounded batch of news."""

    def __init__(
        self,
        model_dir=None,
        threads=4,
        *,
        adapter_factory=None,
    ):
        if (
            not isinstance(threads, int)
            or isinstance(threads, bool)
            or threads < 1
        ):
            raise ValueError(
                "threads must be a positive integer."
            )

        directory = Path(
            model_dir
            or os.getenv("MEDIA_ENTITY_MODEL_DIR")
            or DEFAULT_MODEL_DIR
        ).expanduser()

        self.model_dir = (
            directory
            if directory.is_absolute()
            else ROOT_DIR / directory
        ).resolve()

        self._manifest = _read_manifest(self.model_dir)
        self.threads = threads
        self._adapter_factory = adapter_factory
        self._assets_checked = False
        self._model_info = {}
        self._last_news_model_seconds = []
        self._lock = threading.Lock()
        self._closed = False

        self._versions = {}

        for package in self._manifest["runtime"]:
            try:
                self._versions[package] = (
                    importlib.metadata.version(package)
                )
            except importlib.metadata.PackageNotFoundError:
                self._versions[package] = "NOT_INSTALLED"

        digest = hashlib.sha256()

        digest.update(
            json.dumps(
                self._manifest,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        )

        digest.update(
            json.dumps(
                self._versions,
                sort_keys=True,
            ).encode()
        )

        for filename in (
            "extractor.py",
            "modelAdapters.py",
            "text.py",
            "voting.py",
            "grouping.py",
            "contract.py",
        ):
            digest.update(filename.encode())
            digest.update(
                (Path(__file__).parent / filename).read_bytes()
            )

        self.calculation_version = (
            "sha256:" + digest.hexdigest()
        )

    @property
    def metadata(self):
        return {
            "calculationVersion": self.calculation_version,
            "device": "cpu",
            "threads": self.threads,
            "versions": dict(self._versions),
            "models": copy.deepcopy(self._model_info),
            "newsModelSeconds": list(
                self._last_news_model_seconds
            ),
            "loadingPolicy": (
                "one model at a time, once per bounded batch"
            ),
        }

    def _verify_assets(self):
        if self._assets_checked:
            return

        if self._adapter_factory is None:
            for package, required in self._manifest["runtime"].items():
                actual = self._versions[package]

                if actual.split("+")[0] != required.split("+")[0]:
                    raise EntityExtractionError(
                        f"{package} requires {required}; "
                        f"installed {actual}. "
                        "Install this project's requirements.txt."
                    )

        logger.info(
            "Verifying local entity model files."
        )

        for asset in self._manifest["assets"]:
            path = self.model_dir / asset["path"]

            if (
                not path.is_file()
                or path.stat().st_size != asset["size"]
                or _digest_file(path) != asset["sha256"]
            ):
                raise EntityExtractionError(
                    f"Missing or changed model asset: {path}. "
                    "Run scripts/adverseMedia/prepare_entity_models.py."
                )

        self._assets_checked = True

    def _run_models(self, rows):
        factory = self._adapter_factory
        inference_context = contextlib.nullcontext

        if factory is None:
            import torch
            from .modelAdapters import ModelAdapter

            torch.set_num_threads(self.threads)
            factory = ModelAdapter
            inference_context = torch.inference_mode

        policy = self._manifest["policy"]
        predictions = {}
        timings = {}

        for key in MODEL_KEYS:
            adapter = None

            try:
                started = time.monotonic()
                spec = self._manifest["models"][key]

                if self._adapter_factory is None:
                    with torch.random.fork_rng(devices=[]):
                        torch.manual_seed(42)
                        adapter = factory(
                            key,
                            self.model_dir,
                            spec,
                        )
                else:
                    adapter = factory(
                        key,
                        self.model_dir,
                        spec,
                    )

                duration = time.monotonic() - started
                previous = self._model_info.get(key, {})

                self._model_info[key] = {
                    "repo": spec["repo"],
                    "revision": spec["revision"],
                    "lastLoadSeconds": duration,
                    "totalLoadSeconds": (
                        previous.get("totalLoadSeconds", 0)
                        + duration
                    ),
                    "tokenizerVocabulary": len(adapter.tokenizer),
                    "loadCount": previous.get("loadCount", 0) + 1,
                }

                logger.info(
                    "Entity model loaded: model=%s "
                    "seconds=%.2f batch_news=%s",
                    key,
                    duration,
                    len(rows),
                )

                predictions[key] = []
                timings[key] = []

                for row in rows:
                    started = time.monotonic()

                    with inference_context():
                        mentions = collect_mentions(
                            row,
                            adapter,
                            chunk_tokens=policy["chunk_tokens"],
                            chunk_overlap=policy["chunk_overlap"],
                        )

                    predictions[key].append(mentions)

                    timings[key].append(
                        time.monotonic() - started
                    )

                logger.info(
                    "Entity model completed: model=%s",
                    key,
                )

            except Exception as error:
                raise EntityExtractionError(
                    f"Model load/inference failed: {error}",
                    model_key=key,
                ) from error

            finally:
                if adapter is not None:
                    adapter.close()
                    del adapter

                gc.collect()

        return predictions, timings

    def extract(
        self,
        *,
        title: str | None,
        body: str | None,
    ) -> ExtractionResult:
        return self.extract_many([(title, body)])[0]

    def extract_many(
        self,
        articles,
        *,
        batch_size=32,
    ) -> list[ExtractionResult]:
        if (
            not isinstance(batch_size, int)
            or isinstance(batch_size, bool)
            or not 1 <= batch_size <= 128
        ):
            raise ValueError(
                "batch_size must be an integer between 1 and 128."
            )

        rows = [
            prepare_news(title, body)
            for title, body in articles
        ]

        with self._lock:
            if self._closed:
                raise EntityExtractionError(
                    "This extraction service has been closed."
                )

            results = [
                {
                    "status": "SKIPPED_EMPTY_INPUT",
                    "schemaVersion": "1.0",
                    "calculationVersion": self.calculation_version,
                    "entities": [],
                    "unresolvedNameLinks": [],
                }
                for _ in rows
            ]

            self._last_news_model_seconds = [
                0.0 for _ in rows
            ]

            for offset in range(0, len(rows), batch_size):
                active = [
                    (index, row)
                    for index, row in enumerate(
                        rows[offset:offset + batch_size],
                        offset,
                    )
                    if row["title"].strip() or row["body"].strip()
                ]

                if not active:
                    continue

                self._verify_assets()

                predictions, timings = self._run_models(
                    [row for _, row in active]
                )

                for position, (index, row) in enumerate(active):
                    per_model = {
                        key: predictions[key][position]
                        for key in MODEL_KEYS
                    }

                    results[index] = self._format_result(
                        row,
                        per_model,
                    )

                    self._last_news_model_seconds[index] = sum(
                        timings[key][position]
                        for key in MODEL_KEYS
                    )

            return results

    def _format_result(self, row, per_model):
        policy = self._manifest["policy"]

        accepted = accept_votes(
            per_model,
            policy["voting_score_cutoff"],
        )

        candidates = [
            mention
            for values in per_model.values()
            for mention in values
        ]

        groups, unresolved = group_mentions(
            row,
            accepted,
            candidates,
            mode=policy["grouping"],
        )

        result = {
            "status": "COMPLETED",
            "schemaVersion": "1.0",
            "calculationVersion": self.calculation_version,
            "entities": [],
            "unresolvedNameLinks": [],
        }

        for group in groups:
            mentions = []

            for mention in group["mentions"]:
                validate_mention(row, mention)

                mentions.append({
                    "text": mention["name"],
                    "field": mention["field"],
                    "start": mention["field_start"],
                    "end": mention["field_end"],
                    "evidenceSentence": mention["evidence_sentence"],
                    "votes": mention["votes"],
                    "models": mention["models"],
                    "scores": mention["source_scores"],
                })

            result["entities"].append({
                "entityType": group["entity_type"],
                "memberName": group["name"],
                "aliases": group["aliases"],
                "mentions": mentions,
                "groupingEvidence": [
                    {
                        "left": link["left"],
                        "right": link["right"],
                        "rule": link["rule"],
                        "evidenceSentence": link["evidence"],
                        "explanation": link.get("explanation", ""),
                    }
                    for link in group["grouping_evidence"]
                ],
            })

        for item in unresolved:
            record = {
                key: value
                for key, value in item.items()
                if key != "evidence"
            }

            if "evidence" in item:
                record["evidenceSentence"] = item["evidence"]

            result["unresolvedNameLinks"].append(record)

        return result

    def close(self):
        with self._lock:
            self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()