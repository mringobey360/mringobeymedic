# Safe compatibility migration for SmartPOS V30.
#
# Earlier V29 builds could contain a conditional constraint named
# ``one_active_assistant_per_manager_branch``.  The V30 model intentionally
# does NOT declare that constraint because assistant seat limits are governed
# by the subscription/entitlement layer, not by a database constraint.
#
# Some production databases reached this migration from a migration state in
# which the constraint had already been removed.  Calling RemoveConstraint
# again therefore makes Django fail with:
#   ValueError: No constraint named one_active_assistant_per_manager_branch
#
# Keep this migration as a state-safe no-op so existing production databases
# can advance normally without dropping data or faking migrations.
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('accounts', '0010_manager_assistant_audit'),
    ]

    operations = []
