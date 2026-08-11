"""
Master data — loaded from master_data.xlsx (single file, multiple sheets)
in the same folder as this file. This is the file to edit when customers,
locations, technologies, grades, or service lines need to change; no code
edits are needed for master data changes.

Sheets expected in master_data.xlsx:
  - Customers              : Customer | Requirement IOU | Requirement Sub IOU
  - Locations               : Location | Country | Onsite (Yes/No — optional;
                               defaults to No/offshore if column or value is
                               missing, feeds the Onsite Demand estimation-bias
                               metric)
  - PrimaryTechnologies       : Primary Technology
  - SecondaryTechnologies      : Secondary Technology
  - Grades                  : Grade
  - ServiceLines              : Service Line
  - ReasonCategories          : Reason Category (used by Log a Demand Event)

After editing master_data.xlsx, restart the Streamlit app to pick up changes
(the file is read once, at import time).
"""
import os

import pandas as pd

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MASTER_DATA_FILE = os.path.join(_BASE_DIR, "master_data.xlsx")

if not os.path.exists(MASTER_DATA_FILE):
    raise FileNotFoundError(
        f"Master data file not found at {MASTER_DATA_FILE}. "
        "Place master_data.xlsx in the same folder as Home.py."
    )

_customers_df = pd.read_excel(MASTER_DATA_FILE, sheet_name="Customers")
_locations_df = pd.read_excel(MASTER_DATA_FILE, sheet_name="Locations")
_primary_tech_df = pd.read_excel(MASTER_DATA_FILE, sheet_name="PrimaryTechnologies")
_secondary_tech_df = pd.read_excel(MASTER_DATA_FILE, sheet_name="SecondaryTechnologies")
_grades_df = pd.read_excel(MASTER_DATA_FILE, sheet_name="Grades")
_service_lines_df = pd.read_excel(MASTER_DATA_FILE, sheet_name="ServiceLines")
_reason_categories_df = pd.read_excel(MASTER_DATA_FILE, sheet_name="ReasonCategories")

CUSTOMERS = {
    str(row["Customer"]).strip(): {
        "iou": str(row["Requirement IOU"]).strip(),
        "sub_iou": str(row["Requirement Sub IOU"]).strip(),
    }
    for _, row in _customers_df.dropna(subset=["Customer"]).iterrows()
}

LOCATIONS = {
    str(row["Location"]).strip(): str(row["Country"]).strip()
    for _, row in _locations_df.dropna(subset=["Location"]).iterrows()
}

# Onsite is optional in the sheet — defaults to "No" (offshore) if the
# column or a specific row's value is missing, so older master_data.xlsx
# files without this column don't break on load.
_ONSITE_COL_PRESENT = "Onsite" in _locations_df.columns
LOCATION_ONSITE = {
    str(row["Location"]).strip(): (str(row.get("Onsite", "No")).strip().lower() == "yes")
    for _, row in _locations_df.dropna(subset=["Location"]).iterrows()
} if _ONSITE_COL_PRESENT else {}

PRIMARY_TECHNOLOGIES = _primary_tech_df["Primary Technology"].dropna().astype(str).str.strip().tolist()
SECONDARY_TECHNOLOGIES = _secondary_tech_df["Secondary Technology"].dropna().astype(str).str.strip().tolist()
GRADES = _grades_df["Grade"].dropna().astype(str).str.strip().tolist()
SERVICE_LINES = _service_lines_df["Service Line"].dropna().astype(str).str.strip().tolist()
REASON_CATEGORIES = _reason_categories_df["Reason Category"].dropna().astype(str).str.strip().tolist()


def get_customers():
    return sorted(CUSTOMERS.keys())


def get_customer_info(name):
    return CUSTOMERS.get(name, {"iou": "", "sub_iou": ""})


def get_locations():
    return sorted(LOCATIONS.keys())


def get_country(location):
    return LOCATIONS.get(location, "")


def is_onsite(location):
    """True if the location is flagged Onsite in master_data.xlsx.
    Defaults to False (offshore) if unset or the sheet predates this
    column — see the Locations sheet's Onsite column to correct."""
    return LOCATION_ONSITE.get(location, False)


def get_reason_categories():
    return REASON_CATEGORIES

