# V75 — Property listing form: Seller Centre design + step wizard

Files changed
- `marketplace/templates/marketplace/seller_listing_form.html` — rebuilt with the V74 components (breadcrumb, hero, sections, aside, choice cards, switches, chips, upload box, action bar) and a 7-step wizard: Type → Price → Location → Details → Photos → Contact → Review.
- `marketplace/listing_views.py` — `ListingForm.Meta.widgets['purpose'] = forms.RadioSelect()` (rent/sale cards).

Behaviour
- Without JavaScript all sections show as a normal long form (progressive enhancement).
- Next validates the required fields of the current step (type, headline, description, price > 0, region, district, phone ≥ 9 digits). Final submit re-checks every step and jumps to the first one with an error.
- Server-side errors open the step that contains them and mark it red in the stepper.
- Editing an existing listing: every step is open and "Save changes" is available on every step.
- Enter in a text field goes to the next step instead of submitting.
- Review step summarises the entry with an Edit shortcut per row.
- No model, URL, migration or CSS-file changes. Field names are unchanged.
