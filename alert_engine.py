"""
Path A rules engine — Customer Behavior alert family (scenarios 1, 3, 9, 10).

Pure structured aggregation over alerts_db.DemandEvent: no LLM, no
embeddings. This module answers "does this customer (or this
customer+technology combination) have a track record worth flagging right
now" using counts and averages only. See SWP_Functional_Requirements
"Contextual Demand Intelligence" section for how this fits the wider
No-RAG / RAG split.

Two granularities are always computed independently (per the confirmed
design decision): customer-level and customer+primary-technology-level.
Either, both, or neither may cross the alert threshold for a given entry.
"""
import statistics

import alerts_db as adb

MIN_SAMPLE_SIZE = 3          # below this, never alert — not enough history to trust
ADVERSE_RATE_THRESHOLD = 0.4  # alert if >= 40% of events were postponed/reduced/cancelled

RISK_HIGH = "High Risk"
RISK_MODERATE = "Moderate Risk"


def _compute_profile(events):
    """Given a list of DemandEvent rows (already scoped to the desired
    granularity), compute the summary stats an alert is built from.
    Returns None if there isn't enough history to say anything useful."""
    sample_size = len(events)
    if sample_size < MIN_SAMPLE_SIZE:
        return None

    adverse = [e for e in events if e.event_type in adb.ADVERSE_EVENT_TYPES]
    adverse_rate = len(adverse) / sample_size

    shifts = [e.lead_time_days for e in events if e.event_type == "Postponed" and e.lead_time_days is not None]
    avg_shift_days = round(statistics.mean(shifts)) if shifts else None

    reductions = [
        (e.qty_before - e.qty_after) / e.qty_before
        for e in events
        if e.event_type == "Reduced" and e.qty_before and e.qty_after is not None and e.qty_before > 0
    ]
    avg_reduction_pct = round(statistics.mean(reductions) * 100, 1) if reductions else None

    return {
        "sample_size": sample_size,
        "adverse_count": len(adverse),
        "adverse_rate": adverse_rate,
        "avg_shift_days": avg_shift_days,
        "avg_reduction_pct": avg_reduction_pct,
        "recent_events": events[:5],  # already ordered most-recent-first by the query
    }


def _risk_level(adverse_rate):
    if adverse_rate >= 0.6:
        return RISK_HIGH
    return RISK_MODERATE


def _build_alert(customer, primary_technology, granularity, profile):
    scope_label = customer if granularity == adb.GRANULARITY_CUSTOMER else f"{customer} — {primary_technology}"
    pct = round(profile["adverse_rate"] * 100)

    headline = f"{scope_label} has a pattern of postponing or reducing demand"

    body_parts = [
        f"Based on {profile['sample_size']} logged demand events, "
        f"{pct}% ({profile['adverse_count']} of {profile['sample_size']}) were postponed, "
        f"reduced, or cancelled."
    ]
    if profile["avg_shift_days"]:
        body_parts.append(f"Postponements shifted by an average of {profile['avg_shift_days']} days.")
    if profile["avg_reduction_pct"]:
        body_parts.append(f"Reductions cut quantity by an average of {profile['avg_reduction_pct']}%.")
    body = " ".join(body_parts)

    evidence = []
    for e in profile["recent_events"]:
        line = f"{e.event_date.strftime('%b %Y')} — {e.event_type}"
        if e.event_type == "Postponed" and e.lead_time_days:
            line += f" ({e.lead_time_days} days)"
        if e.event_type == "Reduced" and e.qty_delta:
            line += f" ({e.qty_delta:+d})"
        if e.reason_category:
            line += f", reason: \"{e.reason_category}\""
        evidence.append(line)

    recommendation = (
        "Consider phasing onboarding or holding a buffer before committing resources."
        if profile["adverse_rate"] >= 0.6 else
        "Worth a quick check with the account team before finalizing resourcing."
    )

    confidence = "High" if profile["sample_size"] >= 6 else "Medium"

    return {
        "customer": customer,
        "primary_technology": primary_technology,
        "granularity": granularity,
        "risk_level": _risk_level(profile["adverse_rate"]),
        "headline": headline,
        "body": body,
        "evidence": evidence,
        "recommendation": recommendation,
        "confidence": confidence,
        "sample_size": profile["sample_size"],
    }


def get_customer_behavior_alerts(customer, primary_technology=None):
    """Returns a list of 0-2 alert dicts: one for the customer-level
    profile, one for the customer+technology profile (only if
    primary_technology is given and it independently crosses threshold).
    Does not log anything — call log_shown_alert() separately for each
    alert actually displayed, once the caller decides to render it."""
    alerts = []

    cust_events = adb.get_events_for_customer(customer)
    cust_profile = _compute_profile(cust_events)
    if cust_profile and cust_profile["adverse_rate"] >= ADVERSE_RATE_THRESHOLD:
        alerts.append(_build_alert(customer, None, adb.GRANULARITY_CUSTOMER, cust_profile))

    if primary_technology:
        tech_events = adb.get_events_for_customer(customer, primary_technology=primary_technology)
        tech_profile = _compute_profile(tech_events)
        if tech_profile and tech_profile["adverse_rate"] >= ADVERSE_RATE_THRESHOLD:
            alerts.append(_build_alert(customer, primary_technology, adb.GRANULARITY_CUSTOMER_TECH, tech_profile))

    return alerts


def log_shown_alert(alert, plan_id=None, family="customer_behavior"):
    """Persists an alert that was actually rendered to the planner.
    Returns the alert_log row id, used later to record Useful/Not Useful
    feedback against the same row."""
    import json
    return adb.log_alert(
        customer=alert["customer"],
        primary_technology=alert["primary_technology"],
        granularity=alert["granularity"],
        risk_level=alert["risk_level"],
        headline=alert["headline"],
        evidence_json=json.dumps(alert["evidence"]),
        recommendation=alert["recommendation"],
        confidence=alert["confidence"],
        sample_size=alert["sample_size"],
        plan_id=plan_id,
        family=family,
    )


# ---------------------------------------------------------------- Estimation Basis (scenarios 2, 12a, 13-15)

def compute_estimation_bias(metric, customer=None):
    """Structured variance analysis — planned vs. actual for a given
    metric, optionally scoped to one customer. No alert-threshold gating
    here (this is presented as a standing dashboard view, not a
    point-of-entry interruption — see functional document Section 3,
    Estimation Basis family note)."""
    rows = adb.get_actuals_for_metric(metric, customer=customer)
    if not rows:
        return None
    variances_pct = [r.variance_pct for r in rows if r.variance_pct is not None]
    if not variances_pct:
        return None
    return {
        "metric": metric,
        "customer": customer,
        "sample_size": len(rows),
        "avg_variance_pct": round(statistics.mean(variances_pct), 1),
        "direction": "over-estimated" if statistics.mean(variances_pct) < 0 else "under-estimated",
        "rows": rows[:5],
    }


# ---------------------------------------------------------------- Profile-to-text bridge (Step 3 / RAG)

def summarize_customer_profile_text(customer):
    """Builds a short natural-language description of a customer's
    logged demand-event behavior, suitable for embedding. This is the
    bridge between Path A (structured counts) and Path B (semantic
    search): raw DemandEvent notes describe *why* something happened,
    but never the aggregate pattern ("postponed 4 of 6 times"), so
    without this, ad hoc questions like "which customers postpone
    often?" asked via Similarity Search would find nothing relevant even
    though Path A already knows the answer. Returns None if the customer
    doesn't clear MIN_SAMPLE_SIZE — same threshold as the live alert, so
    a profile summary is never embedded before there's enough history to
    say anything meaningful about it."""
    events = adb.get_events_for_customer(customer)
    profile = _compute_profile(events)
    if profile is None:
        return None

    pct = round(profile["adverse_rate"] * 100)
    parts = [
        f"{customer}: {profile['sample_size']} demand events logged, "
        f"{pct}% ({profile['adverse_count']} of {profile['sample_size']}) postponed, reduced, or cancelled."
    ]
    if profile["avg_shift_days"]:
        parts.append(f"Average postponement shift: {profile['avg_shift_days']} days.")
    if profile["avg_reduction_pct"]:
        parts.append(f"Average reduction when reduced: {profile['avg_reduction_pct']}%.")
    return " ".join(parts)
