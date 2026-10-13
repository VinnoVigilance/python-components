"""Build one 900-row Excel comparing Claude and GPT watchlist labels side by side (no adjudication)."""
import json
import pandas as pd

D = "data/reports/watchlist_labeling"
GPT = r"C:\Users\admin\Downloads\watchlist_labels_900_gpt.xlsx"
REP = r"C:\Users\admin\Downloads\claude_gpt_label_comparison_report.xlsx"
OUT = f"{D}/watchlist_labels_900_claude_vs_gpt.xlsx"
LABELS = ["Sanctions", "PEP", "RCA", "Crime-Corruption", "Crime-Fraud", "Crime-Money Laundering",
          "Crime-Trafficking & Smuggling", "Crime-Cybercrime", "Crime-Violent Crime", "Crime-Theft & Property Crime",
          "Crime-Forgery & Counterfeiting", "Crime-Tax Crime", "Crime-Securities & Market Crime", "Crime-Organized Crime",
          "Crime-Environmental & Wildlife Crime", "Terrorism / Terrorist Financing", "Proliferation / Proliferation Financing",
          "Human Rights / Atrocities", "Regulatory", "Law Enforcement", "Judicial / Court", "Jurisdiction Risk", "State-Owned"]
VERDICT = {"Claude دقیق‌تر": "Claude more accurate", "GPT دقیق‌تر": "GPT more accurate", "هر دو نیاز به اصلاح": "Both need correction"}
CLAUDE_VERDICT = {"C": "Claude more accurate", "G": "GPT more accurate", "B": "Both need correction", "E": "Both acceptable"}


def split(v):
    return set() if v is None or (isinstance(v, float) and pd.isna(v)) or str(v).strip() == "" else {s.strip() for s in str(v).split("|") if s.strip()}


def join(s):
    return " | ".join(l for l in LABELS if l in s)


def jl(p):
    return [json.loads(l) for l in open(p, encoding="utf-8")]


def cell(lab, cp, cu, gp, gu):
    c = "pos" if lab in cp else "unsure" if lab in cu else ""
    g = "pos" if lab in gp else "unsure" if lab in gu else ""
    if not c and not g: return ""
    if c == g: return f"both {c}"
    return f"C:{c or 'absent'} / G:{g or 'absent'}"


def build():
    sample = {r["record_key"]: r for r in jl(f"{D}/watchlist_label_sample_900.jsonl")}
    text = {r["record_key"]: r["text"] for r in jl(f"{D}/labels_all_input_900.jsonl")}
    gpt = pd.read_excel(GPT, dtype=str).set_index("record_key")
    rep = pd.read_excel(REP, sheet_name="Differences", header=None, skiprows=4, dtype=str)
    rep.columns = ["record_key", "source", "diff_type", "gpt_labels", "claude_labels", "only_gpt", "only_claude",
                   "gpt_unsure", "claude_unsure", "verdict", "suggested_labels", "suggested_unsure", "reason", "text"]
    rep = rep[rep.record_key.notna() & (rep.record_key != "record_key")].set_index("record_key")
    mv = {r["record_key"]: r for r in jl(f"{D}/claude_review_verdicts_314.jsonl")}
    rows = []
    for cr in jl(f"{D}/labels_all_900.jsonl"):
        k = cr["record_key"]
        cp, cu = {l["label"] for l in cr["labels"]}, set(cr.get("unassessed", []))
        g = gpt.loc[k]
        gp, gu = split(g["labels"]), split(g["unsure_labels"])
        smp, r = sample.get(k, {}), (rep.loc[k] if k in rep.index else None)
        both, union = cp & gp, cp | gp
        row = {
            "record_key": k, "source": k.split(":")[0], "entity_type": smp.get("entity_type"),
            "sample_part": smp.get("part"), "stratum": smp.get("stratum"), "input_text": text[k],
            "claude_labels": join(cp), "claude_label_count": len(cp), "claude_unsure": join(cu),
            "claude_evidence": "\n".join(f"{l['label']}: {l['evidence']}" for l in cr["labels"]),
            "claude_note": cr.get("note", "") or "",
            "gpt_labels": join(gp), "gpt_label_count": len(gp), "gpt_unsure": join(gu),
            "gpt_evidence": g["evidence"] or "", "gpt_note": g["note"] or "",
            "labels_both": join(both), "labels_union": join(union),
            "labels_only_claude": join(cp - gp), "labels_only_gpt": join(gp - cp),
            "unsure_both": join(cu & gu), "unsure_only_claude": join(cu - gu), "unsure_only_gpt": join(gu - cu),
            "claude_pos_gpt_unsure": join(cp & gu), "gpt_pos_claude_unsure": join(gp & cu),
            "positive_labels_match": "Y" if cp == gp else "N",
            "unsure_labels_match": "Y" if cu == gu else "N",
            "full_match": "Y" if cp == gp and cu == gu else "N",
            "difference_type": "none" if cp == gp and cu == gu else ("unsure only" if cp == gp else "positive labels"),
            "jaccard_positive": round(len(both) / len(union), 3) if union else 1.0,
            "gpt_verdict": VERDICT.get(r["verdict"], r["verdict"]) if r is not None else "",
            "gpt_verdict_labels": join(split(r["suggested_labels"])) if r is not None else "",
            "gpt_verdict_unsure": join(split(r["suggested_unsure"])) if r is not None else "",
            "gpt_verdict_reason_fa": r["reason"] if r is not None else "",
            "claude_verdict": CLAUDE_VERDICT[mv[k]["verdict"]] if k in mv else "",
            "claude_verdict_labels": join(set(mv[k]["labels"])) if k in mv else "",
            "claude_verdict_unsure": join(set(mv[k]["unsure"])) if k in mv else "",
            "claude_verdict_reason": mv[k]["reason"] if k in mv else "",
        }
        if k in mv:
            row["verdicts_same_winner"] = "Y" if row["gpt_verdict"] == row["claude_verdict"] else "N"
            row["verdicts_same_labels"] = "Y" if split(r["suggested_labels"]) == set(mv[k]["labels"]) else "N"
            row["verdicts_same_labels_and_unsure"] = "Y" if row["verdicts_same_labels"] == "Y" and split(r["suggested_unsure"]) == set(mv[k]["unsure"]) else "N"
        else:
            row["verdicts_same_winner"] = row["verdicts_same_labels"] = row["verdicts_same_labels_and_unsure"] = ""
        for lab in LABELS: row[lab] = cell(lab, cp, cu, gp, gu)
        rows.append(row)
    return pd.DataFrame(rows)


def kappa(a, b):
    n = len(a); po = sum(x == y for x, y in zip(a, b)) / n; pa, pb = sum(a) / n, sum(b) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return round((po - pe) / (1 - pe), 3) if pe < 1 else None


def by_label(df):
    out = []
    for lab in LABELS:
        c = [lab in split(v) for v in df.claude_labels]; g = [lab in split(v) for v in df.gpt_labels]
        out.append({"label": lab, "claude_pos": sum(c), "gpt_pos": sum(g), "both_pos": sum(x and y for x, y in zip(c, g)),
                    "only_claude": sum(x and not y for x, y in zip(c, g)), "only_gpt": sum(y and not x for x, y in zip(c, g)),
                    "claude_unsure": sum(lab in split(v) for v in df.claude_unsure), "gpt_unsure": sum(lab in split(v) for v in df.gpt_unsure),
                    "cohen_kappa": kappa(c, g) if any(c) or any(g) else None})
    return pd.DataFrame(out)


def by_source(df):
    return (df.groupby("source").agg(records=("record_key", "size"),
                                     positive_match=("positive_labels_match", lambda s: (s == "Y").sum()),
                                     full_match=("full_match", lambda s: (s == "Y").sum()),
                                     claude_labels=("claude_label_count", "sum"), gpt_labels=("gpt_label_count", "sum"),
                                     avg_jaccard=("jaccard_positive", "mean"))
            .assign(positive_match_pct=lambda d: (d.positive_match / d.records).round(3), avg_jaccard=lambda d: d.avg_jaccard.round(3))
            .sort_values("records", ascending=False).reset_index())


def prf(pred, gold):
    tp = sum(len(p & g) for p, g in zip(pred, gold)); fp = sum(len(p - g) for p, g in zip(pred, gold)); fn = sum(len(g - p) for p, g in zip(pred, gold))
    pr = tp / (tp + fp) if tp + fp else 1.0; rc = tp / (tp + fn) if tp + fn else 1.0
    return pr, rc, (2 * pr * rc / (pr + rc) if pr + rc else 0.0)


def reference(df, col):
    return [split(r[col]) if r["difference_type"] != "none" else split(r["claude_labels"]) for _, r in df.iterrows()]


def verdict_tables(df):
    d = df[df.difference_type != "none"]
    cross = pd.crosstab(d.gpt_verdict, d.claude_verdict, margins=True, margins_name="Total")
    C, G = [split(v) for v in df.claude_labels], [split(v) for v in df.gpt_labels]
    acc = []
    for ref_name, col in [("GPT verdict", "gpt_verdict_labels"), ("Claude verdict", "claude_verdict_labels")]:
        R = reference(df, col)
        for who, P in [("Claude labels", C), ("GPT labels", G)]:
            pr, rc, f1 = prf(P, R)
            acc.append({"judged_by": ref_name, "labeller": who, "exact_match_900": sum(p == r for p, r in zip(P, R)),
                        "exact_match_pct": round(sum(p == r for p, r in zip(P, R)) / len(R), 3),
                        "precision": round(pr, 3), "recall": round(rc, 3), "f1": round(f1, 3)})
    RG, RC = reference(df, "gpt_verdict_labels"), reference(df, "claude_verdict_labels")
    lab = []
    for L in LABELS:
        c = [L in s for s in C]; g = [L in s for s in G]; rg = [L in s for s in RG]; rc = [L in s for s in RC]
        disputed = [x != y for x, y in zip(c, g)]
        if not any(disputed): continue
        lab.append({"label": L, "disputed_records": sum(disputed),
                    "claude_right_by_claude_verdict": sum(dd and x == r for dd, x, r in zip(disputed, c, rc)),
                    "gpt_right_by_claude_verdict": sum(dd and y == r for dd, y, r in zip(disputed, g, rc)),
                    "claude_right_by_gpt_verdict": sum(dd and x == r for dd, x, r in zip(disputed, c, rg)),
                    "gpt_right_by_gpt_verdict": sum(dd and y == r for dd, y, r in zip(disputed, g, rg)),
                    "verdicts_disagree_on_label": sum(a != b for a, b in zip(rg, rc))})
    return cross, pd.DataFrame(acc), pd.DataFrame(lab).sort_values("disputed_records", ascending=False)


def report(df, acc):
    d = df[df.difference_type != "none"]
    n, nd = len(df), len(d)
    a = {(r.judged_by, r.labeller): r for r in acc.itertuples()}
    gv, cv = d.gpt_verdict.value_counts(), d.claude_verdict.value_counts()
    same_w, same_l = (d.verdicts_same_winner == "Y").sum(), (d.verdicts_same_labels == "Y").sum()
    pct = lambda x, t: f"{x} of {t} ({x / t:.0%})"
    rows = [
        ("1. Scale", "Records labelled by both", f"{n} watchlist records, each labelled independently by Claude and by GPT with the same 23-label guide."),
        ("1. Scale", "Fully identical", pct((df.full_match == "Y").sum(), n) + " - same positive and same unsure labels."),
        ("1. Scale", "Differ only in unsure", f"{(df.difference_type == 'unsure only').sum()} records - same positive labels, different 'not sure' flags. No effect on the labels used for training."),
        ("1. Scale", "Differ in positive labels", f"{(df.difference_type == 'positive labels').sum()} records - at least one real label differs."),
        ("2. Two verdicts", "GPT verdict (comparison report)", f"Claude more accurate {gv.get('Claude more accurate', 0)}, GPT more accurate {gv.get('GPT more accurate', 0)}, both need correction {gv.get('Both need correction', 0)} (of {nd})."),
        ("2. Two verdicts", "Claude verdict (this review)", f"Claude more accurate {cv.get('Claude more accurate', 0)}, GPT more accurate {cv.get('GPT more accurate', 0)}, both need correction {cv.get('Both need correction', 0)}, both acceptable {cv.get('Both acceptable', 0)} (of {nd})."),
        ("2. Two verdicts", "Verdicts pick the same winner", pct(same_w, nd)),
        ("2. Two verdicts", "Verdicts give the same positive labels", pct(same_l, nd) + f". The {nd - same_l} records where they differ are in the 'Verdict Disagreements' sheet."),
        ("3. Accuracy (900 records)", "Claude labels", f"Exactly right on {a[('GPT verdict', 'Claude labels')].exact_match_pct:.0%} (GPT verdict) / {a[('Claude verdict', 'Claude labels')].exact_match_pct:.0%} (Claude verdict); F1 {a[('GPT verdict', 'Claude labels')].f1:.2f} / {a[('Claude verdict', 'Claude labels')].f1:.2f}."),
        ("3. Accuracy (900 records)", "GPT labels", f"Exactly right on {a[('GPT verdict', 'GPT labels')].exact_match_pct:.0%} (GPT verdict) / {a[('Claude verdict', 'GPT labels')].exact_match_pct:.0%} (Claude verdict); F1 {a[('GPT verdict', 'GPT labels')].f1:.2f} / {a[('Claude verdict', 'GPT labels')].f1:.2f}."),
        ("3. Accuracy (900 records)", "Main difference", f"Both are precise (few wrong labels). GPT's gap is recall ~{a[('Claude verdict', 'GPT labels')].recall:.0%}: it misses labels that are in the text (PEP titles, Judicial charges/convictions, Organized Crime 'gang/crime group', Violent Crime)."),
        ("4. Who wins per label", "Claude stronger", "PEP (senior titles), Judicial / Court (charged/convicted/sentenced), Violent Crime, Organized Crime, Trafficking, Money Laundering - see 'Disputes by Label'."),
        ("4. Who wins per label", "GPT stronger", "Theft & Property Crime: GPT labels robbery/armed robbery as theft; Claude usually gave only Violent Crime. This is Claude's one systematic miss (~18 records). Also CIA World Leaders titles (PEP) and 'Violación' (rape -> Violent Crime)."),
        ("5. Where the two verdicts disagree", "DMW 'License Status: Delisted' (8)", "GPT verdict: Regulatory. Claude verdict: Regulatory only unsure, because 'Delisted' does not say the licence was cancelled for a breach. DECISION NEEDED."),
        ("5. Where the two verdicts disagree", "Senior officials - PEP (9)", "Heads of national agencies / presidential administration / legislators that both labellers left unsure (Claude verdict: PEP), plus Taliban-regime and prosecutor roles (GPT verdict: PEP). DECISION NEEDED: how senior is 'senior'."),
        ("5. Where the two verdicts disagree", "Cyber sanctions programs (5)", "OFAC CYBER4 and the EU 'cyber-attacks' regime: GPT verdict removes Cybercrime, Claude verdict keeps it (CYBER2 / UK Cyber regime records were labelled Cybercrime by both). DECISION NEEDED."),
        ("5. Where the two verdicts disagree", "Proliferation strictness (4)", "Missiles or 'nuclear programme' without weapons wording. Claude verdict: unsure unless the text links to WMD/military use."),
        ("5. Where the two verdicts disagree", "Single cases (14)", "Each explained in the claude_verdict_reason / gpt_verdict_reason_fa columns."),
        ("6. Problems found in the GPT report", "Inconsistent decisions", "Same wording judged differently: 'launder oil' kept as ML on SECO 100925 but removed on SECO 95925; ViCAP unknown-suspect notices kept on two records but cleared on FBI 9a7bdccd; Taliban-regime roles PEP on DFAT:51 but unsure on similar records."),
        ("6. Problems found in the GPT report", "Factual misses", "SECO-SANCTIONS:27660 - removed PEP although the text says 'appointed Minister for Security'. FBI d216ad - gave Human Rights for a criminal-gang kidnapping."),
        ("6. Problems found in the GPT report", "Weak GPT evidence", "Some GPT evidence quotes do not prove the label (e.g. 'Measures: Asset freeze ;' as evidence for Fraud on UKSL GHR0019/GHR0020)."),
        ("7. Caveats", "Self-review bias", "The Claude verdict judges labels Claude partly produced; the GPT verdict has the mirror risk. Both reviewers are LLMs - neither is ground truth."),
        ("7. Caveats", "Unsure labels", "Unsure flags are not used for training, so the 111 unsure-only differences matter far less than the 203 positive differences."),
        ("8. Recommendation", "Gold labels", f"Use labels where both verdicts agree ({same_l} disputed records + {(df.difference_type == 'none').sum()} identical records). Send the {nd - same_l} verdict disagreements to a human reviewer."),
        ("8. Recommendation", "Decide 3 rules", "DMW 'Delisted'; PEP seniority threshold; cyber program names. Then add them to labelling_guide.md so the adverse-media set follows the same rules."),
        ("8. Recommendation", "Fix Claude's gap", "Add one line to the guide: robbery / armed robbery = Theft & Property Crime (plus Violent Crime when violence is stated)."),
    ]
    return pd.DataFrame(rows, columns=["section", "finding", "detail"])


if __name__ == "__main__":
    df = build()
    cross, acc, lab = verdict_tables(df)
    with pd.ExcelWriter(OUT, engine="openpyxl") as w:
        report(df, acc).to_excel(w, sheet_name="Report", index=False)
        df.to_excel(w, sheet_name="Records", index=False)
        cross.to_excel(w, sheet_name="Verdict Crosstab")
        acc.to_excel(w, sheet_name="Accuracy vs Verdicts", index=False)
        lab.to_excel(w, sheet_name="Disputes by Label", index=False)
        df[df.verdicts_same_labels == "N"].to_excel(w, sheet_name="Verdict Disagreements", index=False)
        by_label(df).to_excel(w, sheet_name="By Label", index=False)
        by_source(df).to_excel(w, sheet_name="By Source", index=False)
        for ws in w.book.worksheets:
            ws.freeze_panes = "B2"; ws.auto_filter.ref = ws.dimensions
            for col in ws.columns:
                ws.column_dimensions[col[0].column_letter].width = min(50, max(10, max(len(str(c.value or "").split("\n")[0]) for c in col[:200]) + 2))
    print(len(df), "rows ->", OUT)
    print(df.difference_type.value_counts().to_string())
