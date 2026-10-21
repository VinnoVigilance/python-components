"""CPU student: a small encoder trained to copy the frozen checker's P(yes) for each flagged (piece, label) and the
victim question, so the server needs no LLM."""
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import torch

from checker import cool_down
from highlighter import EXTRA_TRIGGERS, TAXONOMY_TRIGGERS, compile_triggers, flag_units, split_units

RESEARCH = Path(__file__).resolve().parents[1]
FRESH = RESEARCH / "cache" / "fresh"
FROZEN = json.load(open(RESEARCH / "results" / "frozen_versions.json", encoding="utf-8"))
ORDER = ["random300", "heldout_list", "rest"]
VICTIM = "Victim or missing person"
CONTEXT, MAX_LEN, RECORD_CHARS = 150, 256, 1200


VERSIONS = {"v4": ("checker_answers.csv", "split_final.csv"), "v5": ("checker_answers_v5.csv", "split_v5.csv"),
            "v6": ("checker_answers_v6.csv", "split_v5.csv")}
TRAIN_SPLITS = {"v4": ["training"], "v5": ["training", "check_pool"], "v6": ["training", "check_pool"]}


def pieces(version="v4"):
    """Every flagged (record, piece, label) of the fresh records, in the frozen checker's order, with the checker's
    P(yes); plus one victim row per flagged record with the checker's P(victim)."""
    answers_file, split_file = VERSIONS[version]
    split = pd.read_csv(FRESH / "split.csv", dtype=str, keep_default_na=False)
    final = pd.read_csv(FRESH / split_file, dtype=str, keep_default_na=False).set_index("record_key")["split"]
    split = split[split["split"].isin(ORDER)].copy()
    split["order"] = split["split"].map(ORDER.index)
    split = split.sort_values(["order", "list_name", "record_key"])
    scope = set(FROZEN["scope_labels"])
    pick = lambda lexicon: {k: v for k, v in lexicon.items() if k in scope}
    triggers = compile_triggers(pick(TAXONOMY_TRIGGERS), pick(EXTRA_TRIGGERS))
    rows = []
    for key, text, source in zip(split["record_key"], split["text"], split["list_name"]):
        units = split_units(text)
        for k, label, _ in flag_units(units, triggers, source=source if version >= "v5" else None,
                                    version="v6" if version >= "v6" else "v5"):
            before = units[k - 1][1][-CONTEXT:] if k else ""
            after = units[k + 1][1][:CONTEXT] if k + 1 < len(units) else ""
            rows.append({"record_key": key, "unit_index": k, "label": label,
                         "text": f"{units[k][0]}: {units[k][1]} || {before} [...] {after}"})
    q = pd.DataFrame(rows)
    answers = pd.read_csv(FRESH / answers_file, dtype={"record_key": str})
    q = q.merge(answers[["record_key", "unit_index", "label", "p_yes"]], how="left", on=["record_key", "unit_index", "label"])
    assert q["p_yes"].notna().all() and len(q) == len(answers)
    q = q.rename(columns={"p_yes": "target"})
    role = pd.read_csv(FRESH / "checker_role.csv", dtype={"record_key": str})
    body = split.set_index("record_key")["text"].str.replace(r"^Source list:.*\n", "", regex=True).str[:RECORD_CHARS]
    v = pd.DataFrame({"record_key": role["record_key"], "unit_index": -1, "label": VICTIM,
                      "text": role["record_key"].map(body).values, "target": role["p_victim"].values})
    out = pd.concat([q, v], ignore_index=True)
    out["split"] = out["record_key"].map(final)
    return out


class Student:
    def __init__(self, path, device="cpu", threads=None):
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        if threads:
            torch.set_num_threads(threads)
        self.tok = AutoTokenizer.from_pretrained(path)
        self.model = AutoModelForSequenceClassification.from_pretrained(path, num_labels=1).to(device).eval()
        self.device = device

    def encode(self, labels, texts):
        return self.tok(list(labels), list(texts), truncation="only_second", max_length=MAX_LEN, padding=True,
                        return_tensors="pt").to(self.device)

    @torch.no_grad()
    def p_yes(self, labels, texts, batch_size=64):
        out = []
        for s in range(0, len(texts), batch_size):
            if self.device != "cpu" and s % (batch_size * 20) == 0:
                cool_down()
            enc = self.encode(labels[s:s + batch_size], texts[s:s + batch_size])
            out += torch.sigmoid(self.model(**enc).logits[:, 0].float()).cpu().tolist()
        return np.array(out)


def train(base, out_dir, epochs=3, lr=3e-5, batch_size=32, seed=42, version="v4"):
    """Fine-tune on the training split (soft targets), keeping 10% of training people aside to watch agreement."""
    torch.manual_seed(seed)
    data = pieces(version)
    tr = data[data["split"].isin(TRAIN_SPLITS[version])].reset_index(drop=True)
    people = tr["record_key"].drop_duplicates().sample(frac=0.1, random_state=seed)
    val, tr = tr[tr["record_key"].isin(people)], tr[~tr["record_key"].isin(people)]
    s = Student(base, device="cuda:0")
    s.model.train()
    opt = torch.optim.AdamW(s.model.parameters(), lr=lr, weight_decay=0.01)
    steps = epochs * ((len(tr) + batch_size - 1) // batch_size)
    sched = torch.optim.lr_scheduler.LambdaLR(opt, lambda i: min(1.0, i / (0.06 * steps)) * max(0.0, (steps - i) / steps))
    loss_fn, t0 = torch.nn.BCEWithLogitsLoss(), time.time()
    for ep in range(epochs):
        idx = np.random.default_rng(seed + ep).permutation(len(tr))
        for b in range(0, len(idx), batch_size):
            if b % (batch_size * 50) == 0:
                cool_down()
            rows = tr.iloc[idx[b:b + batch_size]]
            enc = s.encode(rows["label"].values, rows["text"].values)
            with torch.autocast("cuda", dtype=torch.bfloat16):
                logits = s.model(**enc).logits[:, 0].float()
            loss = loss_fn(logits, torch.tensor(rows["target"].values, dtype=torch.float32, device="cuda:0"))
            loss.backward()
            torch.nn.utils.clip_grad_norm_(s.model.parameters(), 1.0)
            opt.step(), sched.step(), opt.zero_grad()
        s.model.eval()
        pv = s.p_yes(val["label"].values, val["text"].values, batch_size=128)
        s.model.train()
        agree = ((pv >= 0.5) == (val["target"].values >= 0.5)).mean()
        print(f"epoch {ep + 1}: agreement with teacher on held-aside training pieces {agree:.3f} ({time.time() - t0:.0f} s)",
              flush=True)
    s.model.save_pretrained(out_dir)
    s.tok.save_pretrained(out_dir)
    return len(tr), len(val), time.time() - t0


if __name__ == "__main__":
    name, base = sys.argv[1], sys.argv[2]
    epochs, version = int(sys.argv[3]) if len(sys.argv) > 3 else 3, sys.argv[4] if len(sys.argv) > 4 else "v4"
    batch_size = int(sys.argv[5]) if len(sys.argv) > 5 else 32
    n_tr, n_val, secs = train(base, RESEARCH / "cache" / f"student_{name}", epochs=epochs, version=version,
                              batch_size=batch_size)
    print(f"trained on {n_tr} pieces, watched {n_val}, {secs:.0f} s")
