-- Servicing: job cards, parts issued to jobs, labour, and SERVICE invoices.
-- See docs/DATA_MODEL.md section 7.
--
-- SQLite can't change CHECK constraints in place, so four tables are rebuilt
-- (new table -> copy rows -> drop old -> rename). The runner turns foreign keys off around
-- this and runs a full foreign_key_check before committing.
-- Triggers are dropped first and re-created last, so copying rows doesn't fire them
-- (in particular, copying the stock ledger must not change stock quantities again).

DROP TRIGGER trg_invoice_item_vehicle_once;
DROP TRIGGER trg_invoice_no_uncancel;
DROP TRIGGER trg_invoice_no_delete;
DROP TRIGGER trg_invoice_item_no_delete;
DROP TRIGGER trg_movement_apply_stock;
DROP TRIGGER trg_movement_no_update;
DROP TRIGGER trg_movement_no_delete;

-- ---------------------------------------------------------------- new masters & job cards

CREATE TABLE labour_item (
    id              INTEGER PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE COLLATE NOCASE CHECK (trim(name) <> ''),
    default_charge  INTEGER NOT NULL DEFAULT 0 CHECK (default_charge >= 0),   -- incl. GST
    sac_code        TEXT NOT NULL DEFAULT '998714',
    gst_rate_bp     INTEGER NOT NULL DEFAULT 1800 CHECK (gst_rate_bp BETWEEN 0 AND 10000),
    is_active       INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime'))
) STRICT;

INSERT INTO labour_item (name, default_charge) VALUES
    ('General service', 35000),
    ('Oil change', 5000),
    ('Brake adjustment', 10000),
    ('Chain cleaning & lubrication', 10000),
    ('Carburettor / throttle body cleaning', 25000),
    ('Wash & polish', 15000);

CREATE TABLE service_job (
    id                  INTEGER PRIMARY KEY,
    job_no              TEXT NOT NULL UNIQUE,
    customer_id         INTEGER NOT NULL REFERENCES customer (id),
    vehicle_id          INTEGER REFERENCES vehicle (id),        -- set when it's a vehicle we sold
    registration_number TEXT,
    vehicle_description TEXT NOT NULL CHECK (trim(vehicle_description) <> ''),
    chassis_number      TEXT,
    odometer_km         INTEGER CHECK (odometer_km >= 0),
    service_type        TEXT NOT NULL DEFAULT 'PAID'
                        CHECK (service_type IN ('PAID', 'FREE', 'WARRANTY', 'ACCIDENT')),
    free_service_no     INTEGER CHECK (free_service_no BETWEEN 1 AND 6),
    complaints          TEXT,
    work_done           TEXT,
    mechanic_name       TEXT,
    received_at         TEXT NOT NULL,
    promised_at         TEXT,
    delivered_at        TEXT,
    next_service_date   TEXT,
    next_service_km     INTEGER CHECK (next_service_km >= 0),
    status              TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'BILLED', 'CANCELLED')),
    invoice_id          INTEGER REFERENCES invoice (id),
    cancel_reason       TEXT,
    notes               TEXT,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime')),
    updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime')),
    CHECK ((service_type = 'FREE') = (free_service_no IS NOT NULL)),
    CHECK ((status = 'BILLED') = (invoice_id IS NOT NULL))
) STRICT;

CREATE INDEX idx_service_job_status ON service_job (status);
CREATE INDEX idx_service_job_customer ON service_job (customer_id);
CREATE INDEX idx_service_job_reg ON service_job (registration_number);

CREATE TABLE service_job_part (
    id              INTEGER PRIMARY KEY,
    job_id          INTEGER NOT NULL REFERENCES service_job (id),
    part_id         INTEGER NOT NULL REFERENCES part (id),
    quantity        INTEGER NOT NULL CHECK (quantity > 0),
    unit_price      INTEGER NOT NULL CHECK (unit_price >= 0),   -- incl. GST
    discount        INTEGER NOT NULL DEFAULT 0 CHECK (discount >= 0),
    unit_cost       INTEGER NOT NULL DEFAULT 0 CHECK (unit_cost >= 0),
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime')),
    CHECK (discount <= unit_price * quantity)
) STRICT;

CREATE INDEX idx_service_job_part_job ON service_job_part (job_id);

CREATE TABLE service_job_labour (
    id              INTEGER PRIMARY KEY,
    job_id          INTEGER NOT NULL REFERENCES service_job (id),
    labour_item_id  INTEGER REFERENCES labour_item (id),
    description     TEXT NOT NULL CHECK (trim(description) <> ''),
    amount          INTEGER NOT NULL CHECK (amount >= 0),       -- incl. GST; 0 on free services
    sac_code        TEXT NOT NULL,
    gst_rate_bp     INTEGER NOT NULL CHECK (gst_rate_bp BETWEEN 0 AND 10000),
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime'))
) STRICT;

CREATE INDEX idx_service_job_labour_job ON service_job_labour (job_id);

-- ---------------------------------------------------------------- invoice_sequence: + SERVICE, JOB

CREATE TABLE invoice_sequence_new (
    id              INTEGER PRIMARY KEY,
    series          TEXT NOT NULL CHECK (series IN ('VEHICLE', 'PART', 'SERVICE', 'BOOKING', 'RECEIPT', 'JOB')),
    financial_year  TEXT NOT NULL CHECK (financial_year GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]'),
    prefix          TEXT NOT NULL,
    last_number     INTEGER NOT NULL DEFAULT 0 CHECK (last_number >= 0),
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime')),
    UNIQUE (series, financial_year)
) STRICT;
INSERT INTO invoice_sequence_new SELECT * FROM invoice_sequence;
DROP TABLE invoice_sequence;
ALTER TABLE invoice_sequence_new RENAME TO invoice_sequence;

-- ---------------------------------------------------------------- invoice: + SERVICE

CREATE TABLE invoice_new (
    id                  INTEGER PRIMARY KEY,
    invoice_no          TEXT NOT NULL UNIQUE,
    invoice_type        TEXT NOT NULL CHECK (invoice_type IN ('VEHICLE', 'PART', 'SERVICE')),
    invoice_date        TEXT NOT NULL,
    customer_id         INTEGER REFERENCES customer (id),
    booking_id          INTEGER REFERENCES booking (id),
    bill_name           TEXT NOT NULL,
    bill_relation       TEXT,
    bill_phone          TEXT,
    bill_address        TEXT,
    bill_gstin          TEXT,
    hypothecated_to     TEXT,
    total_taxable       INTEGER NOT NULL DEFAULT 0 CHECK (total_taxable >= 0),
    total_discount      INTEGER NOT NULL DEFAULT 0 CHECK (total_discount >= 0),
    total_cgst          INTEGER NOT NULL DEFAULT 0 CHECK (total_cgst >= 0),
    total_sgst          INTEGER NOT NULL DEFAULT 0 CHECK (total_sgst >= 0),
    round_off           INTEGER NOT NULL DEFAULT 0 CHECK (round_off BETWEEN -50 AND 50),
    grand_total         INTEGER NOT NULL DEFAULT 0 CHECK (grand_total >= 0),
    status              TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'CANCELLED')),
    cancelled_at        TEXT,
    cancel_reason       TEXT,
    notes               TEXT,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime')),
    updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime')),
    CHECK (invoice_type = 'PART' OR customer_id IS NOT NULL),  -- only counter part sales may be walk-in
    CHECK ((status = 'CANCELLED') = (cancelled_at IS NOT NULL))
) STRICT;
INSERT INTO invoice_new SELECT * FROM invoice;
DROP TABLE invoice;
ALTER TABLE invoice_new RENAME TO invoice;
CREATE INDEX idx_invoice_date ON invoice (invoice_date);
CREATE INDEX idx_invoice_customer ON invoice (customer_id);

-- ---------------------------------------------------------------- invoice_item: + LABOUR

CREATE TABLE invoice_item_new (
    id              INTEGER PRIMARY KEY,
    invoice_id      INTEGER NOT NULL REFERENCES invoice (id),
    line_no         INTEGER NOT NULL CHECK (line_no > 0),
    item_kind       TEXT NOT NULL CHECK (item_kind IN ('VEHICLE', 'PART', 'LABOUR')),
    vehicle_id      INTEGER REFERENCES vehicle (id),
    part_id         INTEGER REFERENCES part (id),
    description     TEXT NOT NULL,
    hsn_code        TEXT NOT NULL,          -- SAC code for labour
    quantity        INTEGER NOT NULL CHECK (quantity > 0),
    unit_price      INTEGER NOT NULL CHECK (unit_price >= 0),
    discount        INTEGER NOT NULL DEFAULT 0 CHECK (discount >= 0),
    taxable_value   INTEGER NOT NULL CHECK (taxable_value >= 0),
    gst_rate_bp     INTEGER NOT NULL CHECK (gst_rate_bp BETWEEN 0 AND 10000),
    cgst            INTEGER NOT NULL CHECK (cgst >= 0),
    sgst            INTEGER NOT NULL CHECK (sgst >= 0),
    line_total      INTEGER NOT NULL CHECK (line_total >= 0),
    unit_cost       INTEGER NOT NULL DEFAULT 0 CHECK (unit_cost >= 0),
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime')),
    UNIQUE (invoice_id, line_no),
    CHECK (
        (item_kind = 'VEHICLE' AND vehicle_id IS NOT NULL AND part_id IS NULL AND quantity = 1)
        OR (item_kind = 'PART' AND part_id IS NOT NULL AND vehicle_id IS NULL)
        OR (item_kind = 'LABOUR' AND part_id IS NULL AND vehicle_id IS NULL)
    ),
    CHECK (discount <= unit_price * quantity)
) STRICT;
INSERT INTO invoice_item_new SELECT * FROM invoice_item;
DROP TABLE invoice_item;
ALTER TABLE invoice_item_new RENAME TO invoice_item;
CREATE INDEX idx_invoice_item_invoice ON invoice_item (invoice_id);
CREATE INDEX idx_invoice_item_vehicle ON invoice_item (vehicle_id) WHERE vehicle_id IS NOT NULL;
CREATE INDEX idx_invoice_item_part ON invoice_item (part_id) WHERE part_id IS NOT NULL;

-- ---------------------------------------------------------------- part_stock_movement: + JOB_ISSUE / JOB_RETURN

CREATE TABLE part_stock_movement_new (
    id              INTEGER PRIMARY KEY,
    part_id         INTEGER NOT NULL REFERENCES part (id),
    movement_type   TEXT NOT NULL CHECK (movement_type IN (
                        'PURCHASE', 'SALE', 'SALE_CANCEL', 'ADJUSTMENT', 'RETURN_TO_SUPPLIER', 'JOB_ISSUE', 'JOB_RETURN')),
    quantity        INTEGER NOT NULL CHECK (quantity <> 0),
    unit_cost       INTEGER NOT NULL DEFAULT 0 CHECK (unit_cost >= 0),
    gst_amount      INTEGER NOT NULL DEFAULT 0 CHECK (gst_amount >= 0),
    reference_no    TEXT,
    invoice_item_id INTEGER REFERENCES invoice_item (id),
    movement_date   TEXT NOT NULL,
    notes           TEXT,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime')),
    service_job_id  INTEGER REFERENCES service_job (id),
    CHECK (
        (movement_type IN ('PURCHASE', 'SALE_CANCEL', 'JOB_RETURN') AND quantity > 0)
        OR (movement_type IN ('SALE', 'RETURN_TO_SUPPLIER', 'JOB_ISSUE') AND quantity < 0)
        OR movement_type = 'ADJUSTMENT'
    ),
    CHECK ((movement_type IN ('SALE', 'SALE_CANCEL')) = (invoice_item_id IS NOT NULL)),
    CHECK ((movement_type IN ('JOB_ISSUE', 'JOB_RETURN')) = (service_job_id IS NOT NULL))
) STRICT;
INSERT INTO part_stock_movement_new
    (id, part_id, movement_type, quantity, unit_cost, gst_amount, reference_no, invoice_item_id, movement_date,
     notes, created_at, updated_at)
    SELECT id, part_id, movement_type, quantity, unit_cost, gst_amount, reference_no, invoice_item_id,
           movement_date, notes, created_at, updated_at
    FROM part_stock_movement;
DROP TABLE part_stock_movement;
ALTER TABLE part_stock_movement_new RENAME TO part_stock_movement;
CREATE INDEX idx_movement_part ON part_stock_movement (part_id, movement_date);
CREATE INDEX idx_movement_job ON part_stock_movement (service_job_id) WHERE service_job_id IS NOT NULL;

-- ---------------------------------------------------------------- triggers (same rules as 0001)

CREATE TRIGGER trg_invoice_item_vehicle_once
BEFORE INSERT ON invoice_item
WHEN NEW.vehicle_id IS NOT NULL
 AND EXISTS (
    SELECT 1 FROM invoice_item ii JOIN invoice i ON i.id = ii.invoice_id
    WHERE ii.vehicle_id = NEW.vehicle_id AND i.status = 'ACTIVE'
 )
BEGIN
    SELECT RAISE(ABORT, 'vehicle is already on an active invoice');
END;

CREATE TRIGGER trg_invoice_no_uncancel
BEFORE UPDATE OF status ON invoice
WHEN OLD.status = 'CANCELLED' AND NEW.status <> 'CANCELLED'
BEGIN
    SELECT RAISE(ABORT, 'a cancelled invoice cannot be re-activated');
END;

CREATE TRIGGER trg_invoice_no_delete
BEFORE DELETE ON invoice
BEGIN
    SELECT RAISE(ABORT, 'invoices cannot be deleted; cancel them instead');
END;

CREATE TRIGGER trg_invoice_item_no_delete
BEFORE DELETE ON invoice_item
BEGIN
    SELECT RAISE(ABORT, 'invoice lines cannot be deleted');
END;

CREATE TRIGGER trg_movement_apply_stock
AFTER INSERT ON part_stock_movement
BEGIN
    UPDATE part SET stock_qty = stock_qty + NEW.quantity WHERE id = NEW.part_id;
END;

CREATE TRIGGER trg_movement_no_update
BEFORE UPDATE ON part_stock_movement
BEGIN
    SELECT RAISE(ABORT, 'stock movements cannot be edited; add an adjustment instead');
END;

-- Ledger rows stay, except when deleting a part entered by mistake: a part that was never sold
-- or used on a job may have its purchase / adjustment rows removed along with it.
CREATE TRIGGER trg_movement_no_delete
BEFORE DELETE ON part_stock_movement
WHEN OLD.movement_type NOT IN ('PURCHASE', 'ADJUSTMENT')
  OR EXISTS (SELECT 1 FROM invoice_item WHERE part_id = OLD.part_id)
  OR EXISTS (SELECT 1 FROM service_job_part WHERE part_id = OLD.part_id)
BEGIN
    SELECT RAISE(ABORT, 'stock movements cannot be deleted; add an adjustment instead');
END;

-- A billed job card's parts and labour are on its invoice; they can't change afterwards.
CREATE TRIGGER trg_job_part_locked_insert
BEFORE INSERT ON service_job_part
WHEN (SELECT status FROM service_job WHERE id = NEW.job_id) <> 'OPEN'
BEGIN
    SELECT RAISE(ABORT, 'parts can only be added to an open job card');
END;

CREATE TRIGGER trg_job_labour_locked_insert
BEFORE INSERT ON service_job_labour
WHEN (SELECT status FROM service_job WHERE id = NEW.job_id) <> 'OPEN'
BEGIN
    SELECT RAISE(ABORT, 'labour can only be added to an open job card');
END;
