import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

import database as db
from common import bootstrap, plan_banner

sys_cfg, plan, L = bootstrap("Function 5: Analytics")

# Page-scoped layout compression — reduces the padding Streamlit adds
# around the block container and between stacked elements, so the four
# tabs fit with noticeably less scrolling. Injected fresh on every run of
# this page's script, so it doesn't leak into other pages.
st.markdown("""
<style>
.block-container { padding-top: 1.2rem !important; padding-bottom: 1rem !important; }
div[data-testid="stVerticalBlock"] > div.element-container { margin-bottom: 0.35rem !important; }
</style>
""", unsafe_allow_html=True)

st.header("Function 5: Analytics")
plan_banner(plan)

CHANNELS = ["ep", "ba", "internal", "trainee"]
CHART_HEIGHT = 300
CHART_MARGIN = dict(l=10, r=10, t=30, b=10)


def region_filter_widget(df, key_prefix):
    """India / Non-India filter, derived from the row's Country field
    (auto-populated from Location in Function 1/3 — see Data Model).
    Returns the filtered dataframe."""
    region = st.radio(
        "Region", ["All", "India", "Non-India"], horizontal=True, key=f"{key_prefix}_region"
    )
    if region == "India":
        return df[df["country"] == "India"]
    if region == "Non-India":
        return df[df["country"] != "India"]
    return df


def customer_filter_widget(df, key_prefix):
    """Customer filter — multiselect so more than one customer can be
    compared at once; empty selection means no filter (show all)."""
    customers = sorted(df["customer"].dropna().unique().tolist())
    selected = st.multiselect(
        L["customer"], customers, default=[], key=f"{key_prefix}_customer",
        placeholder="All customers (leave empty for no filter)",
    )
    if selected:
        return df[df["customer"].isin(selected)]
    return df


if plan is None:
    st.warning("No active Workforce Plan. Set one up in Application Configuration first.")
else:
    rows = db.all_rows(plan.id)
    if not rows:
        st.info("No data yet in this plan. Enter demand, supply, or rampdown/attrition first.")
    else:
        df = pd.DataFrame([{
            "customer": r.customer,
            "country": r.country,
            "primary_technology": r.primary_technology,
            "ep": r.ep,
            "ba": r.ba,
            "internal": r.internal,
            "trainee": r.trainee,
            "gross_demand": r.gross_demand,
            "total_supply": r.total_supply,
        } for r in rows])

        tab1, tab2, tab3, tab4 = st.tabs([
            "Sourcing Mix by Technology",
            "Customer Concentration (Pareto)",
            "Customer × Technology Heatmap",
            "Dynamics",
        ])

        # ---------------------------------------------------- Metric 1
        with tab1:
            st.caption(f"% of {L['gross_demand']} per {L['primary_technology']}, by Sourcing Channel.")
            with st.expander("Details"):
                st.write(
                    f"% of {L['gross_demand']} contributed by each Sourcing Channel "
                    f"({L['ep']}, {L['ba']}, {L['internal']}, {L['trainee']}), per {L['primary_technology']}. "
                    f"Bars can total more or less than 100% for technologies with Mismatch rows "
                    f"(Total Supply ≠ Gross Demand) — that's expected, not an error."
                )

            fc1, fc2 = st.columns(2)
            with fc1:
                df1 = region_filter_widget(df, "f5t1")
            with fc2:
                df1 = customer_filter_widget(df1, "f5t1")

            if df1.empty:
                st.info("No rows match the selected filters.")
            else:
                grp = df1.groupby("primary_technology")[CHANNELS + ["gross_demand"]].sum().reset_index()
                safe_gross = grp["gross_demand"].replace(0, 1)
                for ch in CHANNELS:
                    grp[ch + "_pct"] = grp[ch] / safe_gross * 100

                channel_label_map = {f"{ch}_pct": L[ch] for ch in CHANNELS}
                count_map = {ch: L[ch] for ch in CHANNELS}
                melt = grp.melt(
                    id_vars=["primary_technology", "gross_demand"] + CHANNELS,
                    value_vars=[f"{ch}_pct" for ch in CHANNELS],
                    var_name="channel_key", value_name="pct",
                )
                melt["channel"] = melt["channel_key"].map(channel_label_map)
                melt["count"] = melt.apply(lambda row: row[row["channel_key"].replace("_pct", "")], axis=1)

                fig1 = px.bar(
                    melt, x="primary_technology", y="pct", color="channel", barmode="stack",
                    text=melt["count"].astype(int).astype(str),
                    labels={"primary_technology": L["primary_technology"], "pct": f"% of {L['gross_demand']}", "channel": "Sourcing Channel"},
                    hover_data={"count": True, "pct": ":.1f"},
                )
                fig1.update_traces(textposition="inside")
                fig1.update_layout(height=CHART_HEIGHT, margin=CHART_MARGIN, legend=dict(orientation="h", yanchor="bottom", y=1.02))
                st.plotly_chart(fig1, use_container_width=True)

                display_cols = ["primary_technology"] + CHANNELS + ["gross_demand"]
                display_df = grp[display_cols].rename(columns={
                    "primary_technology": L["primary_technology"], **count_map, "gross_demand": L["gross_demand"],
                })
                st.dataframe(display_df, use_container_width=True, hide_index=True, height=140)

                with st.expander("Underlying data (with %)"):
                    st.dataframe(grp, use_container_width=True, height=140)

        # ---------------------------------------------------- Metric 2
        with tab2:
            st.caption(f"Total {L['gross_demand']} per {L['customer']}, ranked, with cumulative %.")

            cust = (df.groupby("customer")["gross_demand"].sum()
                    .reset_index().sort_values("gross_demand", ascending=False).reset_index(drop=True))
            total_demand = max(cust["gross_demand"].sum(), 1)
            cust["cumulative_pct"] = cust["gross_demand"].cumsum() / total_demand * 100

            fig2 = go.Figure()
            fig2.add_bar(x=cust["customer"], y=cust["gross_demand"], name=L["gross_demand"])
            fig2.add_scatter(
                x=cust["customer"], y=cust["cumulative_pct"], name="Cumulative %",
                yaxis="y2", mode="lines+markers",
            )
            fig2.update_layout(
                height=CHART_HEIGHT, margin=CHART_MARGIN,
                xaxis=dict(title=L["customer"]),
                yaxis=dict(title=L["gross_demand"]),
                yaxis2=dict(title="Cumulative %", overlaying="y", side="right", range=[0, 100]),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            )
            st.plotly_chart(fig2, use_container_width=True)

            with st.expander("Underlying data"):
                st.dataframe(cust, use_container_width=True, height=140)

        # ---------------------------------------------------- Metric 3
        with tab3:
            st.caption(f"{L['gross_demand']} for each {L['customer']}–{L['primary_technology']} combination.")

            heat = df.groupby(["customer", "primary_technology"])["gross_demand"].sum().reset_index()
            pivot = heat.pivot(index="customer", columns="primary_technology", values="gross_demand").fillna(0)

            fig3 = px.imshow(
                pivot,
                labels=dict(x=L["primary_technology"], y=L["customer"], color=L["gross_demand"]),
                color_continuous_scale="Blues", text_auto=True, aspect="auto",
            )
            fig3.update_layout(height=CHART_HEIGHT + 60, margin=CHART_MARGIN)
            st.plotly_chart(fig3, use_container_width=True)

            with st.expander("Underlying data"):
                st.dataframe(pivot, use_container_width=True, height=140)

        # ---------------------------------------------------- Metric 4 (new)
        with tab4:
            st.caption(f"Count of {L['gross_demand']} per Sourcing Channel, across all {L['primary_technology']}s.")

            fc3, fc4 = st.columns(2)
            with fc3:
                df4 = region_filter_widget(df, "f5t4")
            with fc4:
                df4 = customer_filter_widget(df4, "f5t4")

            if df4.empty:
                st.info("No rows match the selected filters.")
            else:
                totals = df4[CHANNELS].sum()
                dyn_df = pd.DataFrame({
                    "Sourcing Channel": [L[ch] for ch in CHANNELS],
                    "Count": [totals[ch] for ch in CHANNELS],
                })

                fig4 = px.bar(dyn_df, x="Sourcing Channel", y="Count", text="Count", color="Sourcing Channel")
                fig4.update_traces(textposition="outside")
                fig4.update_layout(height=CHART_HEIGHT, margin=CHART_MARGIN, showlegend=False)
                st.plotly_chart(fig4, use_container_width=True)

                with st.expander("Underlying data"):
                    st.dataframe(dyn_df, use_container_width=True, hide_index=True, height=140)
