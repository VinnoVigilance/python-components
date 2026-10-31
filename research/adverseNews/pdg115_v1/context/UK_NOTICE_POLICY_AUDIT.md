# UK_GOV notice cluster: full-text policy audit

This is a same-assistant, **non-blind** follow-up: old labels were visible before all ten articles were read. It is useful for policy design, but not independent adjudication or accuracy measurement. All ten full saved bodies were inspected; most are short summary pages pointing to letters or annexes that are not included in the model input. No labels in the 5,000-row corpus were changed.

## Confirmed rule (product owner, 2026-10-31)

A warning or improvement notice should receive **1 only when the supplied article text itself establishes a specific qualifying offence, deliberately abusive conduct, formal investigation of a suspected breach, relevant regulatory penalty, licence restriction, or other concrete qualifying enforcement basis**. A notice about broadly inadequate services, financial management, or governance is **0** if the text gives no such specific basis. A page whose only notice is explicitly lifted is **0** under the existing resolution-only exception unless it substantively reports an independent qualifying matter. Do not infer misconduct from the notice title or from an annex that is not in the supplied text.

The product owner accepted this narrower boundary. The ten saved articles below can now be interpreted consistently under the written policy. Their GPT review is still not independent reference labeling. The original 5,000-row corpus remains frozen; a separate correction overlay records affected silver labels so future experiments can opt in without rewriting historical evaluations.

## Ten saved articles

| Article | Old silver label | Status in supplied text | Policy assessment | Why |
| --- | ---: | --- | --- | --- |
| `AN-P115-063` Kent SEND | 0 | Lifted after review | 0 | Only inadequate service progress and the lifted notice are described. |
| `AN-P115-R3900` Dame Catherine Harpur's School | 1 | Active warning/improvement notice | 0 | “Failings” are unspecified; article has no specific suspected breach or qualifying conduct. |
| `AN-P115-R3964` Robert Smyth Academy Trust | 1 | Notice closed; recoverable funding obligation remains | 0 | The notice is closed; repayment obligation alone is not a reported offence. |
| `AN-P115-R3975` Oldershaw Academy Trust | 1 | Lifted | 0 | Only financial-management notice and lifting are described. |
| `AN-P115-R4038` Wirral Council | 1 | Lifted | 0 | Inadequate service judgement and later lifting; no independent qualifying conduct in the short article. |
| `AN-P115-R4225` Basildon Academies | 0 | Lifted | 0 | Only financial-management notice and lifting are described. |
| `AN-P115-R4253` Holland Park School | 1 | Lifted | 0 | Only financial-control notice and lifting are described. |
| `AN-P115-R4264` Brighter Academy Trust | 0 | Lifted | 0 | Only financial-management notice and lifting are described. |
| `AN-P115-R4347` Walkwood Academy Trust | 1 | Active notice | 0 | Financial-management improvement notice lacks a specific breach or penalty basis. |
| `AN-P115-R4385` Sandwich Technology School | 0 | Issued warning notice | 0 | Basis is an inadequate inspection judgement, not a stated offence or specific suspected breach. |

The strongest finding is **internal inconsistency**: seven pages describe lifted or closed notices, yet four of those seven have old positive labels while three have old negative labels. The confirmed rule implies six old positive silver labels should be 0; four old negatives remain 0. These are targeted policy corrections to model-proposed labels, not a new estimate of model accuracy. The four affected closed/lifted notices and two active notices are listed in the separate correction overlay.
