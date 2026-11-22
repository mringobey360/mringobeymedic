# Category tenant isolation

Categories are scoped to a Pharmacy branch, just like Products.

- Product forms only list categories for the active branch.
- Inventory category filters only list categories for the active branch.
- Bulk Excel imports create/reuse categories only inside the active branch.
- A product cannot be assigned to a category belonging to another branch.
- The migration `0004_category_pharmacy_tenant_scope` safely splits legacy global categories by the branches that used them.
- Superadmin Django Admin can see all categories; normal application users remain tenant-scoped by the existing pharmacy context.
