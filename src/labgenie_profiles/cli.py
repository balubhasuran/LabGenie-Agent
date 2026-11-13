"""Build disease/timepoint profiles from full outpatient PCORnet-style CSV tables.

The study's OP_* exports and corresponding PCORnet CDM table names are accepted.
No model calls, urgency inference, or clinical release decisions are made here.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import hmac
import json
import os
import re
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import duckdb

SCHEDULE = {"T0": 0, "T1": 30, "T2": 60, "T3": 90, "T4": 180, "T5": 360}
DISEASES = {
    "t2dm": "Type 2 diabetes mellitus",
    "ckd": "Chronic kidney disease",
    "hypothyroidism": "Hypothyroidism",
    "hyperlipidemia": "Hyperlipidemia",
}
ALIASES = {
    "demographics": ("OP_Demographics_fixed_fields.csv", "OP_Demographics.csv", "DEMOGRAPHIC.csv"),
    "encounters": ("OP_Encounters.csv", "ENCOUNTER.csv"),
    "diagnoses": ("OP_Diagnoses.csv", "DIAGNOSIS.csv"),
    "labs": ("OP_Labs_with_TSH.csv", "LAB_RESULT_CM.csv"),
    "prescribing": ("OP_Prescribing.csv", "PRESCRIBING.csv"),
}
OUTPATIENT_TYPES = ("AV", "OA", "TH")


def find_files(folder: Path) -> dict[str, Path]:
    present = {p.name.lower(): p for p in folder.iterdir() if p.is_file()}
    found = {}
    for table, names in ALIASES.items():
        matches = [present[n.lower()] for n in names if n.lower() in present]
        if len(matches) != 1:
            raise ValueError(f"Expected one {table} CSV in {folder}; found {len(matches)}. Accepted: {', '.join(names)}")
        found[table] = matches[0]
    return found


def headers(path: Path) -> dict[str, str]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return {column.upper(): column for column in next(csv.reader(stream))}


def column(head: dict[str, str], *names: str, required: bool = True) -> str:
    for name in names:
        if name.upper() in head:
            return '"' + head[name.upper()].replace('"', '""') + '"'
    if required:
        raise ValueError(f"Missing required column; expected one of: {', '.join(names)}")
    return "NULL"


def source_column(alias: str, value: str) -> str:
    return value if value == "NULL" else f"{alias}.{value}"


def path_literal(path: Path) -> str:
    return "'" + path.as_posix().replace("'", "''") + "'"


def csv_view(con: duckdb.DuckDBPyConnection, name: str, path: Path) -> None:
    con.execute(f"CREATE VIEW {name} AS SELECT * FROM read_csv({path_literal(path)}, header=true, all_varchar=true, sample_size=20480, ignore_errors=false)")


def date_expr(value: str) -> str:
    return f"TRY_CAST(NULLIF(TRIM({value}), '') AS DATE)"


def normalized_code(code: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (code or "").upper())


def disease_for(code: str) -> str | None:
    code = normalized_code(code)
    if code.startswith(("E11", "250")):
        return "t2dm"
    if code.startswith(("N18", "I12", "I13", "585")):
        return "ckd"
    if code.startswith(("E00", "E01", "E02", "E03", "243", "244")):
        return "hypothyroidism"
    if code.startswith(("E78", "272")):
        return "hyperlipidemia"
    return None


def age_on(birth: date | None, snapshot: date) -> int | None:
    if birth is None:
        return None
    return snapshot.year - birth.year - ((snapshot.month, snapshot.day) < (birth.month, birth.day))


def alias(patient_id: str, salt: str) -> str:
    digest = hmac.new(salt.encode(), patient_id.encode(), hashlib.sha256).hexdigest()[:16]
    return f"P-{digest}"


def display_sex(value: str | None) -> str:
    return {"F": "female", "M": "male"}.get((value or "").upper(), "unspecified")


def display_race(value: str | None) -> str:
    return {"5": "White", "05": "White", "3": "Black or African American", "03": "Black or African American", "2": "Asian", "02": "Asian"}.get((value or "").upper(), value or "unspecified")


def setup(con: duckdb.DuckDBPyConnection, paths: dict[str, Path]) -> None:
    heads = {key: headers(path) for key, path in paths.items()}
    for name, path in paths.items():
        csv_view(con, name + "_raw", path)
    eh, dh, lh, rh, mh = (heads[x] for x in ("encounters", "diagnoses", "labs", "prescribing", "demographics"))
    enc_id = column(eh, "ENCOUNTERID")
    enc_pid = column(eh, "ID", "PATID")
    enc_type = column(eh, "ENC_TYPE")
    con.execute(f"CREATE TEMP TABLE outpatient_enc AS SELECT DISTINCT TRIM({enc_id}) encounter_id, TRIM({enc_pid}) patient_id FROM encounters_raw WHERE UPPER(TRIM({enc_type})) IN {OUTPATIENT_TYPES}")
    dx_pid = column(dh, "patient_id", "ID", "PATID")
    dx_enc = column(dh, "ENCOUNTERID")
    dx_date = column(dh, "ADMIT_DATE", "DX_DATE")
    dx_code = column(dh, "DX")
    dx_name = column(dh, "ICD10 String", "DX_NAME", required=False)
    dx_name = source_column("d", dx_name)
    # PCORnet raw codes may include ICD-9/10. The prefix mapping below covers both.
    con.execute(f"""CREATE TEMP TABLE dx AS SELECT TRIM(d.{dx_pid}) patient_id,
        {date_expr('d.' + dx_date)} event_date, TRIM(d.{dx_code}) code,
        COALESCE(NULLIF(TRIM({dx_name}), ''), TRIM(d.{dx_code})) event_name
        FROM diagnoses_raw d JOIN outpatient_enc e ON TRIM(d.{dx_enc})=e.encounter_id
        AND TRIM(d.{dx_pid})=e.patient_id
        WHERE {date_expr('d.' + dx_date)} IS NOT NULL AND NULLIF(TRIM(d.{dx_code}), '') IS NOT NULL""")
    lab_pid = column(lh, "ID", "PATID")
    lab_enc = column(lh, "ENCOUNTERID")
    lab_date = column(lh, "RESULT_DATE", "SPECIMEN_DATE")
    lab_num = column(lh, "RESULT_NUM")
    lab_unit = column(lh, "RESULT_UNIT", required=False)
    lab_name = column(lh, "COMPONENT", "LAB_LOINC", "RAW_LAB_NAME", required=False)
    lab_fallback = column(lh, "LONG_COMMON_NAME", "RAW_LAB_NAME", "LAB_LOINC", required=False)
    lab_unit = source_column("l", lab_unit)
    lab_name = source_column("l", lab_name)
    lab_fallback = source_column("l", lab_fallback)
    con.execute(f"""CREATE TEMP TABLE labs AS SELECT TRIM(l.{lab_pid}) patient_id,
        {date_expr('l.' + lab_date)} event_date,
        COALESCE(NULLIF(TRIM({lab_name}), ''), NULLIF(TRIM({lab_fallback}), ''), 'Unnamed laboratory test') event_name,
        TRIM(l.{lab_num}) lab_value, COALESCE(TRIM({lab_unit}), '') lab_unit
        FROM labs_raw l JOIN outpatient_enc e ON TRIM(l.{lab_enc})=e.encounter_id
        AND TRIM(l.{lab_pid})=e.patient_id
        WHERE {date_expr('l.' + lab_date)} IS NOT NULL AND TRY_CAST(l.{lab_num} AS DOUBLE) IS NOT NULL""")
    rx_pid = column(rh, "ID", "PATID")
    rx_enc = column(rh, "ENCOUNTERID")
    rx_date = column(rh, "RX_ORDER_DATE", "RX_START_DATE")
    rx_start = column(rh, "RX_START_DATE", required=False)
    rx_code = column(rh, "RXNORM_CUI", "RXCUI", required=False)
    rx_name = column(rh, "RAW_RX_MED_NAME", "RAW_RX_MED_NAME", required=False)
    rx_start = source_column("r", rx_start)
    rx_code = source_column("r", rx_code)
    rx_name = source_column("r", rx_name)
    con.execute(f"""CREATE TEMP TABLE meds AS SELECT TRIM(r.{rx_pid}) patient_id,
        COALESCE({date_expr('r.' + rx_date)}, {date_expr(rx_start)}) event_date,
        COALESCE(TRIM({rx_code}), '') code, COALESCE(TRIM({rx_name}), '') event_name
        FROM prescribing_raw r JOIN outpatient_enc e ON TRIM(r.{rx_enc})=e.encounter_id
        AND TRIM(r.{rx_pid})=e.patient_id
        WHERE COALESCE({date_expr('r.' + rx_date)}, {date_expr(rx_start)}) IS NOT NULL""")
    demo_pid = column(mh, "ID", "PATID")
    demo_birth = column(mh, "BIRTH_DATE", "BIRTH_DATE_YR", required=False)
    demo_sex = column(mh, "SEX", required=False)
    demo_race = column(mh, "RACE", required=False)
    con.execute(f"""CREATE TEMP TABLE demo AS SELECT TRIM({demo_pid}) patient_id,
        {date_expr(demo_birth)} birth_date, TRIM({demo_sex}) sex,
        TRIM({demo_race}) race FROM demographics_raw""")


def render_profile(patient: str, disease: str, timepoint: str, age: int | None,
                   sex: str, race: str, events: list[tuple], include_dates: bool,
                   index_date: date, snapshot_date: date) -> str:
    latest: dict[str, tuple] = {}
    labs: dict[str, list[tuple]] = defaultdict(list)
    diagnoses: list[tuple] = []
    meds: list[tuple] = []
    for kind, event_date, name, value, unit, code in events:
        if event_date > snapshot_date or event_date < index_date:
            continue
        if kind == "lab":
            labs[name].append((event_date, value, unit))
            if name not in latest or event_date >= latest[name][0]:
                latest[name] = (event_date, value, unit)
        elif kind == "diagnosis":
            diagnoses.append((event_date, code, name))
        else:
            meds.append((event_date, code, name))
    lines = ["LABGENIE CLINICAL PROFILE", "Research use only", "", f"Profile ID: {patient}",
             f"Condition: {DISEASES[disease]} ({disease})", f"Timepoint: {timepoint}",
             f"Days after index: {SCHEDULE[timepoint]}",
             f"Age at snapshot: {age if age is not None else 'unknown'}", f"Sex: {sex}",
             f"Race: {race}"]
    if include_dates:
        lines.extend([f"Index date: {index_date.isoformat()}", f"Snapshot date: {snapshot_date.isoformat()}"])
    lines.extend(["", "CURRENT LABORATORY RESULTS (latest per test)"])
    for name in sorted(latest):
        day, value, unit = latest[name]
        lines.append(f"{day.isoformat() + ' | ' if include_dates else ''}{name} | {value} {unit}".rstrip())
    if not latest:
        lines.append("None available.")
    lines.extend(["", "LABORATORY TRENDS (index through snapshot)"])
    for name in sorted(labs):
        for day, value, unit in sorted(labs[name], reverse=True):
            lines.append(f"{day.isoformat() + ' | ' if include_dates else ''}{name} | {value} {unit}".rstrip())
    if not labs:
        lines.append("None available.")
    lines.extend(["", "MEDICATION RECORDS (index through snapshot)"])
    lines.extend(f"{day.isoformat() + ' | ' if include_dates else ''}{code} | {name}" for day, code, name in sorted(meds, reverse=True))
    if not meds:
        lines.append("None available.")
    lines.extend(["", "DIAGNOSES (index through snapshot)"])
    lines.extend(f"{day.isoformat() + ' | ' if include_dates else ''}{code} | {name}" for day, code, name in sorted(diagnoses, reverse=True))
    if not diagnoses:
        lines.append("None available.")
    lines.extend(["", "Generated mechanically from CSV records. No urgency or clinical interpretation was inferred."])
    return "\n".join(lines) + "\n"


def build(input_dir: Path, output_dir: Path, timepoints: list[str], diseases: list[str],
          salt: str, include_dates: bool = False, memory_limit: str = "4GB",
          temp_dir: Path | None = None) -> dict[str, int]:
    paths = find_files(input_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    try:
        con.execute(f"SET memory_limit = '{memory_limit.replace(chr(39), '')}'")
        if temp_dir:
            temp_dir.mkdir(parents=True, exist_ok=True)
            con.execute(f"SET temp_directory = {path_literal(temp_dir)}")
        setup(con, paths)
        index_cursor = con.execute("SELECT patient_id, code, event_date FROM dx ORDER BY patient_id, event_date, code")
        indices: dict[str, dict[str, date]] = defaultdict(dict)
        while rows := index_cursor.fetchmany(50_000):
            for patient_id, code, event_date in rows:
                disease = disease_for(code)
                if disease in diseases and disease not in indices[patient_id]:
                    indices[patient_id][disease] = event_date
        demos = {row[0]: row[1:] for row in con.execute("SELECT patient_id, birth_date, sex, race FROM demo").fetchall()}
        con.execute("CREATE TEMP TABLE selected_patients(patient_id VARCHAR)")
        con.executemany("INSERT INTO selected_patients VALUES (?)", [(x,) for x in indices]) if indices else None
        cursor = con.execute("""SELECT patient_id, kind, event_date, event_name, lab_value, lab_unit, code FROM (
            SELECT d.patient_id, 'diagnosis' kind, d.event_date, d.event_name, '' lab_value, '' lab_unit, d.code FROM dx d JOIN selected_patients s USING(patient_id)
            UNION ALL SELECT l.patient_id, 'lab', l.event_date, l.event_name, l.lab_value, l.lab_unit, '' FROM labs l JOIN selected_patients s USING(patient_id)
            UNION ALL SELECT m.patient_id, 'medication', m.event_date, m.event_name, '', '', m.code FROM meds m JOIN selected_patients s USING(patient_id)
        ) ORDER BY patient_id, event_date, kind""")
        manifest: list[dict[str, str | int]] = []
        count = 0

        def write_patient(patient_id: str, events: list[tuple]) -> None:
            nonlocal count
            birth, raw_sex, raw_race = demos.get(patient_id, (None, None, None))
            public_id = alias(patient_id, salt)
            for disease, index_date in indices[patient_id].items():
                for timepoint in timepoints:
                    snapshot_date = index_date + timedelta(days=SCHEDULE[timepoint])
                    eligible = [x for x in events if index_date <= x[1] <= snapshot_date]
                    if not any(x[0] == "lab" for x in eligible):
                        continue
                    profile = render_profile(public_id, disease, timepoint,
                                             age_on(birth, snapshot_date), display_sex(raw_sex),
                                             display_race(raw_race), eligible, include_dates,
                                             index_date, snapshot_date)
                    folder = output_dir / disease / timepoint
                    folder.mkdir(parents=True, exist_ok=True)
                    destination = folder / f"{public_id}_{disease}_{timepoint}.txt"
                    destination.write_text(profile, encoding="utf-8")
                    manifest.append({"profile_id": public_id, "disease": disease,
                                     "timepoint": timepoint, "days_after_index": SCHEDULE[timepoint],
                                     "profile_path": destination.relative_to(output_dir).as_posix(),
                                     "lab_records": sum(x[0] == "lab" for x in eligible),
                                     "diagnosis_records": sum(x[0] == "diagnosis" for x in eligible),
                                     "medication_records": sum(x[0] == "medication" for x in eligible)})
                    count += 1

        current_id: str | None = None
        current_events: list[tuple] = []
        while batch := cursor.fetchmany(50_000):
            for patient_id, kind, event_date, name, value, unit, code in batch:
                if current_id is not None and patient_id != current_id:
                    write_patient(current_id, current_events)
                    current_events = []
                current_id = patient_id
                current_events.append((kind, event_date, name, value, unit, code))
        if current_id is not None:
            write_patient(current_id, current_events)
        with (output_dir / "manifest.csv").open("w", encoding="utf-8", newline="") as stream:
            fields = ["profile_id", "disease", "timepoint", "days_after_index", "profile_path", "lab_records", "diagnosis_records", "medication_records"]
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(manifest)
        return {"patients": len(indices), "profiles": count}
    finally:
        con.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", required=True, type=Path, help="Directory with all five PCORnet-style outpatient CSV tables")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--timepoints", nargs="+", choices=list(SCHEDULE), default=list(SCHEDULE))
    parser.add_argument("--diseases", nargs="+", choices=list(DISEASES), default=list(DISEASES))
    parser.add_argument("--salt-env", default="LABGENIE_PROFILE_SALT", help="Environment variable for HMAC pseudonyms")
    parser.add_argument("--include-dates", action="store_true", help="Include dates in local outputs; never publish those outputs")
    parser.add_argument("--memory-limit", default="4GB")
    parser.add_argument("--temp-dir", type=Path)
    args = parser.parse_args()
    salt = os.environ.get(args.salt_env)
    if not salt or len(salt) < 16:
        parser.error(f"Set {args.salt_env} to a private random string of at least 16 characters")
    result = build(args.input_dir, args.output_dir, args.timepoints, args.diseases,
                   salt, args.include_dates, args.memory_limit, args.temp_dir)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
