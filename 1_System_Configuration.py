import streamlit as st

import database as db
from common import bootstrap

sys_cfg, plan, L = bootstrap("System Configuration")

st.header("System configuration")

if sys_cfg.locked:
    st.info("System configuration is locked. Its parameters are not displayed here — "
             "see **Home** for a status summary.")
else:
    st.caption("One-time decision: choose which optional columns are structurally active, and set "
               "the column display names used across the whole application. Once saved, none of "
               "this can be changed.")

    st.subheader("Optional columns")
    sl = st.checkbox(L["service_line"], value=True)
    gr = st.checkbox(L["grade"], value=True)
    st_tech = st.checkbox(L["secondary_technology"], value=True)

    st.divider()
    st.subheader("Column names")
    st.caption("Default column names are shown below. Rename any of them — once saved, "
               "these are locked permanently along with the toggles above.")
    new_labels = {}
    keys = list(db.DEFAULT_LABELS.keys())
    for i in range(0, len(keys), 2):
        c1, c2 = st.columns(2)
        k1 = keys[i]
        new_labels[k1] = c1.text_input(f"{db.DEFAULT_LABELS[k1]} (default)", value=L.get(k1, db.DEFAULT_LABELS[k1]), key=f"lbl_{k1}")
        if i + 1 < len(keys):
            k2 = keys[i + 1]
            new_labels[k2] = c2.text_input(f"{db.DEFAULT_LABELS[k2]} (default)", value=L.get(k2, db.DEFAULT_LABELS[k2]), key=f"lbl_{k2}")

    st.warning("This is a one-time decision. Once you save, the toggles and column names above are locked permanently.")
    if st.button("Save system configuration", type="primary"):
        db.save_system_config(sl, gr, st_tech, new_labels)
        st.success("System configuration saved and locked.")
        st.rerun()
