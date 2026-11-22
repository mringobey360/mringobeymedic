# POS Discount Control

## Manager / Shop Owner
Open **Products & Stock → Edit** for a product and configure **POS Discount Policy**:
- Enable/disable discounts for that product.
- Set minimum discount percentage.
- Set maximum discount percentage.

Example: Enable discount, minimum `5%`, maximum `10%`. A seller may grant 5%, 7.5%, or 10%, but not 0%, 11%, or any value outside the range.

## Seller / POS
The discount field appears only for products whose manager has enabled discounts. The allowed range is displayed next to the field. The seller cannot change the product's configured policy.

## Security
The browser is not trusted for discount authorization. Online checkout validates the submitted percentage against the current database policy. Offline sales store the requested percentage locally and the server validates it again when the sale syncs.

## Accounting / Audit
Every SaleItem records:
- original unit selling price,
- final unit price after discount,
- discount percentage,
- total discount amount.

Profit uses the final selling price, so reports remain aligned with the amount actually charged.
