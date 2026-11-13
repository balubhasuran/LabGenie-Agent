import csv
import re
from pathlib import Path

from labgenie_profiles.cli import build


def write_csv(path: Path, columns: list[str], rows: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(columns)
        writer.writerows(rows)


def test_full_csv_produces_disease_timepoints_without_future_leakage(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    write_csv(source / "OP_Demographics_fixed_fields.csv", ["ID", "BIRTH_DATE", "SEX", "RACE"],
              [["raw-patient-1", "1950-01-01", "F", "5"]])
    write_csv(source / "OP_Encounters.csv", ["ENCOUNTERID", "ID", "ENC_TYPE"],
              [["e0", "raw-patient-1", "AV"], ["e90", "raw-patient-1", "OA"],
               ["e120", "raw-patient-1", "TH"], ["inpatient", "raw-patient-1", "IP"]])
    write_csv(source / "OP_Diagnoses.csv", ["patient_id", "ENCOUNTERID", "ADMIT_DATE", "DX", "ICD10 String"],
              [["raw-patient-1", "e0", "2020-01-01", "E11.9", "Type 2 diabetes"],
               ["raw-patient-1", "e90", "2020-03-31", "E78.5", "Hyperlipidemia"],
               ["raw-patient-1", "inpatient", "2019-01-01", "E11.9", "Inpatient diagnosis"]])
    write_csv(source / "OP_Labs_with_TSH.csv", ["ID", "ENCOUNTERID", "RESULT_DATE", "RESULT_NUM", "RESULT_UNIT", "COMPONENT"],
              [["raw-patient-1", "e0", "2020-01-01", "9.6", "%", "Hemoglobin A1c"],
               ["raw-patient-1", "e90", "2020-03-31", "8.0", "%", "Hemoglobin A1c"],
               ["raw-patient-1", "e120", "2020-04-30", "7.1", "%", "Hemoglobin A1c"],
               ["raw-patient-1", "inpatient", "2020-01-01", "20", "%", "Hemoglobin A1c"]])
    write_csv(source / "OP_Prescribing.csv", ["ID", "ENCOUNTERID", "RX_ORDER_DATE", "RXNORM_CUI", "RAW_RX_MED_NAME"],
              [["raw-patient-1", "e90", "2020-03-31", "123", "Example medicine"]])
    output = tmp_path / "output"
    result = build(source, output, ["T0", "T3"], ["t2dm"], "private-test-salt-at-least-16")
    assert result == {"patients": 1, "profiles": 2}
    t0 = next((output / "t2dm" / "T0").glob("*.txt")).read_text(encoding="utf-8")
    t3 = next((output / "t2dm" / "T3").glob("*.txt")).read_text(encoding="utf-8")
    assert "9.6 %" in t0 and "8.0 %" not in t0 and "Example medicine" not in t0
    assert "8.0 %" in t3 and "Example medicine" in t3 and "7.1 %" not in t3
    assert "20 %" not in t0 + t3
    assert "raw-patient-1" not in t0 + t3
    assert not re.search(r"\b\d{4}-\d{2}-\d{2}\b", t0 + t3)
    manifest = list(csv.DictReader((output / "manifest.csv").open(encoding="utf-8")))
    assert {row["timepoint"] for row in manifest} == {"T0", "T3"}


def test_pcornet_table_names_and_column_aliases(tmp_path: Path) -> None:
    source = tmp_path / "pcornet"
    source.mkdir()
    write_csv(source / "DEMOGRAPHIC.csv", ["PATID", "BIRTH_DATE", "SEX", "RACE"],
              [["private-id", "1960-04-01", "M", "03"]])
    write_csv(source / "ENCOUNTER.csv", ["ENCOUNTERID", "PATID", "ENC_TYPE"],
              [["visit-1", "private-id", "AV"]])
    write_csv(source / "DIAGNOSIS.csv", ["PATID", "ENCOUNTERID", "DX_DATE", "DX"],
              [["private-id", "visit-1", "2021-01-01", "N18.9"]])
    write_csv(source / "LAB_RESULT_CM.csv", ["PATID", "ENCOUNTERID", "RESULT_DATE", "RESULT_NUM", "RESULT_UNIT", "LAB_LOINC"],
              [["private-id", "visit-1", "2021-01-01", "24", "mL/min/1.73m2", "33914-3"]])
    write_csv(source / "PRESCRIBING.csv", ["PATID", "ENCOUNTERID", "RX_START_DATE", "RAW_RX_MED_NAME"], [])
    output = tmp_path / "output"
    result = build(source, output, ["T0"], ["ckd"], "another-private-test-salt")
    assert result == {"patients": 1, "profiles": 1}
    text = next((output / "ckd" / "T0").glob("*.txt")).read_text(encoding="utf-8")
    assert "33914-3 | 24 mL/min/1.73m2" in text
    assert "N18.9 | N18.9" in text
    assert "private-id" not in text
