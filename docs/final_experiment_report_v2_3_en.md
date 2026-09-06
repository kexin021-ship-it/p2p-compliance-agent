# Purchase Order Investigation Agent vs. Rule Baseline

**Frozen V2.2 — full run on 3,000 purchase orders**

Version 2.3 (revised). 6 September 2026.

---

## Executive summary

The frozen V2.2 agent completed a full batch run over all 3,000 purchase orders. All five
batches finished with zero failed requests. The agent and the pure rule baseline reached
the same decision on 2,628 POs — an agreement rate of 87.6% — and differed on 372.

There is no per-case human ground truth across the full 3,000, so 87.6% is a system
**agreement** rate, not accuracy. An earlier draft of this report claimed a **+10.6
percentage point relative accuracy advantage** over the rule baseline, based on the frozen
holdout set. That claim has been re-checked, found invalid, and is **retracted** in
Section 6: of the 9 holdout cases, only 7 were actual disagreements, all 7 were
consignment purchase orders following a single pattern, and the effective sample size is 1.

This project produced 42 human judgements across distinct purchase orders — 20 development,
13 initial blind review and regression, 9 frozen holdout. All three sets were drafted by a
model and then reviewed line by line by the author. Because the drafting model is the same
model under evaluation, these labels do not constitute independent ground truth. The
conclusions this report can support are therefore limited to full-run decision
distribution, system agreement, behavioural differences, and operating cost.

| Key metric | Result | Interpretation |
|---|---|---|
| Full-run scale | 3,000 POs | Distribution and agreement analysis |
| Rule / agent agreement | 2,628 POs — 87.6% | Not accuracy |
| Frozen holdout disagreements | 7 actual, one shared pattern | Effective sample size 1; comparison invalid |
| Relative accuracy advantage | **Retracted** | See Section 6 |
| Agent `insufficient_evidence` | 361 POs — 12.0% | 252 of them are consignment identification |
| Manual investigation queue | 785 → 1,026 (+31%) | Insufficient evidence still requires a human |

---

## 1. Research question and hypothesis

**Question.** In purchase-to-pay process investigation, can an LLM-based agent produce more
reliable PO-level decisions than a pure rule baseline, while also identifying the specific
line items, primary finding, and evidence gaps?

**Hypothesis.** With the prompt, fact-summary logic, and model version frozen, the agent
would be correct more often than the rule baseline in independent blind human review of
their disagreements. Its main contribution was expected to come from explicitly recognising
insufficient-evidence states, rather than from simply widening or narrowing investigation
scope.

---

## 2. Systems and decision definitions

**Rule baseline.** No AI. Deterministic checks on event ordering, required events, reversal
handling, and closure. Outputs *further investigation* or *no further investigation*. It is
the deliberately dumb comparator the project needs.

**Frozen agent.** `gpt-5.4-mini-2026-03-17`, `reasoning_effort=low`. Input is a structured
PO fact summary. Output is a PO-level decision plus primary finding, focus line items,
observed facts, evidence, possible causes, missing information, decision rationale, and
recommended action.

| Decision | Meaning | Applies when |
|---|---|---|
| `further_investigation` | Needs investigation | A concrete, unresolved process anomaly or gap exists |
| `insufficient_evidence` | Evidence insufficient | The log lacks the state values or follow-up events needed to characterise the case |
| `no_further_investigation` | No investigation needed | Events show a closed loop, withdrawal and termination, or no unresolved signal |

Note that only the agent can emit `insufficient_evidence`. The rule baseline is a two-class
system. This asymmetry matters in Section 6.

---

## 3. Sample design and human labelling

The experiment used four layers: development, regression, frozen holdout, and full run. The
development set and initial blind review both fed into the formation of V2.2, so they serve
debugging and robustness checks only. The frozen holdout ran after version lock and was
intended for the final relative comparison — but that comparison was found invalid on
re-check (Section 6).

| Stage | Sample | Human judgements | Purpose | Valid for unbiased comparison |
|---|---|---|---|---|
| Development set | 20 POs | 20 | Prompt and output-logic tuning | No |
| Initial blind review | 60 POs | 13 | Error discovery, V2.2 regression | No |
| Frozen holdout | 47 POs | 9 | Adjudicate disagreements | No (retracted on re-check) |
| Frozen full run | 3,000 POs | none | Scale and routing analysis | N/A |

Total human judgement covers 42 distinct purchase orders: `development_labels_v1.jsonl`
(20), `blind_labels_v1.jsonl` (13), `blind_holdout_labels_v2_2.jsonl` (9). An earlier file
`development_labels.jsonl` was byte-identical to `development_labels_v1.jsonl` and has been
removed to avoid double counting. How all three sets were produced is described in
Section 9, point 3.

---

## 4. Freeze and reproducibility controls

- Frozen version fingerprint: `6D016275F48316DF74CDCB2A3336D94DEFEA7FC43B74B479000AFD2678B3DC6B`
- Full-run predictions SHA256: `2D2D85D72D0EA45F1CD495406E87B1556FD7F61A7C028774AAFEDF6079B38584`
- Prediction count: 3,000 unique purchase orders
- Batch status: 5 of 5 complete, 0 failed requests

The version fingerprint confirms the model, system prompt, and summary program did not
change after the freeze. The prediction hash confirms that all downstream scoring,
aggregation, and document references point to the same full-run result.

---

## 5. Full-run results on 3,000 POs

### Decision distribution

![Agent decision distribution](figures/full_3000_agent_distribution_v2_2.png)

| System | Further investigation | Insufficient evidence | No further investigation |
|---|---:|---:|---:|
| Rule baseline | 785 (26.2%) | 0 | 2,215 (73.8%) |
| Frozen V2.2 agent | 665 (22.2%) | 361 (12.0%) | 1,974 (65.8%) |

The agent extends the binary rule routing into three classes. 361 POs are explicitly marked
as insufficient evidence rather than being forced into either "anomalous" or "closed."

### Agreement and disagreement

![Breakdown of the 372 disagreements](figures/full_3000_disagreement_breakdown_v2_2.png)

> The subtitle in this figure reflects the original reading and is left uncorrected for
> traceability. See the paragraph below.

| Rule decision | Agent decision | POs |
|---|---|---:|
| Further investigation | Further investigation | 665 |
| Further investigation | Insufficient evidence | 109 |
| Further investigation | No further investigation | 11 |
| No further investigation | Insufficient evidence | 252 |
| No further investigation | No further investigation | 1,963 |

Of the 372 disagreements, 361 come from the agent's insufficient-evidence judgement: 252
that the rule cleared and 109 that the rule flagged. Only 11 cases were downgraded by the
agent from "investigate" to "no investigation."

Inspecting the stated rationale for those 252 cases shows that **251 of them say the same
thing**: a consignment PO where goods were received but withdrawal or settlement cannot be
observed in the log. The agent's main behavioural change is therefore not general
uncertainty expression — it is concentrated in one structural category that can be
identified directly from the line item category field.

---

## 6. Frozen holdout comparison — conclusion retracted

| Item | As originally reported | After re-check |
|---|---|---|
| Holdout disagreements | 9 | **7** (the other 2 had identical decisions) |
| Case composition | not stated | **All 7 were consignment POs** |
| Label independence | treated as independent adjudication | **4 notes were word-for-word identical** |
| Effective sample size | 9 | **1** |

Three problems were found.

**First**, 2 of the 9 cases (4507000784 and 4508056941) had the same decision from both
systems and were not disagreements at all. The actual count is 7.

**Second**, all 7 disagreements were consignment purchase orders, the human conclusion was
`insufficient_evidence` in every one, and 4 of the notes were textually identical. This is
one pattern repeated seven times; the effective sample size is 1.

**Third**, that exact pattern was written into the frozen system prompt, which instructs
the agent to return `insufficient_evidence` when a consignment line has been received but
withdrawal or settlement is not observable.

In addition, the human ground truth for all 7 was `insufficient_evidence` — an output the
two-class rule baseline is structurally incapable of producing. It could not have scored on
these cases under any circumstances.

Taken together, this comparison measured prompt compliance and the difference in output
class count, not independent judgement quality. **The +10.6 percentage point relative
accuracy advantage is retracted.** The full-run distribution and agreement statistics in
Section 5 are unaffected.

---

## 7. Primary findings and business interpretation

| Agent primary finding | POs | Share |
|---|---:|---:|
| Process appears resolved | 1,974 | 65.8% |
| Process state uncertain | 360 | 12.0% |
| Invoice receipt without observed clearing | 335 | 11.2% |
| Required invoice receipt not observed | 175 | 5.8% |
| Required goods receipt not observed | 55 | 1.8% |
| Vendor invoice before PO creation | 51 | 1.7% |
| Other reversal and ordering issues | 50 | 1.7% |

Rules are well suited to anomalies with clear, stable definitions: cheap and consistent. But
in this experiment the insufficient-evidence class did not demonstrate independent agent
judgement. 252 of 361 cases (70%) amount to "this is a consignment PO whose withdrawal and
settlement are not observable."

Consignment does not go through order-level invoicing at all, so a missing invoice receipt
is expected rather than anomalous. It should be exempted deterministically at the rule
layer on the line item category field, not routed into a human review queue. This exemption
was identified during the design phase but was never implemented in the rule baseline, so
these cases were passed through to the agent and counted toward its incremental value.

From a deployment standpoint: the rule baseline produces a human queue of 785 cases. The
agent produces 665 explicit anomalies plus 361 insufficient-evidence cases, for a total of
1,026 — an increase of 31%. **The operative conclusion of this experiment is that, with the
rule baseline lacking structural exemptions, introducing the agent did not reduce
investigation workload.**

---

## 8. Operating cost and engineering reliability

| Item | Count |
|---|---:|
| Standard input tokens | 4,156,666 |
| Cached input tokens | 8,219,904 |
| Output tokens (incl. reasoning) | 1,981,847 |
| Total | 14,358,417 |
| Estimated Batch API cost | ≈ $12.65 (≈ $0.004 per PO) |

Cost is estimated from recorded token usage and the GPT-5.4 Mini Batch price at the time of
writing; the API invoice is authoritative. All five batches completed with zero failed
requests, demonstrating that the frozen version can run reliably at 3,000-PO scale.

---

## 9. Limitations and validity boundaries

1. There is no per-case human ground truth across the full 3,000, so the agreement rate
   cannot be read as accuracy.
2. The frozen holdout does not constitute a valid independent comparison. There were 7
   actual disagreements sharing a single consignment pattern, giving an effective sample
   size of 1, and that pattern was written into the system prompt — so the result reflects
   prompt compliance rather than judgement.
3. All 42 human labels were drafted by GPT, then reviewed line by line by the author with
   revisions requested and re-confirmed. Because the drafting model is the same model under
   evaluation, there is anchoring and circular-validation risk; these are not independent
   ground truth. At least one mislabel has been identified: POs 4507039581 and 4507005288
   were marked unclosed on invoice receipts posted less than a month before the log's
   effective end, without accounting for right-censoring.
4. The 20 development cases and 13 initial blind review cases fed into V2.2 tuning and
   cannot be repackaged as independent test results.
5. The event log does not contain all amounts, quantities, post-change state values, payment
   documents, or any processing after the log ends, so business conclusions remain bounded
   by data coverage.
6. This experiment evaluates process investigation behaviour under a given log and rule
   definition. It is not equivalent to an audit conclusion or to real loss detection.
7. The rule baseline does not implement the four structural exemptions identified during
   design (consignment, service entry sheet, deleted line items, 2-way match). The
   comparator is therefore weaker than it should be, and the agent's performance relative to
   it is systematically overstated.

---

## 10. Conclusion and next steps

**Conclusion.** This experiment does not support the claim that the frozen V2.2 agent
outperforms the rule baseline. The original holdout comparison was invalid and has been
retracted. Three conclusions are supported:

1. The frozen V2.2 pipeline runs reliably at 3,000-PO scale with zero failed requests, at
   approximately $0.004 per purchase order.
2. The agent's behavioural change is primarily the introduction of an insufficient-evidence
   class, which raises the human queue from 785 to 1,026 cases (+31%) rather than reducing
   investigation volume.
3. 70% of that class is consignment identification, which the rule layer can exempt in a
   single line based on line item category. No language model is required.

**Next steps**, reprioritised accordingly:

1. Implement the four structural exemptions (consignment, service entry sheet, deleted line
   items, 2-way match) in the rule baseline and re-evaluate. Until then, any comparison
   overstates the agent.
2. Build a test set labelled independently and blind by the author, without model-drafted
   notes, stratified by the rule × agent decision matrix, to estimate absolute false
   positive and false negative rates for both systems.
3. Redraw the division of labour: rules own structural exemptions; the agent handles only
   what rules cannot express — reversal chains, cross-line invoice postings, and historical
   ordering anomalies.

Further prompt tuning is **not** recommended.

---

## Appendix A — Human label files

| Label file | Records | Role |
|---|---:|---|
| `evals/development_labels_v1.jsonl` | 20 | Development set (model-drafted, author-reviewed) |
| `evals/blind_labels_v1.jsonl` | 13 | Initial blind review and regression (same provenance) |
| `evals/blind_holdout_labels_v2_2.jsonl` | 9 | Frozen holdout disagreements (same provenance; comparison retracted) |

## Appendix B — Reproducible outputs

| File | Purpose |
|---|---|
| `outputs/agent_predictions_all_3000_frozen_v2_2.jsonl` | Full-run predictions |
| `outputs/agent_predictions_all_3000_frozen_v2_2_summary.json` | Full-run raw summary |
| `outputs/final_experiment_metrics_v2_2.json` | Report statistics |
| `outputs/full_3000_agent_distribution_v2_2.csv` | Decision distribution |
| `outputs/full_3000_rule_agent_crosstab_v2_2.csv` | Rule × agent crosstab |
| `outputs/final_holdout_metrics_v2_2.csv` | Holdout metrics (superseded by Section 6) |

---

## Suggested citation

> In a full-scale experiment on 3,000 purchase orders, the frozen V2.2 agent and a pure rule
> baseline agreed on 87.6% of decisions, with the agent assigning 12.0% of POs to an
> insufficient-evidence class the rule baseline cannot express. Re-checking showed that 70%
> of that class is consignment identification, which the rule layer can exempt directly, and
> that the human investigation queue rose from 785 to 1,026 cases as a result. The frozen
> holdout's relative accuracy advantage was found to rest on an effective sample size of 1
> and has been retracted. The agent's role was accordingly revised from filter to a division
> of labour in which rules own structural exemptions and the agent handles composite
> anomalies.
