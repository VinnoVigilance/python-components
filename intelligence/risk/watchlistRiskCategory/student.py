"""CPU inference for the frozen watchlist v6 student."""

import hashlib
import json
import os
from pathlib import Path

from intelligence.risk.labelContract import RiskModelError, load_label_contract


DEFAULT_MODEL_DIR = Path(__file__).resolve().parent / "models/student-e5small-v6"
VICTIM_LABEL = "Victim or missing person"


class WatchlistStudent:
    """Load local assets once and score label/passage pairs without a teacher."""

    def __init__(self, model_path=None, threads=4):
        import torch
        import transformers
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        directory = Path(model_path or os.getenv("WATCHLIST_RISK_MODEL_DIR") or DEFAULT_MODEL_DIR).expanduser().resolve()
        self.contract = load_label_contract(directory)
        self.manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        if self.manifest.get("runtime_recipe") != "watchlist-paired-passages-v1":
            raise RiskModelError("Unsupported watchlist inference recipe.")
        actual = {"torch": torch.__version__.split("+")[0], "transformers": transformers.__version__}
        if actual != self.manifest["environment"]:
            raise RiskModelError(f"Watchlist runtime requires {self.manifest['environment']}; installed {actual}.")
        for name in ("config.json", "tokenizer.json", "tokenizer_config.json", "model.safetensors"):
            with (directory / name).open("rb") as stream:
                actual = hashlib.file_digest(stream, "sha256").hexdigest()
            if self.manifest["files_sha256"].get(name) != actual:
                raise RiskModelError(f"Watchlist asset does not match manifest: {name}.")
        self.policy = self.manifest["decision_policy"]
        required = {"comparison": ">=", "aggregation": "any_passage"}
        if any(self.policy.get(key) != value for key, value in required.items()):
            raise RiskModelError("Unsupported watchlist decision policy.")
        for key in ("text_threshold", "victim_threshold"):
            value = self.policy.get(key)
            if type(value) not in {int, float} or not 0 <= value <= 1:
                raise RiskModelError(f"Invalid {key}.")
        for key, minimum in (("context_chars", 0), ("max_tokens", 1), ("victim_record_chars", 1)):
            if type(self.policy.get(key)) is not int or self.policy[key] < minimum:
                raise RiskModelError(f"Invalid {key}.")
        text_labels = self.manifest["text_labels"]
        if (not text_labels or len(set(text_labels)) != len(text_labels)
                or not set(text_labels).issubset(self.contract.artifact_labels)):
            raise RiskModelError("Text labels must belong to the model label contract.")
        if threads < 1:
            raise ValueError("threads must be positive.")
        torch.set_num_threads(threads)
        self._torch = torch
        self.tokenizer = AutoTokenizer.from_pretrained(directory, local_files_only=True)
        self.model = AutoModelForSequenceClassification.from_pretrained(
            directory, local_files_only=True, num_labels=1,
        ).to("cpu").eval()
        if self.policy["max_tokens"] > self.model.config.max_position_embeddings:
            raise RiskModelError("Token budget exceeds the model's position limit.")
        self.model_version = self.manifest["model_id"] + ":" + self.manifest["files_sha256"]["model.safetensors"]
        self.policy_version = "sha256:" + hashlib.sha256(
            json.dumps(self.policy, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        self.artifact_version = "sha256:" + hashlib.sha256(
            (directory / "manifest.json").read_bytes()
        ).hexdigest()

    def score(self, labels, texts, batch_size=64):
        """Return uncalibrated sigmoid scores in input order."""
        if len(labels) != len(texts):
            raise ValueError("Each passage requires one label.")
        if batch_size < 1:
            raise ValueError("batch_size must be positive.")
        result = []
        with self._torch.inference_mode():
            for start in range(0, len(texts), batch_size):
                inputs = self.tokenizer(
                    labels[start:start + batch_size], texts[start:start + batch_size],
                    truncation="only_second", max_length=self.policy["max_tokens"],
                    padding=True, return_tensors="pt",
                )
                result.extend(self._torch.sigmoid(self.model(**inputs).logits[:, 0].float()).tolist())
        if any(not 0 <= value <= 1 for value in result):
            raise ValueError("Student returned an invalid score.")
        return result
