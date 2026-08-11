import io
import re

import pandas as pd
import streamlit as st

import database as db
from common import bootstrap, fmt_date, plan_banner

sys_cfg, plan, L = bootstrap("Function 4: Generate WFP")
SL_ON = sys_cfg.service_line_enabled
GR_ON = sys_cfg.grade_enabled
ST_ON = sys_cfg.secondary_technology_enabled

st.header("Function 4: Generate WFP")
plan_banner(plan)
st.caption("Read-only validation. Mismatches are informational and do not block anything.")

NUMERIC_KEYS = ["demand_count", "rampdown", "release_to_other_accounts", "attrition",
                 "gross_demand", "net_demand", "ep", "ba", "internal", "trainee", "total_supply"]


def safe_filename(name):
    cleaned = re.sub(r'[\\/:*?"<>|]', "_", name).strip()
    return cleaned if cleaned else "WFP"


if plan is None:
    st.warning("No active Workforce Plan. Set one up in Application Configuration first.")
elif st.button("Generate WFP", type="primary"):
    db.mark_plan_generated()
    rows = db.all_rows(plan.id)
    total = len(rows)
    mismatches = sum(1 for r in rows if r.status == "Mismatch")

    if total == 0:
        st.info("No records yet.")
    else:
        if mismatches > 0:
            st.error(f"{mismatches} mismatch(es) found out of {total} records.")
        else:
            st.success(f"No mismatches found — all {total} records match.")

        tab_summary, tab_detail = st.tabs(["Summary", "Detail"])

        with tab_summary:
            data = []
            by_customer = {}
            for r in rows:
                c = by_customer.setdefault(r.customer, {"demand": 0, "net": 0, "gross": 0, "supply": 0, "mismatch": False})
                c["demand"] += r.demand_count
                c["net"] += r.net_demand
                c["gross"] += r.gross_demand
                c["supply"] += r.total_supply
                # Rolled up from the underlying row status, not recomputed from
                # the aggregated totals — two mismatched rows for the same
                # customer can offset each other in the sum (e.g. +5 / -5) and
                # look matched in aggregate while still needing review at the
                # row level. This flag reflects "does this customer have any
                # row currently in Mismatch," which is what Function 2 and the
                # Detail tab both key off of.
                if r.status == "Mismatch":
                    c["mismatch"] = True

            customer_mismatch = {cust: v["mismatch"] for cust, v in by_customer.items()}
            for cust, v in by_customer.items():
                data.append({
                    L["customer"]: cust, L["demand_count"]: v["demand"], L["net_demand"]: v["net"],
                    L["gross_demand"]: v["gross"], "Gross Supply": v["supply"],
                })

            summary_df = pd.DataFrame(data)
            numeric_cols = [c for c in summary_df.columns if c != L["customer"]]

            total_row_index = len(summary_df)  # appended below; used to identify the Total row for styling
            if not summary_df.empty:
                totals = {L["customer"]: "Total"}
                totals.update({col: summary_df[col].sum() for col in numeric_cols})
                summary_df = pd.concat([summary_df, pd.DataFrame([totals])], ignore_index=True)

            def highlight_summary(row):
                if row.name == total_row_index:
                    return ["background-color: #eef0f3; font-weight: 700"] * len(row)
                if customer_mismatch.get(row[L["customer"]], False):
                    return ["background-color: #fcebeb"] * len(row)
                return [""] * len(row)

            st.dataframe(
                summary_df.style.apply(highlight_summary, axis=1)
                          .set_properties(subset=numeric_cols, **{"text-align": "center"}),
                use_container_width=True,
            )
            if any(customer_mismatch.values()):
                st.caption(
                    "Rows highlighted in red belong to a customer with at least one Mismatch line "
                    "(see Detail tab) — the customer's totals above may still look matched even so, "
                    "since individual mismatches can offset each other in the sum."
                )
            st.caption("This view is on-screen only and is not included in the download.")

        with tab_detail:
            detail_rows = []
            for r in rows:
                # Requirement IOU / Sub IOU shown right after Customer.
                d = {
                    L["customer"]: r.customer,
                    L["requirement_iou"]: r.requirement_iou,
                    L["requirement_sub_iou"]: r.requirement_sub_iou,
                    L["location"]: r.location,
                    L["start_date"]: fmt_date(r.start_date),
                    L["primary_technology"]: r.primary_technology,
                }
                if ST_ON: d[L["secondary_technology"]] = r.secondary_technology
                if SL_ON: d[L["service_line"]] = r.service_line
                if GR_ON: d[L["grade"]] = r.grade
                d.update({
                    L["demand_count"]: r.demand_count, L["rampdown"]: r.rampdown,
                    L["release_to_other_accounts"]: r.release_to_other_accounts, L["attrition"]: r.attrition,
                    L["gross_demand"]: r.gross_demand, L["net_demand"]: r.net_demand,
                    L["ep"]: r.ep, L["ba"]: r.ba, L["internal"]: r.internal, L["trainee"]: r.trainee,
                    L["total_supply"]: r.total_supply, L["status"]: r.status,
                })
                detail_rows.append(d)
            detail_df = pd.DataFrame(detail_rows)

            numeric_labels = [L[k] for k in NUMERIC_KEYS if L[k] in detail_df.columns]

            def highlight_mismatch(row):
                color = "background-color: #fcebeb" if row[L["status"]] == "Mismatch" else ""
                return [color] * len(row)

            styled = (detail_df.style
                      .apply(highlight_mismatch, axis=1)
                      .set_properties(subset=numeric_labels, **{"text-align": "center"}))
            st.dataframe(styled, use_container_width=True)

        if mismatches == 0:
            buf = io.BytesIO()
            with pd.ExcelWriter(buf, engine="openpyxl") as writer:
                detail_df.to_excel(writer, index=False, sheet_name="WFP Detail")
            plan_label = plan.plan_name if plan.plan_name else "WFP"
            file_name = f"{safe_filename(plan_label)}.xlsx"
            st.download_button(
                f"Download {plan_label}.xlsx", data=buf.getvalue(),
                file_name=file_name,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
