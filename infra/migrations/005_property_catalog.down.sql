-- 005_property_catalog.down.sql
-- Destructive and operator-reviewed only, same contract as 001-004 downs.
-- The runner currently exposes --down only for 004; apply this manually via
-- psql (ON_ERROR_STOP=1) only when the whole 005 layer must be unwound.
-- Reverse order of 005_property_catalog.up.sql: interest -> contact ->
-- amenity link -> amenity catalogue -> properties indexes, FKs, constraints,
-- added columns. Legacy 004 columns (location/bedrooms/price/currency/
-- availability) were never dropped by the up, so no data is restored here;
-- the 005 backfill is simply discarded with the enriched columns.
-- NOTE: rows written through WU11 may carry operation_type='temporary_rent'
-- and status/price values that only live in the columns being dropped; the
-- re-added 004 CHECK validates existing rows, so delete or normalize those
-- rows first (this is a reviewed-only rollback, not a routine path).

DROP TABLE IF EXISTS property_interests;
DROP TABLE IF EXISTS property_contacts;
DROP TABLE IF EXISTS property_amenity_rel;
DROP TABLE IF EXISTS property_amenities;

DROP INDEX IF EXISTS properties_account_status_idx;
DROP INDEX IF EXISTS properties_account_city_idx;
DROP INDEX IF EXISTS properties_account_price_idx;

ALTER TABLE properties DROP CONSTRAINT IF EXISTS properties_responsible_agent_fk;
ALTER TABLE properties DROP CONSTRAINT IF EXISTS properties_account_reference_unique;

ALTER TABLE properties DROP COLUMN IF EXISTS responsible_agent_id;
ALTER TABLE properties DROP COLUMN IF EXISTS captured_at;
ALTER TABLE properties DROP COLUMN IF EXISTS capture_source;
ALTER TABLE properties DROP COLUMN IF EXISTS video_url;
ALTER TABLE properties DROP COLUMN IF EXISTS photos;
ALTER TABLE properties DROP COLUMN IF EXISTS construction_status;
ALTER TABLE properties DROP COLUMN IF EXISTS property_age_years;
ALTER TABLE properties DROP COLUMN IF EXISTS parking_spaces;
ALTER TABLE properties DROP COLUMN IF EXISTS bathrooms;
ALTER TABLE properties DROP COLUMN IF EXISTS rooms;
ALTER TABLE properties DROP COLUMN IF EXISTS total_area_m2;
ALTER TABLE properties DROP COLUMN IF EXISTS covered_area_m2;
ALTER TABLE properties DROP COLUMN IF EXISTS accepts_swap;
ALTER TABLE properties DROP COLUMN IF EXISTS expenses_amount;
ALTER TABLE properties DROP COLUMN IF EXISTS longitude;
ALTER TABLE properties DROP COLUMN IF EXISTS latitude;
ALTER TABLE properties DROP COLUMN IF EXISTS city;
ALTER TABLE properties DROP COLUMN IF EXISTS neighborhood;
ALTER TABLE properties DROP COLUMN IF EXISTS floor_unit;
ALTER TABLE properties DROP COLUMN IF EXISTS address;
ALTER TABLE properties DROP COLUMN IF EXISTS price_currency;
ALTER TABLE properties DROP COLUMN IF EXISTS price_amount;
ALTER TABLE properties DROP COLUMN IF EXISTS status;
ALTER TABLE properties DROP COLUMN IF EXISTS reference_code;
ALTER TABLE properties DROP COLUMN IF EXISTS property_type;

-- restore the original 004 operation_type domain (sale | rent)
ALTER TABLE properties DROP CONSTRAINT IF EXISTS properties_operation_type_check;
ALTER TABLE properties ADD CONSTRAINT properties_operation_type_check
    CHECK (operation_type IN ('sale', 'rent'));
