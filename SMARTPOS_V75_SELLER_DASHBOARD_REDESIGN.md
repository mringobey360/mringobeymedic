# SmartPOS V75 — Seller Dashboard Redesign

- Dashboard is now a focused overview: welcome banner, getting-started checklist (hidden once complete), 4 headline numbers, recent orders, shortcuts and shop link.
- Plans moved to their own page "Plans & billing" (`seller_service_requests`), with plan cards and billing history.
- Orders, Products, Categories, Enquiries and Rooms & property pages share one header (breadcrumb + title), segmented filters with counts, and mobile card tables.
- Duplicate tab strip removed for sellers (sidebar already navigates); still shown for admins/staff.
- `views.py`: added `_annotate_store_services`; dashboard/plans views pass extra context. No models, URLs, migrations or payment logic changed.
- V76: welcome banner text fixed (white, readable) with rotating shine; added "Sales this week" bars and "Needs attention" panel.
- Run the Django test suite and check on staging before production.
- V81: mobile overlap fixes. Dialogs hide the floating bottom bars and sit above the header; the Return/Void sheet keeps its buttons pinned and visible; the POS "Review & pay" bar hides while the cart is on screen; pages get bottom space so forms are never hidden behind the nav (templates/base.html, sales/sale_list.html, sales/pos.html).
- V82: dialogs are re-parented to <body> so they fill the whole screen (no blank strip, not covered by header); removed duplicate bottom padding; tighter Return/Void sheet on phones.
- V83: Return/Void dialog z-index set in CSS (z-[110] was not in the pre-built Tailwind), so the header no longer covers the top of the form.
