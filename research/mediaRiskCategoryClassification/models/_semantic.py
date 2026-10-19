"""Frozen CPU encoders; exact token windows, masked mean pooling, no target input."""
from importlib.metadata import version

import numpy as np
from sklearn.base import BaseEstimator, TransformerMixin
from sklearn.utils.validation import check_is_fitted


def token_windows(token_ids, content_budget):
    """Disjoint windows cover every content token, including the final short tail."""
    if content_budget < 1:
        raise ValueError('No space remains for article tokens.')
    if not token_ids:
        yield []
    else:
        for start in range(0, len(token_ids), content_budget):
            yield token_ids[start:start + content_budget]


def normalize_rows(values):
    return values / np.maximum(np.linalg.norm(values, axis=1, keepdims=True), 1e-12)


class FrozenTextEncoder(TransformerMixin, BaseEstimator):
    """A 384-d article vector, with no fitting or fine-tuning of encoder weights.

    Normalize each chunk embedding, average by its number of content tokens,
    then normalize the article embedding. No disk embedding cache is used.
    Loaded weights are excluded from joblib; the pinned HF snapshot is needed
    again on reload (automatically downloaded if absent from the HF cache).
    """

    def __init__(self, model_id, revision, max_length, prefix='', batch_size=16,
                 torch_threads=4):
        self.model_id = model_id
        self.revision = revision
        self.max_length = max_length
        self.prefix = prefix
        self.batch_size = batch_size
        self.torch_threads = torch_threads

    def _load(self):
        if hasattr(self, '_backbone'):
            self._torch.set_num_threads(self.torch_threads)
            return
        try:
            import torch
            from transformers import AutoModel, AutoTokenizer
        except ImportError as exc:
            raise RuntimeError(
                'Semantic model dependencies are missing or incompatible. '
                'See README.md for the CPU installation commands. '
                f'Detail: {exc}') from exc
        if self.batch_size < 1 or self.torch_threads < 1:
            raise ValueError('batch_size and torch_threads must be positive.')
        if len(self.revision) != 40:
            raise ValueError('Use a full pinned Hugging Face commit SHA.')
        torch.set_num_threads(self.torch_threads)
        print(f'Loading frozen CPU encoder: {self.model_id} @ {self.revision[:12]}', flush=True)
        tokenizer = AutoTokenizer.from_pretrained(
            self.model_id, revision=self.revision, trust_remote_code=False)
        backbone = AutoModel.from_pretrained(
            self.model_id, revision=self.revision, trust_remote_code=False,
            use_safetensors=True).to(device='cpu', dtype=torch.float32)
        backbone.requires_grad_(False).eval()
        if self.max_length > backbone.config.max_position_embeddings:
            raise ValueError('Configured token window exceeds the encoder position limit.')
        self._torch, self._tokenizer, self._backbone = torch, tokenizer, backbone

    def fit(self, X, y=None):
        # y is deliberately never passed to the pretrained encoder.
        self._load()
        self.n_features_out_ = int(self._backbone.config.hidden_size)
        self.encoding_passes_ = []
        self.fitted_ = True
        return self

    def _iter_chunks(self, texts):
        tokenizer = self._tokenizer
        # Both pinned checkpoints use BERT's [CLS] ... [SEP] input format.
        # Transformers 5 removed build_inputs_with_special_tokens; use the
        # tokenizer's actual special-token IDs without decoding token windows.
        if (tokenizer.num_special_tokens_to_add(pair=False) != 2
                or tokenizer.cls_token_id is None or tokenizer.sep_token_id is None):
            raise ValueError('These encoders require BERT CLS/SEP special tokens.')
        prefix_ids = tokenizer.encode(self.prefix, add_special_tokens=False)
        budget = (self.max_length - tokenizer.num_special_tokens_to_add(pair=False)
                  - len(prefix_ids))
        for row, text in enumerate(texts):
            if not isinstance(text, str):
                raise ValueError('Encoder input must contain article text strings only.')
            content = tokenizer.encode(text, add_special_tokens=False,
                                       truncation=False, verbose=False)
            for index, part in enumerate(token_windows(content, budget)):
                ids = [tokenizer.cls_token_id] + prefix_ids + part + [tokenizer.sep_token_id]
                if len(ids) > self.max_length:
                    raise ValueError('Chunk exceeds token budget; no silent truncation allowed.')
                is_last = (index + 1) * budget >= len(content)
                yield row, max(len(part), 1), {
                    'input_ids': ids, 'attention_mask': [1] * len(ids)}, is_last

    def _encode_batch(self, features):
        torch = self._torch
        batch = self._tokenizer.pad(features, padding=True, return_tensors='pt')
        with torch.inference_mode():
            hidden = self._backbone(**batch).last_hidden_state
            mask = batch['attention_mask'].unsqueeze(-1).to(hidden.dtype)
            pooled = (hidden * mask).sum(dim=1) / mask.sum(dim=1).clamp(min=1)
            return torch.nn.functional.normalize(pooled, p=2, dim=1).cpu().numpy()

    def transform(self, X):
        check_is_fitted(self, 'fitted_')
        if isinstance(X, str):
            raise ValueError('Pass a list of articles, not a single string.')
        texts = list(X)
        self._load()
        from tqdm import tqdm
        sums = np.zeros((len(texts), self.n_features_out_), dtype=np.float64)
        counts = np.zeros(len(texts), dtype=np.int64)
        total_weights = np.zeros(len(texts), dtype=np.int64)
        batch, owners, weights, completions = [], [], [], []

        def flush():
            vectors = self._encode_batch(batch)
            np.add.at(sums, owners, vectors * np.asarray(weights)[:, None])
            np.add.at(total_weights, owners, weights)
            np.add.at(counts, owners, 1)
            progress.set_postfix(chunks=int(counts.sum()), refresh=False)
            # An article advances only after its final chunk was encoded.
            # miniters=0 also refreshes elapsed time for a long article.
            progress.update(sum(completions))
            batch.clear()
            owners.clear()
            weights.clear()
            completions.clear()

        print(f'Encoding {len(texts)} articles; all token windows, batch={self.batch_size}', flush=True)
        with tqdm(total=len(texts), desc='Encoding', unit='article', ascii=True,
                  dynamic_ncols=True, mininterval=2.0, miniters=0) as progress:
            for owner, weight, feature, is_last in self._iter_chunks(texts):
                batch.append(feature)
                owners.append(owner)
                weights.append(weight)
                completions.append(is_last)
                if len(batch) == self.batch_size:
                    flush()
            if batch:
                flush()
        result = normalize_rows(sums / np.maximum(total_weights[:, None], 1)).astype(np.float32)
        if not np.isfinite(result).all():
            raise ValueError('Non-finite article embeddings.')
        stats = {'articles': len(texts), 'chunks': int(counts.sum()),
                 'max_chunks_per_article': int(counts.max()) if len(counts) else 0}
        self.encoding_passes_.append(stats)
        print(f"Encoded {stats['chunks']} chunks for {stats['articles']} articles.", flush=True)
        return result

    def runtime_metadata(self):
        return {
            'model_id': self.model_id, 'revision': self.revision,
            'device': 'cpu', 'dtype': 'float32', 'frozen': True,
            'embedding_dimension': self.n_features_out_,
            'max_tokens_including_prefix_and_special_tokens': self.max_length,
            'prefix': self.prefix, 'overlap_tokens': 0,
            'pooling': 'masked mean -> chunk L2 -> content-token-weighted mean -> article L2',
            'encoding_passes': self.encoding_passes_,
            'versions': {name: version(name) for name in
                         ('torch', 'transformers', 'tokenizers', 'safetensors', 'huggingface-hub')},
            'encoder_weights_in_joblib': False,
            'encoder_parameter_bytes': sum(p.numel() * p.element_size()
                                           for p in self._backbone.parameters()),
        }

    def __getstate__(self):
        state = super().__getstate__().copy()
        for key in ('_torch', '_tokenizer', '_backbone'):
            state.pop(key, None)
        return state
