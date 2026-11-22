# Per-Product "Requires Expiry" Toggle

## Tatizo

Kabla ya hii, `expiry_date` kwenye kila `ProductBatch` ilikuwa hiari kabisa (tazama
`0013_batch_expiry_optional.py`). Hii ilisaidia bidhaa zisizoisha muda (vipodozi,
vifaa), lakini pia iliruhusu mfanyakazi kuacha expiry tupu **kwa bahati mbaya**
kwenye dawa halisi — dawa hiyo isingeonekana kamwe kwenye tahadhari za kuisha muda.

## Suluhisho

Uwanja mpya `Product.requires_expiry` (BooleanField, `default=True`) unaamua kama
batch mpya za bidhaa hiyo LAZIMA ziwe na `expiry_date`:

- **Imewashwa (default)** — kila batch mpya (kuongeza bidhaa, "Add Batch", "Edit
  Batch", na Bulk Upload) lazima iwe na tarehe ya mwisho wa matumizi. Hii ndiyo
  tabia sahihi kwa dawa na bidhaa zinazooza.
- **Imezimwa** — expiry inabaki hiari kama ilivyokuwa awali. Weka hii kwa vipodozi,
  vifaa, na bidhaa nyingine zisizoisha muda.

Uwanja huu unabadilishwa kwenye fomu ya bidhaa (Register/Edit Product), au kwa
safu wima ya hiari `requires_expiry` (YES/NO) kwenye Bulk Upload / Restock Excel.

## Mahali palipobadilika

- `inventory/models.py` — `Product.requires_expiry`.
- `inventory/migrations/0014_product_requires_expiry.py` — default `True` kwa
  bidhaa zilizopo tayari, ili tabia ya sasa (dawa zinatarajiwa kuwa na expiry)
  isibadilike bila mmiliki kuchagua kuzima kwenye bidhaa maalum.
- `inventory/forms.py`
  - `ProductForm` — checkbox mpya "Bidhaa hii Inaisha Muda (mf. Dawa)".
  - `ProductBatchForm` / `ProductBatchEditForm` — zinapokea `require_expiry`
    (kutoka `product.requires_expiry`) na kukataa fomu kama expiry ni tupu wakati
    inahitajika.
- `inventory/views.py`
  - `product_create_view`, `batch_add_view`, `batch_edit_view` — zinapitisha
    `require_expiry` sahihi kwenye fomu husika.
  - Bulk Upload (`_process_bulk_upload`) — safu mpya ya hiari `requires_expiry`
    (YES/NO/NDIYO/HAPANA au tupu = acha kama ilivyo). Safu ya Excel bila expiry
    kwa bidhaa inayohitaji expiry inakataliwa KABLA ya kuandika chochote
    (workbook nzima inathibitishwa kabla ya commit, kama ilivyokuwa awali).
- `inventory/admin.py` — `requires_expiry` imeongezwa kwenye `list_display` na
  `list_filter` ya Product.
- Violezo (`product_form.html`, `product_edit_form.html`, `batch_form.html`,
  `batch_edit_form.html`) — havikuhitaji mabadiliko makubwa kwa sababu vinaonyesha
  fomu kiotomatiki (`{% for field in form %}`); `batch_form.html` na
  `batch_edit_form.html` zimeongezewa ujumbe mfupi unaokumbusha kama bidhaa
  husika inahitaji expiry.

## Athari kwa data iliyopo

- Bidhaa zote zilizopo kabla ya migration hii zinapata `requires_expiry=True`
  moja kwa moja (default). Batches zao za zamani zenye expiry tupu HAZIGUSWI —
  ni batch MPYA tu zinazoathiriwa. Mmiliki anaweza kuzima kipengele hiki kwa
  bidhaa maalum (mf. vipodozi) wakati wowote kupitia "Edit Product".
