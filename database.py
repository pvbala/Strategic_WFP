import datetime

from sqlalchemy import create_engine, Column, Integer, String, Date, Boolean, ForeignKey
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = "sqlite:///swp_app.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

ROW_TYPE_DEMAND = "Demand Row"
ROW_TYPE_RAMPDOWN = "Rampdown Row"

# Default display labels for every column a user can rename via
# System Configuration. Keys are internal/fixed; labels are what's
# shown on every screen and in the Function 4 export, and are editable.
DEFAULT_LABELS = {
    "customer": "Customer",
    "requirement_iou": "Requirement IOU",
    "requirement_sub_iou": "Requirement Sub IOU",
    "location": "Location",
    "country": "Country",
    "start_date": "Start Date",
    "primary_technology": "Primary Technology",
    "secondary_technology": "Secondary Technology",
    "service_line": "Service Line",
    "grade": "Grade",
    "demand_count": "Demand Count",
    "rampdown": "Rampdown",
    "release_to_other_accounts": "Release to Other Accounts",
    "attrition": "Attrition",
    "gross_demand": "Gross Demand",
    "net_demand": "Net Demand",
    "ep": "EP",
    "ba": "BA",
    "internal": "Internal",
    "trainee": "Trainee",
    "total_supply": "Total Supply",
    "status": "Status",
}


class SystemConfig(Base):
    """Singleton row holding the one-time System Configuration decision
    (which optional columns are structurally active). Applies globally,
    across all Workforce Plans."""
    __tablename__ = "system_config"
    id = Column(Integer, primary_key=True)
    service_line_enabled = Column(Boolean, default=True, nullable=False)
    grade_enabled = Column(Boolean, default=True, nullable=False)
    secondary_technology_enabled = Column(Boolean, default=True, nullable=False)
    locked = Column(Boolean, default=False, nullable=False)


class PlanConfig(Base):
    """One row per Workforce Plan (no longer a singleton). Exactly one plan
    is_active at a time — that's the plan Functions 1, 2, 3, and 4 all
    operate against. A plan locks (can't be edited further) the first time
    Generate WFP is run for it. Once locked, a new plan can be created,
    which becomes the new active plan; the locked plan and its data remain
    in the database for reference."""
    __tablename__ = "plan_config"
    id = Column(Integer, primary_key=True)
    plan_name = Column(String, nullable=False)
    start_date = Column(Date, nullable=True)
    end_date = Column(Date, nullable=True)
    locked = Column(Boolean, default=False, nullable=False)
    is_active = Column(Boolean, default=False, nullable=False)


class ColumnLabel(Base):
    """User-editable display label per column key, set via System
    Configuration and reflected across every Function's screens and the
    Function 4 export."""
    __tablename__ = "column_label"
    id = Column(Integer, primary_key=True)
    key = Column(String, unique=True, nullable=False)
    label = Column(String, nullable=False)


class WfpRow(Base):
    """
    One row = one primary-key combination within a plan:
    Plan + Customer + Location + Start Date + Primary Technology
    + [Secondary Technology, if enabled] + [Service Line, if enabled]
    + [Grade, if enabled] + Row Type.
    """
    __tablename__ = "wfp_row"
    id = Column(Integer, primary_key=True)
    plan_id = Column(Integer, ForeignKey("plan_config.id"), nullable=False)

    # Key fields
    customer = Column(String, nullable=False)
    location = Column(String, nullable=False)
    start_date = Column(Date, nullable=False)
    primary_technology = Column(String, nullable=False)
    secondary_technology = Column(String, nullable=True)
    service_line = Column(String, nullable=True)
    grade = Column(String, nullable=True)
    row_type = Column(String, nullable=False)  # ROW_TYPE_DEMAND / ROW_TYPE_RAMPDOWN

    # Non-key attributes
    country = Column(String)
    requirement_iou = Column(String)
    requirement_sub_iou = Column(String)

    demand_count = Column(Integer, default=0, nullable=False)
    rampdown = Column(Integer, default=0, nullable=False)
    release_to_other_accounts = Column(Integer, default=0, nullable=False)
    attrition = Column(Integer, default=0, nullable=False)

    ep = Column(Integer, default=0, nullable=False)
    ba = Column(Integer, default=0, nullable=False)
    internal = Column(Integer, default=0, nullable=False)
    trainee = Column(Integer, default=0, nullable=False)

    @property
    def gross_demand(self):
        return self.demand_count + self.release_to_other_accounts + self.attrition

    @property
    def net_demand(self):
        return self.demand_count - self.rampdown

    @property
    def total_supply(self):
        return self.ep + self.ba + self.internal + self.trainee

    @property
    def status(self):
        return "Match" if self.total_supply == self.gross_demand else "Mismatch"


def init_db():
    Base.metadata.create_all(engine)
    session = SessionLocal()

    cfg = session.query(SystemConfig).first()
    if cfg is None:
        cfg = SystemConfig(service_line_enabled=True, grade_enabled=True,
                            secondary_technology_enabled=True, locked=False)
        session.add(cfg)
        session.commit()
    if not cfg.locked and session.query(WfpRow).count() > 0:
        cfg.locked = True
        session.commit()

    existing_keys = {row.key for row in session.query(ColumnLabel).all()}
    for key, default_label in DEFAULT_LABELS.items():
        if key not in existing_keys:
            session.add(ColumnLabel(key=key, label=default_label))
    session.commit()

    session.close()


# ---------------------------------------------------------------- System Configuration
def get_system_config():
    session = SessionLocal()
    cfg = session.query(SystemConfig).first()
    session.close()
    return cfg


def save_system_config(service_line_enabled, grade_enabled, secondary_technology_enabled, label_dict):
    session = SessionLocal()
    cfg = session.query(SystemConfig).first()
    if cfg.locked:
        session.close()
        raise RuntimeError("System configuration is locked and cannot be changed.")
    cfg.service_line_enabled = service_line_enabled
    cfg.grade_enabled = grade_enabled
    cfg.secondary_technology_enabled = secondary_technology_enabled
    cfg.locked = True

    for key, label in label_dict.items():
        row = session.query(ColumnLabel).filter(ColumnLabel.key == key).first()
        if row:
            row.label = label if label.strip() else DEFAULT_LABELS.get(key, key)

    session.commit()
    session.close()


def get_column_labels():
    session = SessionLocal()
    rows = session.query(ColumnLabel).all()
    labels = {row.key: row.label for row in rows}
    session.close()
    for key, default_label in DEFAULT_LABELS.items():
        labels.setdefault(key, default_label)
    return labels


# ---------------------------------------------------------------- Plans (Application Configuration)
def get_active_plan():
    """Returns the currently active PlanConfig row, or None if no plan has
    ever been created yet."""
    session = SessionLocal()
    plan = session.query(PlanConfig).filter(PlanConfig.is_active.is_(True)).first()
    if plan:
        session.expunge(plan)
    session.close()
    return plan


def get_all_plans():
    """Returns every plan ever created, most recent first — for the plan
    history list in Application Configuration."""
    session = SessionLocal()
    plans = session.query(PlanConfig).order_by(PlanConfig.id.desc()).all()
    session.expunge_all()
    session.close()
    return plans


def set_active_plan(plan_id):
    """Switches which plan is active, without creating a new one or
    touching any plan's locked status or data. Used when the user wants to
    go back to working against an existing plan (including a locked one)."""
    session = SessionLocal()
    session.query(PlanConfig).filter(PlanConfig.is_active.is_(True)).update({"is_active": False})
    plan = session.query(PlanConfig).filter(PlanConfig.id == plan_id).first()
    if plan is None:
        session.close()
        raise ValueError("No such plan.")
    plan.is_active = True
    session.commit()
    session.close()


def create_new_plan(plan_name, start_date, end_date):
    """Creates a new plan and makes it the active plan. Any previously
    active plan is deactivated (it keeps its own locked state and data —
    nothing about it changes except that it's no longer the plan Functions
    1/2/3/4 operate against)."""
    session = SessionLocal()
    session.query(PlanConfig).filter(PlanConfig.is_active.is_(True)).update({"is_active": False})
    new_plan = PlanConfig(plan_name=plan_name, start_date=start_date, end_date=end_date,
                           locked=False, is_active=True)
    session.add(new_plan)
    session.commit()
    session.close()


def update_active_plan(plan_name, start_date, end_date):
    """Edits the currently active plan's details. Only allowed while it's
    unlocked."""
    session = SessionLocal()
    plan = session.query(PlanConfig).filter(PlanConfig.is_active.is_(True)).first()
    if plan is None:
        session.close()
        raise RuntimeError("No active plan to update.")
    if plan.locked:
        session.close()
        raise RuntimeError("This plan is locked (a WFP has already been generated for it) and cannot be changed.")
    plan.plan_name = plan_name
    plan.start_date = start_date
    plan.end_date = end_date
    session.commit()
    session.close()


def mark_plan_generated():
    """Called by Function 4 when Generate WFP is run. Locks the active
    plan's name/dates permanently from that point on."""
    session = SessionLocal()
    plan = session.query(PlanConfig).filter(PlanConfig.is_active.is_(True)).first()
    if plan and plan.plan_name:
        plan.locked = True
        session.commit()
    session.close()


# ---------------------------------------------------------------- WFP row queries (scoped to a plan)
def find_demand_row_id(plan_id, customer, location, start_date, primary_technology,
                        secondary_technology, service_line, grade):
    session = SessionLocal()
    row = session.query(WfpRow).filter(
        WfpRow.plan_id == plan_id,
        WfpRow.row_type == ROW_TYPE_DEMAND,
        WfpRow.customer == customer,
        WfpRow.location == location,
        WfpRow.start_date == start_date,
        WfpRow.primary_technology == primary_technology,
        WfpRow.secondary_technology == secondary_technology,
        WfpRow.service_line == service_line,
        WfpRow.grade == grade,
    ).first()
    row_id = row.id if row else None
    session.close()
    return row_id


def update_demand_count(row_id, demand_count):
    session = SessionLocal()
    row = session.query(WfpRow).filter(WfpRow.id == row_id).first()
    row.demand_count = demand_count
    session.commit()
    session.close()


def save_demand_rows(row_dicts):
    """Function 1's save logic: blocks duplicate keys (within the same
    plan) by editing the existing row's Demand Count instead of inserting
    a new row. Each dict must include 'plan_id'.
    Returns (created_count, updated_count)."""
    created, updated = 0, 0
    for d in row_dicts:
        existing_id = find_demand_row_id(
            d["plan_id"], d["customer"], d["location"], d["start_date"], d["primary_technology"],
            d.get("secondary_technology"), d.get("service_line"), d.get("grade"),
        )
        if existing_id is not None:
            update_demand_count(existing_id, d["demand_count"])
            updated += 1
        else:
            add_rows([d])
            created += 1
    return created, updated


def add_rows(row_dicts):
    """Insert new WfpRow records. Used by Function 1 (new keys) and
    Function 3 (always inserts). Each dict must include 'plan_id'."""
    session = SessionLocal()
    for d in row_dicts:
        session.add(WfpRow(**d))
    session.commit()
    session.close()


def update_supply(row_id, ep, ba, internal, trainee):
    """Used by Function 2 (update-only)."""
    session = SessionLocal()
    row = session.query(WfpRow).filter(WfpRow.id == row_id).first()
    if row is None:
        session.close()
        raise ValueError("No matching demand row found for this key.")
    row.ep = ep
    row.ba = ba
    row.internal = internal
    row.trainee = trainee
    session.commit()
    session.close()


def delete_row(row_id):
    """Deletes a WfpRow entirely. For a Demand Row, this necessarily
    deletes its EP/BA/Internal/Trainee values along with it, since supply
    is stored as fields on the same row rather than a separate table —
    there is no orphaned "supply plan" left behind to clean up
    separately. Callers (Function 1) are responsible for warning the
    planner about this before calling it."""
    session = SessionLocal()
    row = session.query(WfpRow).filter(WfpRow.id == row_id).first()
    if row is not None:
        session.delete(row)
        session.commit()
    session.close()


def customers_with_demand(plan_id):
    session = SessionLocal()
    rows = (session.query(WfpRow.customer)
            .filter(WfpRow.plan_id == plan_id, WfpRow.row_type == ROW_TYPE_DEMAND)
            .distinct().all())
    session.close()
    return sorted({r[0] for r in rows})


def locations_with_demand(plan_id, customer):
    session = SessionLocal()
    rows = (session.query(WfpRow.location)
            .filter(WfpRow.plan_id == plan_id, WfpRow.row_type == ROW_TYPE_DEMAND,
                    WfpRow.customer == customer)
            .distinct().all())
    session.close()
    return sorted({r[0] for r in rows})


def start_dates_with_demand(plan_id, customer, location):
    session = SessionLocal()
    rows = (session.query(WfpRow.start_date)
            .filter(WfpRow.plan_id == plan_id, WfpRow.row_type == ROW_TYPE_DEMAND,
                    WfpRow.customer == customer, WfpRow.location == location)
            .distinct().all())
    session.close()
    return sorted({r[0] for r in rows})


def demand_rows_for(plan_id, customer, location, start_date):
    session = SessionLocal()
    rows = (session.query(WfpRow)
            .filter(WfpRow.plan_id == plan_id, WfpRow.row_type == ROW_TYPE_DEMAND,
                    WfpRow.customer == customer, WfpRow.location == location,
                    WfpRow.start_date == start_date)
            .all())
    session.expunge_all()
    session.close()
    return rows


def all_rows(plan_id):
    session = SessionLocal()
    rows = session.query(WfpRow).filter(WfpRow.plan_id == plan_id).all()
    session.expunge_all()
    session.close()
    return rows
