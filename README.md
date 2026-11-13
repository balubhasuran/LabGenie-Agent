# LabGenie clinical profile generator

This repository reproduces the **profile construction** part of the LabGenie-Agent study. It reads the complete outpatient PCORnet-style CSV exports, finds each patient's first qualifying diagnosis for each supported disease, and writes as-of clinical profiles indexed by disease and timepoint (for example, `t2dm/T0` and `t2dm/T3`). It does not generate patient education, run the urgency assessment agent, or make a clinical release decision.

The input is a **set of five full CSV tables**, not a preselected patient row. The study's `OP_*` exports and the corresponding PCORnet table names are accepted:

| Required table | Study export | PCORnet-style alternative | Fields used |
| --- | --- | --- | --- |
| Demographics | `OP_Demographics_fixed_fields.csv` or `OP_Demographics.csv` | `DEMOGRAPHIC.csv` | `ID`/`PATID`, `BIRTH_DATE`, `SEX`, `RACE` |
| Encounters | `OP_Encounters.csv` | `ENCOUNTER.csv` | `ENCOUNTERID`, `ID`/`PATID`, `ENC_TYPE` |
| Diagnoses | `OP_Diagnoses.csv` | `DIAGNOSIS.csv` | `patient_id`/`ID`/`PATID`, `ENCOUNTERID`, `ADMIT_DATE`/`DX_DATE`, `DX` |
| Labs | `OP_Labs_with_TSH.csv` | `LAB_RESULT_CM.csv` | `ID`/`PATID`, `ENCOUNTERID`, `RESULT_DATE`/`SPECIMEN_DATE`, `RESULT_NUM`, `RESULT_UNIT`, `COMPONENT`/`LAB_LOINC` |
| Prescribing | `OP_Prescribing.csv` | `PRESCRIBING.csv` | `ID`/`PATID`, `ENCOUNTERID`, `RX_ORDER_DATE`/`RX_START_DATE`, `RXNORM_CUI`, `RAW_RX_MED_NAME` |

Only outpatient encounter types `AV`, `OA`, and `TH` are included, matching the study extraction. Diagnosis code prefixes define the four disease indices: T2DM (`E11`, `250`), CKD (`N18`, `I12`, `I13`, `585`), hypothyroidism (`E00`–`E03`, `243`, `244`), and hyperlipidemia (`E78`, `272`). The first qualifying outpatient diagnosis is T0. The offsets are T0=0, T1=30, T2=60, T3=90, T4=180, and T5=360 days. Records are included from the index date **through** each snapshot, so later records cannot leak into earlier profiles. A snapshot is written only when at least one numeric lab result is available by then.

## Install and run

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[test]"
$env:LABGENIE_PROFILE_SALT = "replace-with-a-private-random-string-at-least-16-characters"
labgenie-profiles --input-dir "D:\path\to\csv-tables" --output-dir output --diseases t2dm --timepoints T0 T3
python -m pytest
```

`--output-dir` receives `disease/timepoint/P-<pseudonym>_disease_timepoint.txt` and a `manifest.csv`. Dates and source patient IDs are omitted by default. The pseudonym is a keyed HMAC derived from `LABGENIE_PROFILE_SALT`; keep the salt outside the repository and use the same salt if stable pseudonyms are needed across runs. Use `--include-dates` only for controlled local analysis. For large exports, adjust `--memory-limit` and set `--temp-dir` to a drive with sufficient free space. Raw CSVs and output directories are ignored by Git.

## Examples and prompts

- [`examples/clinical_profile_date_free.txt`](examples/clinical_profile_date_free.txt) is a **synthetic, date-free** illustrative profile in the same structural style as the attached Round 5 Case 3. It is not the underlying EHR record.
- [`examples/case03_generated_content.pdf`](examples/case03_generated_content.pdf) is the attached, unedited Round 5 Case 3 generated-content example. It is a **research artifact, not approved patient guidance**. The study reports `NOT_APPROVED_RESEARCH_ONLY` clinical sign-off for all 11 cases.
- [`prompts/summary.md`](prompts/summary.md) summarizes the content-generation prompt roles, outputs, and guardrails. This repository does not execute those prompts.

The attached Case 3 was a **T2 (60-day)** profile. The T0 and T3 examples in the command above are generated from the full CSV tables; no Case 3 T3 result is claimed here.

## Study alignment and limits

This code follows the profile indexing and as-of filtering described in the supplied V6 manuscript and the Round 3 source script. It is a reusable, independent implementation, **not a byte-for-byte reproduction** of every study preprocessing step. The study used date-shifted EHR extracts and additional eligibility, urgency, safety, and clinician-review stages. The profiles produced here should be validated against the source data dictionary and local research protocol before use in a study analysis. Do not publish source EHR exports, salts, or patient-specific generated profiles.

PCORnet's [Common Data Model](https://pcornet.org/data/common-data-model/) provides the source table conventions; local OneFlorida+/study exports may differ in field names and coding. The CLI fails with a clear missing-column error if a required field is absent.
