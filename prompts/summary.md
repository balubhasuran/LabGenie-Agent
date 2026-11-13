# Content-generation prompt summary

The LabGenie-Agent manuscript describes an orchestrated pipeline. This is a **functional summary** of the evaluated prompt roles; it is not a substitute for the versioned, full system prompts or their model configuration.

| Role | Inputs and requested output | Key boundaries |
| --- | --- | --- |
| Context | Parse the clinical snapshot into a schema-conformant patient context with demographics, labs, medications, and diagnoses. | Use only information present in the profile; do not infer symptoms, adherence, home readings, or goals. |
| Reference retrieval | Retrieve compatible laboratory reference evidence and assign status. | If no compatible reference is available, use `INDETERMINATE`; do not invent a range. |
| Urgency assessment | Assign condition-level and session-level `ON_TRACK`, `NEEDS_ATTENTION`, or `URGENT` using deterministic rules. | A communication-priority tier, not an emergency diagnosis. The generative agents cannot override it. |
| Tailoring | Rank consequential results and choose full, brief, or acknowledge-only explanation depth. | Respect the urgency tier and uncertainty; avoid elevating a minor isolated abnormality solely because it is out of range. |
| Educational reasoning | Produce internal reviewer reasoning and a short plain-language patient explanation for selected results. | Use retrieved status only, avoid unsupported causal claims and treatment directives, and acknowledge uncertainty. |
| Activation A — questions | Generate four or five patient-voiced questions for a clinician. | A three-question output requires an explicit shortfall flag; the optional experience question counts toward the total. |
| Activation B — What Matters Now | Explain the most consequential findings in language calibrated to the urgency tier. | Stay grounded in the profile and retrieved status; use clear, non-catastrophizing language. |
| Activation C — next steps | Produce concrete preparation or follow-up steps, with count set by the communication plan. | Avoid medication start/stop/dose instructions and unsupported clinical directives; flag an insufficient supported-action count. |
| Activation D — SMS | Draft a short notification with the required portal-link placeholder. | Respect length and placeholder requirements and avoid sensitive clinical detail in the SMS. |
| Governance | Record structural, completeness, safety-boundary, grounding, and urgency-consistency checks. | A non-blocking audit record is separate from clinical release. Research outputs require clinician sign-off. |

The supplied V6 manuscript reports that the 11 evaluated profiles each completed all pipeline stages. Automated release checks passed, while every case retained clinical sign-off status `NOT_APPROVED_RESEARCH_ONLY`. Accordingly, the attached Case 3 output is presented only as an example of model-generated research content.
