# System banners: draft -> review -> publish

Where: HQ (superadmin) -> SMS services -> "System notices (banners)"  (/hq/notices/)

Workflow
1. New notice  -> saved as DRAFT. Customers can never see a draft.
2. "Review & publish" opens a full preview (computer / phone, light / dark) with checks.
3. "Publish to customers" makes it live (blocked while "__" blanks remain or the end time has passed).
4. Editing a live notice moves it back to draft so the new wording is reviewed again.
5. Live notices can be switched off/on, unpublished (back to draft), or marked resolved (incidents).

Deploy
- Run migrations: python manage.py migrate   (hq 0017_systemnotice_review_workflow)
  Notices that were already live stay live (backfilled as PUBLISHED).
- Tests: python manage.py test hq.test_system_notices

Changed files
hq/models.py, hq/system_notices.py, hq/notice_forms.py, hq/notice_views.py, hq/urls.py,
hq/migrations/0017_systemnotice_review_workflow.py, hq/test_system_notices.py,
templates/hq/notice_form.html, notice_list.html, notice_review.html (new),
templates/partials/_system_notice_card.html (new), system_notices.html, _system_notice_styles.html
