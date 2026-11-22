# v94 – Layaway "New plan" modal

- templates/layaway/_new_modal.html – sectioned form (Product / Customer / Deposit & schedule), always-visible totals, sticky footer, mobile bottom sheet, dark-mode aware.
- templates/base.html – modals marked data-touch-nofocus no longer auto-focus on touch screens (keyboard no longer covers the form).

Fixes: "Deposit paid by" dropdown no longer shows a double border and lists every option; fields stack cleanly on phones.
