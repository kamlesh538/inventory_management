-- Initial schema. See docs/DATA_MODEL.md.
-- Money: integer paise. GST rates: basis points (1800 = 18%). Dates: ISO-8601 text.
-- Never edit this file after release; add a new numbered migration instead.

-- ---------------------------------------------------------------- settings

CREATE TABLE shop_settings (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    shop_name       TEXT NOT NULL,
    address_line1   TEXT, address_line2 TEXT, city TEXT, district TEXT, state TEXT, pincode TEXT,
    state_code      TEXT,
    gstin           TEXT,
    pan             TEXT,
    phone           TEXT,
    email           TEXT,
    bank_name       TEXT, bank_account_no TEXT, bank_ifsc TEXT,
    invoice_terms   TEXT,
    logo_path       TEXT,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime'))
) STRICT;

INSERT INTO shop_settings (id, shop_name) VALUES (1, 'Jai Bajrang Bali Honda');

CREATE TABLE invoice_sequence (
    id              INTEGER PRIMARY KEY,
    series          TEXT NOT NULL CHECK (series IN ('VEHICLE', 'PART', 'BOOKING', 'RECEIPT')),
    financial_year  TEXT NOT NULL CHECK (financial_year GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]'),
    prefix          TEXT NOT NULL,
    last_number     INTEGER NOT NULL DEFAULT 0 CHECK (last_number >= 0),
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime')),
    UNIQUE (series, financial_year)
) STRICT;

-- ---------------------------------------------------------------- masters

CREATE TABLE vehicle_model (
    id                  INTEGER PRIMARY KEY,
    model_name          TEXT NOT NULL,
    variant             TEXT NOT NULL DEFAULT '',
    category            TEXT NOT NULL CHECK (category IN ('SCOOTER', 'MOTORCYCLE', 'EV')),
    engine_cc           INTEGER CHECK (engine_cc > 0),
    hsn_code            TEXT NOT NULL,
    gst_rate_bp         INTEGER NOT NULL CHECK (gst_rate_bp BETWEEN 0 AND 10000),
    ex_showroom_price   INTEGER NOT NULL CHECK (ex_showroom_price >= 0),
    is_active           INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime')),
    updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime')),
    UNIQUE (model_name, variant)
) STRICT;

CREATE TABLE customer (
    id              INTEGER PRIMARY KEY,
    name            TEXT NOT NULL CHECK (trim(name) <> ''),
    relation_type   TEXT CHECK (relation_type IN ('S/O', 'D/O', 'W/O', 'C/O')),
    relation_name   TEXT,
    phone           TEXT NOT NULL CHECK (trim(phone) <> ''),
    alt_phone       TEXT,
    email           TEXT,
    address_line1   TEXT, address_line2 TEXT, city TEXT, district TEXT, state TEXT, pincode TEXT,
    gstin           TEXT,
    id_proof_type   TEXT CHECK (id_proof_type IN ('AADHAAR', 'PAN', 'DL', 'VOTER_ID', 'PASSPORT')),
    id_proof_number TEXT,
    date_of_birth   TEXT,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime'))
) STRICT;

CREATE INDEX idx_customer_phone ON customer (phone);
CREATE INDEX idx_customer_name ON customer (name COLLATE NOCASE);

CREATE TABLE financer (
    id              INTEGER PRIMARY KEY,
    name            TEXT NOT NULL,
    branch          TEXT NOT NULL DEFAULT '',
    contact_person  TEXT,
    phone           TEXT,
    is_active       INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime')),
    UNIQUE (name, branch)
) STRICT;

CREATE TABLE insurer (
    id              INTEGER PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    contact_person  TEXT,
    phone           TEXT,
    is_active       INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime'))
) STRICT;

-- ---------------------------------------------------------------- vehicle inventory

CREATE TABLE vehicle (
    id                      INTEGER PRIMARY KEY,
    vehicle_model_id        INTEGER NOT NULL REFERENCES vehicle_model (id),
    chassis_number          TEXT NOT NULL UNIQUE COLLATE NOCASE CHECK (trim(chassis_number) <> ''),
    engine_number           TEXT NOT NULL UNIQUE COLLATE NOCASE CHECK (trim(engine_number) <> ''),
    color                   TEXT NOT NULL,
    key_number              TEXT,
    battery_number          TEXT,
    manufacturing_month     TEXT CHECK (manufacturing_month GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]'),
    purchase_invoice_no     TEXT,
    purchase_date           TEXT,
    purchase_taxable_value  INTEGER NOT NULL DEFAULT 0 CHECK (purchase_taxable_value >= 0),
    purchase_gst_amount     INTEGER NOT NULL DEFAULT 0 CHECK (purchase_gst_amount >= 0),
    status                  TEXT NOT NULL DEFAULT 'IN_STOCK'
                            CHECK (status IN ('IN_STOCK', 'BOOKED', 'SOLD', 'RETURNED_TO_OEM')),
    registration_number     TEXT,
    notes                   TEXT,
    created_at              TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime')),
    updated_at              TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime'))
) STRICT;

CREATE INDEX idx_vehicle_model ON vehicle (vehicle_model_id);
CREATE INDEX idx_vehicle_status ON vehicle (status);

-- ---------------------------------------------------------------- parts inventory

CREATE TABLE part (
    id              INTEGER PRIMARY KEY,
    part_number     TEXT NOT NULL UNIQUE COLLATE NOCASE CHECK (trim(part_number) <> ''),
    name            TEXT NOT NULL,
    category        TEXT NOT NULL DEFAULT 'SPARE'
                    CHECK (category IN ('SPARE', 'ACCESSORY', 'OIL', 'TYRE', 'BATTERY', 'OTHER')),
    hsn_code        TEXT NOT NULL,
    gst_rate_bp     INTEGER NOT NULL CHECK (gst_rate_bp BETWEEN 0 AND 10000),
    unit            TEXT NOT NULL DEFAULT 'PCS' CHECK (unit IN ('PCS', 'LTR', 'SET')),
    mrp             INTEGER NOT NULL DEFAULT 0 CHECK (mrp >= 0),
    sale_price      INTEGER NOT NULL CHECK (sale_price >= 0),
    avg_cost        INTEGER NOT NULL DEFAULT 0 CHECK (avg_cost >= 0),
    stock_qty       INTEGER NOT NULL DEFAULT 0 CHECK (stock_qty >= 0),
    reorder_level   INTEGER NOT NULL DEFAULT 0 CHECK (reorder_level >= 0),
    rack_location   TEXT,
    is_active       INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime'))
) STRICT;

CREATE INDEX idx_part_name ON part (name COLLATE NOCASE);

-- ---------------------------------------------------------------- bookings, sales, billing

CREATE TABLE booking (
    id                      INTEGER PRIMARY KEY,
    booking_no              TEXT NOT NULL UNIQUE,
    customer_id             INTEGER NOT NULL REFERENCES customer (id),
    vehicle_model_id        INTEGER NOT NULL REFERENCES vehicle_model (id),
    preferred_color         TEXT,
    booking_date            TEXT NOT NULL,
    expected_delivery_date  TEXT,
    quoted_price            INTEGER NOT NULL DEFAULT 0 CHECK (quoted_price >= 0),
    vehicle_id              INTEGER REFERENCES vehicle (id),
    invoice_id              INTEGER REFERENCES invoice (id),
    status                  TEXT NOT NULL DEFAULT 'OPEN'
                            CHECK (status IN ('OPEN', 'ALLOTTED', 'CONVERTED', 'CANCELLED')),
    cancel_reason           TEXT,
    created_at              TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime')),
    updated_at              TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime'))
) STRICT;

CREATE INDEX idx_booking_customer ON booking (customer_id);
CREATE INDEX idx_booking_status ON booking (status);

CREATE TABLE invoice (
    id                  INTEGER PRIMARY KEY,
    invoice_no          TEXT NOT NULL UNIQUE,
    invoice_type        TEXT NOT NULL CHECK (invoice_type IN ('VEHICLE', 'PART')),
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

CREATE INDEX idx_invoice_date ON invoice (invoice_date);
CREATE INDEX idx_invoice_customer ON invoice (customer_id);

CREATE TABLE invoice_item (
    id              INTEGER PRIMARY KEY,
    invoice_id      INTEGER NOT NULL REFERENCES invoice (id),
    line_no         INTEGER NOT NULL CHECK (line_no > 0),
    item_kind       TEXT NOT NULL CHECK (item_kind IN ('VEHICLE', 'PART')),
    vehicle_id      INTEGER REFERENCES vehicle (id),
    part_id         INTEGER REFERENCES part (id),
    description     TEXT NOT NULL,
    hsn_code        TEXT NOT NULL,
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
    ),
    CHECK (discount <= unit_price * quantity)
) STRICT;

CREATE INDEX idx_invoice_item_invoice ON invoice_item (invoice_id);
CREATE INDEX idx_invoice_item_vehicle ON invoice_item (vehicle_id) WHERE vehicle_id IS NOT NULL;
CREATE INDEX idx_invoice_item_part ON invoice_item (part_id) WHERE part_id IS NOT NULL;

-- A vehicle can appear on at most one ACTIVE invoice. (Cancelled invoices keep their lines.)
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

-- Re-activating a cancelled invoice would bypass the check above, so forbid it.
CREATE TRIGGER trg_invoice_no_uncancel
BEFORE UPDATE OF status ON invoice
WHEN OLD.status = 'CANCELLED' AND NEW.status <> 'CANCELLED'
BEGIN
    SELECT RAISE(ABORT, 'a cancelled invoice cannot be re-activated');
END;

-- Financial records are cancelled, never deleted.
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

CREATE TABLE part_stock_movement (
    id              INTEGER PRIMARY KEY,
    part_id         INTEGER NOT NULL REFERENCES part (id),
    movement_type   TEXT NOT NULL
                    CHECK (movement_type IN ('PURCHASE', 'SALE', 'SALE_CANCEL', 'ADJUSTMENT', 'RETURN_TO_SUPPLIER')),
    quantity        INTEGER NOT NULL CHECK (quantity <> 0),
    unit_cost       INTEGER NOT NULL DEFAULT 0 CHECK (unit_cost >= 0),
    gst_amount      INTEGER NOT NULL DEFAULT 0 CHECK (gst_amount >= 0),
    reference_no    TEXT,
    invoice_item_id INTEGER REFERENCES invoice_item (id),
    movement_date   TEXT NOT NULL,
    notes           TEXT,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime')),
    CHECK (
        (movement_type IN ('PURCHASE', 'SALE_CANCEL') AND quantity > 0)
        OR (movement_type IN ('SALE', 'RETURN_TO_SUPPLIER') AND quantity < 0)
        OR movement_type = 'ADJUSTMENT'
    ),
    CHECK ((movement_type IN ('SALE', 'SALE_CANCEL')) = (invoice_item_id IS NOT NULL))
) STRICT;

CREATE INDEX idx_movement_part ON part_stock_movement (part_id, movement_date);

-- Keep part.stock_qty equal to the ledger total. stock_qty >= 0 makes overselling fail.
CREATE TRIGGER trg_movement_apply_stock
AFTER INSERT ON part_stock_movement
BEGIN
    UPDATE part SET stock_qty = stock_qty + NEW.quantity WHERE id = NEW.part_id;
END;

-- The ledger is append-only; corrections are new ADJUSTMENT rows.
CREATE TRIGGER trg_movement_no_update
BEFORE UPDATE ON part_stock_movement
BEGIN
    SELECT RAISE(ABORT, 'stock movements cannot be edited; add an adjustment instead');
END;

CREATE TRIGGER trg_movement_no_delete
BEFORE DELETE ON part_stock_movement
BEGIN
    SELECT RAISE(ABORT, 'stock movements cannot be deleted; add an adjustment instead');
END;

CREATE TABLE loan (
    id                  INTEGER PRIMARY KEY,
    invoice_id          INTEGER NOT NULL UNIQUE REFERENCES invoice (id),
    customer_id         INTEGER NOT NULL REFERENCES customer (id),
    financer_id         INTEGER NOT NULL REFERENCES financer (id),
    loan_account_no     TEXT,
    loan_amount         INTEGER NOT NULL CHECK (loan_amount > 0),
    down_payment        INTEGER NOT NULL DEFAULT 0 CHECK (down_payment >= 0),
    interest_rate_bp    INTEGER CHECK (interest_rate_bp >= 0),
    tenure_months       INTEGER CHECK (tenure_months > 0),
    emi_amount          INTEGER CHECK (emi_amount >= 0),
    sanction_date       TEXT,
    disbursement_date   TEXT,
    status              TEXT NOT NULL DEFAULT 'APPLIED'
                        CHECK (status IN ('APPLIED', 'SANCTIONED', 'DISBURSED', 'REJECTED')),
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime')),
    updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime'))
) STRICT;

CREATE TABLE payment (
    id              INTEGER PRIMARY KEY,
    receipt_no      TEXT NOT NULL UNIQUE,
    customer_id     INTEGER REFERENCES customer (id),
    invoice_id      INTEGER REFERENCES invoice (id),
    booking_id      INTEGER REFERENCES booking (id),
    payment_date    TEXT NOT NULL,
    payment_type    TEXT NOT NULL CHECK (payment_type IN
                    ('BOOKING_ADVANCE', 'DOWN_PAYMENT', 'FULL_PAYMENT', 'LOAN_DISBURSEMENT', 'BALANCE', 'REFUND')),
    payment_mode    TEXT NOT NULL CHECK (payment_mode IN ('CASH', 'UPI', 'CARD', 'CHEQUE', 'BANK_TRANSFER', 'FINANCE')),
    amount          INTEGER NOT NULL CHECK (amount <> 0),
    reference_no    TEXT,
    loan_id         INTEGER REFERENCES loan (id),
    notes           TEXT,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime')),
    CHECK (invoice_id IS NOT NULL OR booking_id IS NOT NULL),
    CHECK ((payment_type = 'REFUND') = (amount < 0)),
    CHECK ((payment_mode = 'FINANCE') = (loan_id IS NOT NULL))
) STRICT;

CREATE INDEX idx_payment_invoice ON payment (invoice_id);
CREATE INDEX idx_payment_booking ON payment (booking_id);
CREATE INDEX idx_payment_customer ON payment (customer_id);

CREATE TRIGGER trg_payment_no_delete
BEFORE DELETE ON payment
BEGIN
    SELECT RAISE(ABORT, 'payments cannot be deleted; record a refund instead');
END;

CREATE TABLE insurance_policy (
    id              INTEGER PRIMARY KEY,
    vehicle_id      INTEGER NOT NULL REFERENCES vehicle (id),
    invoice_id      INTEGER REFERENCES invoice (id),
    insurer_id      INTEGER NOT NULL REFERENCES insurer (id),
    policy_number   TEXT NOT NULL,
    policy_type     TEXT NOT NULL CHECK (policy_type IN ('COMPREHENSIVE', 'THIRD_PARTY', 'BUNDLED_1OD_5TP')),
    premium         INTEGER NOT NULL CHECK (premium >= 0),
    idv             INTEGER CHECK (idv >= 0),
    start_date      TEXT NOT NULL,
    end_date        TEXT NOT NULL CHECK (end_date >= start_date),
    nominee_name    TEXT,
    nominee_relation TEXT,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime')),
    UNIQUE (insurer_id, policy_number)
) STRICT;

CREATE INDEX idx_insurance_vehicle ON insurance_policy (vehicle_id);
CREATE INDEX idx_insurance_end_date ON insurance_policy (end_date);
