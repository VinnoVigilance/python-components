"""Shared data loading, fold protocols and score views for the text-layer bake-off notebooks."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
from iterstrat.ml_stratifiers import MultilabelStratifiedKFold
from sklearn.metrics import average_precision_score, precision_recall_curve

from buildDataset import find_canonical
from lookupRules import record_rule_labels

RESEARCH = Path(__file__).resolve().parents[1]
SEED = 42
MIN_POSITIVES = 15
SEGMENT_OF = {
    **{s: "Sanctions & terror lists" for s in ("OFAC-SDN", "OFAC-NON-SDN", "UN-SANCTIONS", "EU-FINANCIAL-SANCTIONS",
        "EU-TRAVEL-BAN", "UKSL", "SECO-SANCTIONS", "DFAT", "EU-DESIGNATED-VESSELS", "ATC-DESIGNATED-TERRORIST-INDIVIDUALS",
        "ATC-DESIGNATED-TERRORIST-GROUPS", "US-STATE-TERRORIST-EXCLUSION")},
    **{s: "Wanted lists" for s in ("INTERPOL-RED-NOTICES", "US-MARSHALS-PROFILED-FUGITIVES", "EU-MOST-WANTED",
        "NCA-MOST-WANTED", "FBI-WANTED")},
    **{s: "Regulatory / debarment" for s in ("ADB-DEBARMENT-SUSPENSION", "AFDB-DEBARRED-ENTITIES", "WORLD-BANK-DEBARRED",
        "WORLD-BANK-OTHER-SANCTIONS", "GPPB-BLACKLISTED-ENTITIES", "CFTC-RED-LIST", "DMW-RECRUITMENT-AGENCIES")},
    **{s: "Officials & candidates" for s in ("CIA-WORLD-LEADERS-HISTORICAL", "PH-HOUSE-MEMBERS", "DILG-LOCAL-OFFICIALS",
        "COMELEC-2025-SENATORS")},
    "DNFBP": "Business register",
}


class Data:
    """The 900 records, 0/1 label matrix, lookup matrix and label groups."""

    def __init__(self):
        records = [json.loads(l) for l in open(RESEARCH / "data" / "dataset_900.jsonl", encoding="utf-8")]
        self.labels = json.load(open(RESEARCH / "data" / "label_names.json", encoding="utf-8"))
        self.short = [l.replace("Crime-", "").split(" / ")[0].split(" & ")[0] for l in self.labels]
        df = pd.DataFrame(records)
        df["segment"] = df["source"].map(SEGMENT_OF)
        df["part"] = df["part"].str[0]
        df["text_noname"] = df["text"].map(lambda t: "\n".join(l for l in t.split("\n") if not l.startswith("Source list:")))
        self.df = df
        self.Y = np.array([[int(l in r["labels"]) for l in self.labels] for r in records])
        lookup_csv = RESEARCH / "data" / "lookup_labels_900.csv"
        if lookup_csv.exists():
            self.L = pd.read_csv(lookup_csv)[self.labels].values
        else:
            canonical = find_canonical(records)
            self.L = np.array([[int(l in record_rule_labels(r["source"], canonical[r["record_key"]])) for l in self.labels]
                               for r in records])
            pd.DataFrame(self.L, columns=self.labels).assign(record_key=df["record_key"]).to_csv(lookup_csv, index=False)
        self.modelled = [i for i in range(len(self.labels)) if self.Y[:, i].sum() >= MIN_POSITIVES]
        self.rare = [i for i in range(len(self.labels)) if i not in self.modelled]
        self.text_layer = [i for i in self.modelled if (self.L[:, i] & self.Y[:, i]).sum() < 0.9 * self.Y[:, i].sum()]

    def seen_folds(self, k=5):
        """Multi-label stratified folds: lists appear in both train and test (same as notebook 01)."""
        folds = np.zeros(len(self.df), int)
        msk = MultilabelStratifiedKFold(n_splits=k, shuffle=True, random_state=SEED)
        for f, (_, te) in enumerate(msk.split(self.df, self.Y[:, self.modelled])):
            folds[te] = f
        return folds

    def unseen_folds(self, k=5):
        """List-grouped folds: every list is entirely in one test fold, balanced on text-layer positives."""
        need = (self.Y & (1 - self.L))[:, self.modelled].sum(1)
        stats = self.df.assign(need=need).groupby("source").agg(need=("need", "sum"), n=("need", "size"))
        load, of = np.zeros((k, 2)), {}
        for src, row in stats.sort_values(["need", "n"], ascending=False).iterrows():
            f = int(np.lexsort((load[:, 1], load[:, 0]))[0])
            of[src] = f
            load[f] += (row["need"], row["n"])
        return self.df["source"].map(of).values


def best_threshold(y, s):
    """Score cut-off that maximises F1 on (y, s)."""
    if y.sum() == 0:
        return np.inf
    p, r, t = precision_recall_curve(y, s)
    f = 2 * p * r / np.maximum(p + r, 1e-12)
    return t[np.argmax(f[:-1])] if len(t) else 0.5


def crossfit_predict(S, Yt, folds):
    """0/1 predictions with per-label thresholds picked on the other folds' out-of-fold scores."""
    P = np.zeros_like(Yt)
    for f in np.unique(folds):
        out, inn = folds == f, folds != f
        for j in range(Yt.shape[1]):
            P[out, j] = S[out, j] >= best_threshold(Yt[inn, j], S[inn, j])
    return P


def prf(y, p):
    """Precision, recall, F1 and counts for flat 0/1 arrays."""
    tp, fp, fn = int((y & p).sum()), int(((1 - y) & p).sum()), int((y & (1 - p)).sum())
    pr = tp / (tp + fp) if tp + fp else 0.0
    rc = tp / (tp + fn) if tp + fn else 0.0
    return pr, rc, (2 * pr * rc / (pr + rc) if pr + rc else 0.0), tp, fp, fn


def ml_part(Y, P, L, cols):
    """Recall on positives the lookup missed and precision of labels the model added beyond the lookup."""
    y, p, l = Y[:, cols], P[:, cols], L[:, cols]
    missed, added = y & (1 - l), p & (1 - l)
    rc = (missed & p).sum() / max(missed.sum(), 1)
    pr = (added & y).sum() / max(added.sum(), 1)
    return pr, rc, (2 * pr * rc / (pr + rc) if pr + rc else 0.0)


def wilson(k, n, z=1.96):
    """95% Wilson interval for k successes out of n."""
    if n == 0:
        return np.nan, np.nan
    p = k / n
    d, c = 1 + z * z / n, p + z * z / (2 * n)
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (c - h) / d, (c + h) / d


def full_predictions(data, P_modelled, P_rare=None):
    """23-column predictions: modelled labels from P_modelled, rare labels from P_rare or zero."""
    P = np.zeros_like(data.Y)
    P[:, data.modelled] = P_modelled
    if P_rare is not None:
        P[:, data.rare] = P_rare
    return P


def score_views(data, P23, S_modelled=None):
    """All agreed views of one prediction set (23 columns, lookup not yet added)."""
    Y, L = data.Y, data.L
    SYS = P23 | L
    macro = lambda cols, mask=slice(None): np.mean([prf(Y[mask, i], SYS[mask, i])[2] for i in cols])
    w_pr, w_rc, w_f1, *_ = prf(Y.ravel(), SYS.ravel())
    m_pr, m_rc, m_f1 = ml_part(Y, P23, L, data.modelled)
    a_pr, a_rc, a_f1 = ml_part(Y, P23, L, list(range(len(data.labels))))
    isD = data.df["part"].values == "D"
    out = {
        "whole P": w_pr, "whole R": w_rc, "whole micro-F1": w_f1,
        "whole macro-F1 (23)": macro(range(len(data.labels))), "whole macro-F1 (18, no rare)": macro(data.modelled),
        "text-layer macro-F1": macro(data.text_layer),
        "ML part P": m_pr, "ML part R": m_rc, "ML part F1": m_f1,
        "ML part R incl. rare": a_rc,
        "part D text-layer macro-F1": macro([i for i in data.text_layer if Y[isD, i].sum() >= 3], isD),
    }
    if S_modelled is not None:
        out["macro PR-AUC"] = np.mean([average_precision_score(Y[:, i], S_modelled[:, j]) for j, i in enumerate(data.modelled)])
    return out


def per_label(data, P23, S_modelled=None):
    """Per-label table: support, lookup share, system P/R/F1 with intervals, ML-part recall, PR-AUC."""
    Y, L = data.Y, data.L
    SYS = P23 | L
    rows = []
    for i, name in enumerate(data.short):
        pr, rc, f1, tp, fp, fn = prf(Y[:, i], SYS[:, i])
        missed = Y[:, i] & (1 - L[:, i])
        row = {"label": name, "support": int(Y[:, i].sum()), "lookup share": (Y[:, i] & L[:, i]).sum() / max(Y[:, i].sum(), 1),
               "P": pr, "P 95% CI": "{:.2f}–{:.2f}".format(*wilson(tp, tp + fp)), "R": rc,
               "R 95% CI": "{:.2f}–{:.2f}".format(*wilson(tp, tp + fn)), "F1": f1,
               "ML part R": (missed & P23[:, i]).sum() / missed.sum() if missed.sum() else np.nan}
        if S_modelled is not None and i in data.modelled:
            row["PR-AUC"] = average_precision_score(Y[:, i], S_modelled[:, data.modelled.index(i)])
        rows.append(row)
    return pd.DataFrame(rows).set_index("label")


def label_tiers(data):
    """Tier per label: lookup only (lookup gives ≥90%), else A / B / C by ≥50 / 30–49 / <30 positives the lookup misses."""
    need = (data.Y & (1 - data.L)).sum(0)
    share = (data.Y & data.L).sum(0) / np.maximum(data.Y.sum(0), 1)
    return ["lookup only" if share[i] >= 0.9 else "A validated ML" if need[i] >= 50
            else "B provisional ML" if need[i] >= 30 else "C no ML" for i in range(len(data.labels))]


def apply_tiers(data, P23):
    """Keep model predictions only for tier A and B labels."""
    keep = np.array([t[0] in "AB" for t in label_tiers(data)])
    return P23 * keep


def tier_view(data, P23):
    """Per tier: labels the model added beyond the lookup, how many were wrong, and ML-part P / R / F1."""
    Y, L, tiers = data.Y, data.L, np.array(label_tiers(data))
    rows = []
    for t in ("lookup only", "A validated ML", "B provisional ML", "C no ML"):
        cols = list(np.flatnonzero(tiers == t))
        added = P23[:, cols] & (1 - L[:, cols])
        pr, rc, f1 = ml_part(Y, P23, L, cols)
        rows.append({"tier": t, "labels": ", ".join(data.short[i] for i in cols),
                     "positives lookup misses": int((Y[:, cols] & (1 - L[:, cols])).sum()),
                     "model added": int(added.sum()), "added but wrong": int((added & (1 - Y[:, cols])).sum()),
                     "ML part P": pr, "ML part R": rc, "ML part F1": f1})
    return pd.DataFrame(rows).set_index("tier")


def per_segment(data, P23):
    """Text-layer macro-F1, ML-part recall and exact-match share per list segment."""
    Y, L, seg = data.Y, data.L, data.df["segment"].values
    SYS = P23 | L
    rows = []
    for s in sorted(set(seg)):
        m = seg == s
        cols = [i for i in data.text_layer if Y[m, i].sum() >= 5]
        rows.append({"segment": s, "records": int(m.sum()),
                     "text-layer macro-F1": np.mean([prf(Y[m, i], SYS[m, i])[2] for i in cols]) if cols else np.nan,
                     "ML part R": ml_part(Y[m], P23[m], L[m], data.modelled)[1] if (Y[m] & (1 - L[m])).sum() else np.nan,
                     "exact-match %": 100 * (SYS[m] == Y[m]).all(1).mean()})
    return pd.DataFrame(rows).set_index("segment")
