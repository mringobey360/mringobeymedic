# Inventory Multi-Unit Packaging & Weighted Average Cost

## Purpose

The inventory system now supports optional purchase packaging for medicines, cosmetics, water and other pharmacy products while keeping stock and POS sales in a single **base unit**.

Examples:

- 1 Box = 100 Tablets; buy 5 Boxes -> 500 Tablets.
- 1 Carton = 24 Bottles; buy 3 Cartons -> 72 Bottles.
- Buy 7 Water Bottles individually -> 7 Bottles; conversion is 1.
- 1 Carton = 12 Tubes; buy 4 Cartons -> 48 Tubes.

## Pricing model

`purchase_price` is the price paid for one purchase unit (for example, TSh 4,200 per Box).

The system calculates the base-unit cost:

`base-unit cost = purchase-unit price / units per purchase unit`

Example: TSh 4,200 / 100 Tablets = TSh 42 per Tablet.

The Product's current purchase/cost price is the **Weighted Average Cost (WAC)** across current sellable stock.

When a sale is completed:

- Physical stock is still issued using FEFO (earliest expiry first).
- Profit valuation uses the Product WAC at the moment of sale.
- `SaleItem.unit_cost` stores the WAC snapshot, so historical profit does not change when future purchases change the average.

## Bulk Excel format

The current template uses:

1. `name`
2. `category`
3. `batch_number`
4. `unit` — base/selling unit
5. `barcode`
6. `purchase_unit` — optional package unit
7. `units_per_purchase_unit` — optional conversion; use 1 for direct/base-unit purchases
8. `purchase_quantity` — number of purchase units received
9. `purchase_price` — purchase price for one package unit
10. `selling_price` — selling price per base unit
11. `reorder_level`
12. `expiry_date`

Legacy files using `quantity` + `purchase_price` are still accepted and interpreted as base-unit quantity and base-unit cost.

## Migration note

This production distribution includes committed Django migration files. Do not run `makemigrations` automatically in production; create and review migrations during development, commit them, then run `migrate` in production.

For an existing production database, **do not delete migration history**. Create and apply normal migrations in the production environment.
