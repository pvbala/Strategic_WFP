"""
Contextual Demand Intelligence — data layer.

Separate SQLite database (alerts.db), independent of the existing
swp_app.db, but following the exact same SQLAlchemy pattern used in
database.py. Kept separate so this can be removed, backed up, or migrated
to Postgres on its own schedule without touching the core WFP schema.

References back to swp_app.db (plan_id, wfp_row_id) are stored as plain
integers, not SQLAlchemy ForeignKeys — the two databases are independent
engines, so a cross-database FK constraint isn't possible with SQLite.
Referential integrity across the two is the caller's responsibility.

Covers three of the four scenario families defined in the functional
design document (Customer Behavior, Opportunity/Pipeline, Estimation
Basis). The fourth family (Similarity/Analogy) needs semantic retrieval
over free text and is deliberately out of scope until the LLM/embeddings
layer (Step 3) is introduced — see SWP_Functional_Requirements_F1-F4.md,
"Contextual Demand Intelligence" section, for the full mapping.
"""
import datetime

from sqlalchemy import create_engine, Column, Integer, String, Date, DateTime, Text, Boolean
from sqlalchemy.orm import declarative_base, sessionmaker

ALERTS_DATABASE_URL = "sqlite:///alerts.db"
engine = create_engine(ALERTS_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

# ---------------------------------------------------------------- Vocabulary

EVENT_TYPES = ["Postponed", "Reduced", "Cancelled", "Increased", "On Time", "Accelerated"]
ADVERSE_EVENT_TYPES = {"Postponed", "Reduced", "Cancelled"}

OPPORTUNITY_STAGES = ["Suspecting", "RFP", "Negotiation", "Won", "Lost"]
# Win probability is meaningful (and expected) once a stage reaches RFP;
# Suspecting-stage opportunities may legitimately have none yet (scenario 5).
STAGES_EXPECTING_PROBABILITY = {"RFP", "Negotiation"}

ESTIMATION_METRICS = [
    "Attrition", "Rampdown", "Release to Other Accounts",
    "EP", "BA", "Internal", "Trainee", "Onsite Demand",
]

GRANULARITY_CUSTOMER = "customer"
GRANULARITY_CUSTOMER_TECH = "customer_tech"


# ---------------------------------------------------------------- Models

class DemandEvent(Base):
    """A logged real-world change against previously entered demand —
    postponement, reduction, cancellation, increase, or confirmation that
    it landed on time. This is the evidence the Customer Behavior alert
    family (scenarios 1, 3, 9, 10) is computed from."""
    __tablename__ = "demand_events"
    id = Column(Integer, primary_key=True)

    customer = Column(String, nullable=False)
    primary_technology = Column(String, nullable=True)
    location = Column(String, nullable=True)

    event_type = Column(String, nullable=False)
    original_period = Column(Date, nullable=True)
    new_period = Column(Date, nullable=True)
    qty_before = Column(Integer, nullable=True)
    qty_after = Column(Integer, nullable=True)

    reason_category = Column(String, nullable=True)
    notes = Column(Text, nullable=True)

    linked_plan_id = Column(Integer, nullable=True)
    linked_wfp_row_id = Column(Integer, nullable=True)

    logged_by = Column(String, nullable=True)
    event_date = Column(Date, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    # Step 3 additions — set when this event was captured via free-text +
    # AI extraction rather than the structured form. human_verified is
    # True for every row reaching the database (structured entries are
    # inherently human-entered; AI-extracted entries are only saved after
    # explicit planner confirmation — see Log a Demand Event, AI mode).
    human_verified = Column(Boolean, default=True, nullable=False)
    extraction_provider = Column(String, nullable=True)  # "gemini" / "groq" / "ollama" / None

    @property
    def lead_time_days(self):
        if self.original_period and self.new_period:
            return (self.new_period - self.original_period).days
        return None

    @property
    def qty_delta(self):
        if self.qty_before is not None and self.qty_after is not None:
            return self.qty_after - self.qty_before
        return None


class Opportunity(Base):
    """Pre-demand pipeline entity (scenarios 4, 5). Not a WfpRow — this is
    intentionally a separate concept, since an opportunity may never
    convert to demand at all."""
    __tablename__ = "opportunity"
    id = Column(Integer, primary_key=True)

    customer = Column(String, nullable=False)
    primary_technology = Column(String, nullable=True)
    stage = Column(String, nullable=False, default="Suspecting")

    estimated_qty = Column(Integer, nullable=True)
    estimated_start_date = Column(Date, nullable=True)
    win_probability = Column(Integer, nullable=True)  # 0-100

    notes = Column(Text, nullable=True)
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    human_verified = Column(Boolean, default=True, nullable=False)
    extraction_provider = Column(String, nullable=True)


class ActualOutcome(Base):
    """Planned-vs-actual record for a single metric on a single WfpRow
    context. This is the evidence the Estimation Basis alert family
    (scenarios 2, 12a, 13, 14, 15) is computed from — it does not exist
    anywhere in the base WFP schema, since Functions 1-3 only ever record
    planned values."""
    __tablename__ = "actual_outcome"
    id = Column(Integer, primary_key=True)

    linked_wfp_row_id = Column(Integer, nullable=True)
    customer = Column(String, nullable=False)
    primary_technology = Column(String, nullable=True)
    location = Column(String, nullable=True)

    metric = Column(String, nullable=False)  # one of ESTIMATION_METRICS
    planned_value = Column(Integer, nullable=False)
    actual_value = Column(Integer, nullable=False)

    planner_id = Column(String, nullable=True)
    logged_by = Column(String, nullable=True)
    logged_at = Column(DateTime, default=datetime.datetime.utcnow)

    @property
    def variance(self):
        return self.actual_value - self.planned_value

    @property
    def variance_pct(self):
        if self.planned_value == 0:
            return None
        return round((self.actual_value - self.planned_value) / self.planned_value * 100, 1)


class AlertLog(Base):
    """Every alert actually shown to a planner, plus their feedback.
    Auditable evidence trail; also the basis for future threshold
    recalibration."""
    __tablename__ = "alert_log"
    id = Column(Integer, primary_key=True)

    customer = Column(String, nullable=False)
    primary_technology = Column(String, nullable=True)
    granularity = Column(String, nullable=False)  # GRANULARITY_CUSTOMER / GRANULARITY_CUSTOMER_TECH
    family = Column(String, nullable=False, default="customer_behavior")

    risk_level = Column(String, nullable=False)
    headline = Column(String, nullable=False)
    evidence_json = Column(Text, nullable=True)
    recommendation = Column(Text, nullable=True)
    confidence = Column(String, nullable=True)
    sample_size = Column(Integer, nullable=True)

    plan_id = Column(Integer, nullable=True)
    shown_at = Column(DateTime, default=datetime.datetime.utcnow)
    planner_action = Column(String, nullable=True)  # "useful" / "not_useful" / None


class EmbeddingRecord(Base):
    """Vector store, Step 3 (RAG). One row per piece of free text that's
    been embedded — DemandEvent.notes, Opportunity.notes, and (new)
    per-customer behavior-profile summaries (source_table =
    "customer_profile") — see alert_engine.summarize_customer_profile_text
    and rag_engine.refresh_customer_profile_embedding. The profile
    summaries are what let semantic/keyword search surface Path A-style
    structured patterns ("postponed 4 of 6 times") by meaning, which raw
    free-text notes alone can't do, since notes describe *why* something
    happened, not *how often*.
    embedding_json is a JSON-encoded list of floats; cosine similarity is
    computed in Python (rag_engine.py) rather than in SQL, since SQLite
    has no native vector extension at this scale. Migrating to pgvector
    later means moving this table's storage, not changing the retrieval
    logic that calls it. A companion FTS5 virtual table (embedding_fts,
    see below) mirrors the text column for exact/keyword search,
    kept in sync whenever a row is added or removed here."""
    __tablename__ = "embedding_record"
    id = Column(Integer, primary_key=True)

    source_table = Column(String, nullable=False)  # "demand_events" / "opportunity" / "customer_profile"
    source_id = Column(Integer, nullable=False)
    customer = Column(String, nullable=True)
    primary_technology = Column(String, nullable=True)

    text = Column(Text, nullable=False)
    embedding_json = Column(Text, nullable=False)
    embedding_provider = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class PrecomputedInsight(Base):
    """Batch-generated Similar Situations results — see the Precompute
    Similar Situations page. Distinct from a live, on-demand
    synthesize_similarity_insight() call: these are computed once (on
    request, not automatically) for every Suspecting-stage Opportunity
    and cached here, so a planner can browse them without waiting on a
    live search + LLM call for each one. Cleared and rebuilt in full on
    every precompute run — not incrementally updated, since staleness is
    surfaced via computed_at rather than tracked per-row."""
    __tablename__ = "precomputed_insight"
    id = Column(Integer, primary_key=True)

    source_opportunity_id = Column(Integer, nullable=True)
    customer = Column(String, nullable=False)
    primary_technology = Column(String, nullable=True)

    headline = Column(String, nullable=False)
    summary = Column(Text, nullable=False)
    caveat = Column(Text, nullable=True)
    evidence_json = Column(Text, nullable=True)
    provider = Column(String, nullable=True)
    alert_log_id = Column(Integer, nullable=True)  # links back to the AlertLog row logged at compute time, so feedback buttons keep working across page revisits without re-logging

    computed_at = Column(DateTime, default=datetime.datetime.utcnow)


def init_alerts_db():
    Base.metadata.create_all(engine)
    _migrate_add_columns()
    _ensure_fts_table()


def _migrate_add_columns():
    """create_all() only creates missing tables, not missing columns on
    existing tables. This adds this round's new columns to any alerts.db
    created by an earlier version of the app, idempotently — each ALTER
    is wrapped so an already-present column is silently skipped."""
    import sqlite3
    path = ALERTS_DATABASE_URL.replace("sqlite:///", "")
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    migrations = [
        ("demand_events", "human_verified", "BOOLEAN DEFAULT 1"),
        ("demand_events", "extraction_provider", "VARCHAR"),
        ("opportunity", "human_verified", "BOOLEAN DEFAULT 1"),
        ("opportunity", "extraction_provider", "VARCHAR"),
    ]
    for table, col, coltype in migrations:
        try:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists — fine
    conn.close()


def _ensure_fts_table():
    """FTS5 virtual table mirroring EmbeddingRecord.text, for exact/
    keyword search — a companion to the semantic (embedding) search, not
    a replacement. Embeddings are good at "different words, same
    meaning"; FTS5 is good at "this exact term appears," which embeddings
    can genuinely miss (an exact customer or technology name doesn't
    always embed as the closest match). hybrid_search() in rag_engine.py
    combines both. Kept in sync by add_embedding_record() and
    delete_embeddings_by_source() — never written to directly elsewhere.
    Also backfills any EmbeddingRecord rows created before this table
    existed (from an earlier version of the app), so upgrading doesn't
    leave older notes permanently unsearchable by keyword."""
    import sqlite3
    path = ALERTS_DATABASE_URL.replace("sqlite:///", "")
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS embedding_fts "
        "USING fts5(text, customer, primary_technology, embedding_record_id UNINDEXED)"
    )
    conn.commit()

    already_indexed = {row[0] for row in conn.execute(
        "SELECT DISTINCT embedding_record_id FROM embedding_fts"
    ).fetchall()}
    try:
        all_records = conn.execute(
            "SELECT id, text, customer, primary_technology FROM embedding_record"
        ).fetchall()
    except sqlite3.OperationalError:
        all_records = []  # embedding_record table doesn't exist yet on a brand-new db — fine, nothing to backfill
    to_backfill = [r for r in all_records if r[0] not in already_indexed]
    if to_backfill:
        conn.executemany(
            "INSERT INTO embedding_fts (text, customer, primary_technology, embedding_record_id) VALUES (?, ?, ?, ?)",
            [(r[1], r[2] or "", r[3] or "", r[0]) for r in to_backfill],
        )
        conn.commit()
    conn.close()


# ---------------------------------------------------------------- Demand Events

def add_demand_event(**kwargs):
    session = SessionLocal()
    event = DemandEvent(**kwargs)
    session.add(event)
    session.commit()
    event_id = event.id
    session.close()
    return event_id


def get_events_for_customer(customer, primary_technology=None):
    """primary_technology=None returns every event for the customer
    (used for the customer-level profile). Pass a value to scope to
    customer+technology (used for the finer-grained profile)."""
    session = SessionLocal()
    q = session.query(DemandEvent).filter(DemandEvent.customer == customer)
    if primary_technology:
        q = q.filter(DemandEvent.primary_technology == primary_technology)
    events = q.order_by(DemandEvent.event_date.desc()).all()
    session.expunge_all()
    session.close()
    return events


def get_all_events():
    session = SessionLocal()
    events = session.query(DemandEvent).order_by(DemandEvent.event_date.desc()).all()
    session.expunge_all()
    session.close()
    return events


def distinct_event_customers():
    session = SessionLocal()
    rows = session.query(DemandEvent.customer).distinct().all()
    session.close()
    return sorted({r[0] for r in rows})


# ---------------------------------------------------------------- Opportunities

def add_opportunity(**kwargs):
    session = SessionLocal()
    opp = Opportunity(**kwargs)
    session.add(opp)
    session.commit()
    opp_id = opp.id
    session.close()
    return opp_id


def update_opportunity(opp_id, **kwargs):
    session = SessionLocal()
    opp = session.query(Opportunity).filter(Opportunity.id == opp_id).first()
    if opp is None:
        session.close()
        raise ValueError("No such opportunity.")
    for k, v in kwargs.items():
        setattr(opp, k, v)
    session.commit()
    session.close()


def get_all_opportunities():
    session = SessionLocal()
    opps = session.query(Opportunity).order_by(Opportunity.updated_at.desc()).all()
    session.expunge_all()
    session.close()
    return opps


def get_opportunities_for_customer(customer):
    session = SessionLocal()
    opps = (session.query(Opportunity)
            .filter(Opportunity.customer == customer)
            .order_by(Opportunity.updated_at.desc()).all())
    session.expunge_all()
    session.close()
    return opps


# ---------------------------------------------------------------- Actual Outcomes

def add_actual_outcome(**kwargs):
    session = SessionLocal()
    row = ActualOutcome(**kwargs)
    session.add(row)
    session.commit()
    row_id = row.id
    session.close()
    return row_id


def get_actuals_for_metric(metric, customer=None):
    session = SessionLocal()
    q = session.query(ActualOutcome).filter(ActualOutcome.metric == metric)
    if customer:
        q = q.filter(ActualOutcome.customer == customer)
    rows = q.order_by(ActualOutcome.logged_at.desc()).all()
    session.expunge_all()
    session.close()
    return rows


def get_all_actuals():
    session = SessionLocal()
    rows = session.query(ActualOutcome).order_by(ActualOutcome.logged_at.desc()).all()
    session.expunge_all()
    session.close()
    return rows


# ---------------------------------------------------------------- Alert Log

def log_alert(customer, primary_technology, granularity, risk_level, headline,
              evidence_json, recommendation, confidence, sample_size, plan_id=None,
              family="customer_behavior"):
    session = SessionLocal()
    entry = AlertLog(
        customer=customer, primary_technology=primary_technology, granularity=granularity,
        family=family, risk_level=risk_level, headline=headline, evidence_json=evidence_json,
        recommendation=recommendation, confidence=confidence, sample_size=sample_size,
        plan_id=plan_id,
    )
    session.add(entry)
    session.commit()
    entry_id = entry.id
    session.close()
    return entry_id


def record_alert_feedback(alert_id, action):
    """action: 'useful' or 'not_useful'."""
    session = SessionLocal()
    entry = session.query(AlertLog).filter(AlertLog.id == alert_id).first()
    if entry is not None:
        entry.planner_action = action
        session.commit()
    session.close()


def get_recent_alerts(limit=50):
    session = SessionLocal()
    rows = session.query(AlertLog).order_by(AlertLog.shown_at.desc()).limit(limit).all()
    session.expunge_all()
    session.close()
    return rows


# ---------------------------------------------------------------- Embeddings (Step 3 / RAG)

def add_embedding_record(source_table, source_id, customer, primary_technology,
                          text, embedding_json, embedding_provider=None):
    session = SessionLocal()
    rec = EmbeddingRecord(
        source_table=source_table, source_id=source_id, customer=customer,
        primary_technology=primary_technology, text=text,
        embedding_json=embedding_json, embedding_provider=embedding_provider,
    )
    session.add(rec)
    session.commit()
    rec_id = rec.id
    session.close()
    _fts_insert(rec_id, text, customer, primary_technology)
    return rec_id


def _fts_insert(embedding_record_id, text, customer, primary_technology):
    import sqlite3
    path = ALERTS_DATABASE_URL.replace("sqlite:///", "")
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO embedding_fts (text, customer, primary_technology, embedding_record_id) VALUES (?, ?, ?, ?)",
        (text, customer or "", primary_technology or "", embedding_record_id),
    )
    conn.commit()
    conn.close()


def get_all_embedding_records():
    session = SessionLocal()
    rows = session.query(EmbeddingRecord).all()
    session.expunge_all()
    session.close()
    return rows


def embedding_count():
    session = SessionLocal()
    count = session.query(EmbeddingRecord).count()
    session.close()
    return count


def keyword_search(query_text, top_k=8):
    """FTS5 full-text search over embedded notes/profile summaries,
    ranked by SQLite's built-in bm25 relevance score. Returns
    EmbeddingRecord objects (not raw FTS rows) so callers get the same
    shape as semantic search results. A malformed FTS5 query (special
    characters in the raw query text) is caught and treated as "no
    matches" rather than raised, since the caller's query is
    free-form user input, not a query language the user is expected
    to know."""
    import sqlite3
    if not query_text or not query_text.strip():
        return []
    path = ALERTS_DATABASE_URL.replace("sqlite:///", "")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    safe_query = query_text.replace('"', '""').strip()
    fts_query = f'"{safe_query}"'  # phrase-quoted: treats the input as literal text, not FTS5 query syntax
    try:
        rows = conn.execute(
            "SELECT embedding_record_id, bm25(embedding_fts) AS rank FROM embedding_fts "
            "WHERE embedding_fts MATCH ? ORDER BY rank LIMIT ?",
            (fts_query, top_k),
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    conn.close()
    if not rows:
        return []

    ids = [r["embedding_record_id"] for r in rows]
    session = SessionLocal()
    records = session.query(EmbeddingRecord).filter(EmbeddingRecord.id.in_(ids)).all()
    session.expunge_all()
    session.close()
    order = {rid: i for i, rid in enumerate(ids)}
    records.sort(key=lambda r: order.get(r.id, len(ids)))
    return records


def delete_embeddings_by_source(source_table, customer=None):
    """Deletes matching EmbeddingRecord rows AND their mirrored FTS5
    rows, so the two stores never drift out of sync. Used before
    re-embedding something that changes over time (e.g. a customer's
    behavior-profile summary, refreshed as new events accumulate) —
    delete-then-insert rather than update-in-place, since embeddings
    aren't editable once computed. Returns the number of rows deleted."""
    import sqlite3
    session = SessionLocal()
    q = session.query(EmbeddingRecord).filter(EmbeddingRecord.source_table == source_table)
    if customer is not None:
        q = q.filter(EmbeddingRecord.customer == customer)
    ids = [r.id for r in q.all()]
    if ids:
        session.query(EmbeddingRecord).filter(EmbeddingRecord.id.in_(ids)).delete(synchronize_session=False)
        session.commit()
    session.close()

    if ids:
        path = ALERTS_DATABASE_URL.replace("sqlite:///", "")
        conn = sqlite3.connect(path)
        conn.executemany("DELETE FROM embedding_fts WHERE embedding_record_id = ?", [(i,) for i in ids])
        conn.commit()
        conn.close()
    return len(ids)


# ---------------------------------------------------------------- Precomputed Insights (Step 3 / RAG batch)

def clear_precomputed_insights():
    session = SessionLocal()
    session.query(PrecomputedInsight).delete()
    session.commit()
    session.close()


def add_precomputed_insight(customer, primary_technology, headline, summary, caveat,
                             evidence_json, provider, source_opportunity_id=None, alert_log_id=None):
    session = SessionLocal()
    row = PrecomputedInsight(
        customer=customer, primary_technology=primary_technology, headline=headline,
        summary=summary, caveat=caveat, evidence_json=evidence_json, provider=provider,
        source_opportunity_id=source_opportunity_id, alert_log_id=alert_log_id,
    )
    session.add(row)
    session.commit()
    row_id = row.id
    session.close()
    return row_id


def get_all_precomputed_insights():
    session = SessionLocal()
    rows = session.query(PrecomputedInsight).order_by(PrecomputedInsight.computed_at.desc()).all()
    session.expunge_all()
    session.close()
    return rows
