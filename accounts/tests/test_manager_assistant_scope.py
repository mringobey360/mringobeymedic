from django.test import TestCase

from accounts.models import ManagerAssistant, User
from pharmacies.models import Pharmacy


class ManagerAssistantBranchScopeTests(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user(
            username='manager_scope', password='x', role=User.Role.ADMIN,
            phone_number='+255700000001',
        )
        self.branch_a = Pharmacy.objects.create(name='Scope A', is_active=True)
        self.branch_b = Pharmacy.objects.create(name='Scope B', is_active=True)
        self.branch_c = Pharmacy.objects.create(name='Scope C', is_active=True)
        self.manager.managed_pharmacies.add(self.branch_a, self.branch_b)
        self.assistant = User.objects.create_user(
            username='assistant_scope', password='x', role=User.Role.ASSISTANT,
            phone_number='+255700000002',
        )
        self.profile = ManagerAssistant.objects.create(
            manager=self.manager, assistant=self.assistant, pharmacy=self.branch_a,
            created_by=self.manager,
        )

    def test_all_mode_follows_manager_branches_dynamically(self):
        self.assertEqual(set(self.profile.accessible_pharmacies().values_list('pk', flat=True)), {self.branch_a.pk, self.branch_b.pk})
        self.manager.managed_pharmacies.remove(self.branch_b)
        self.assertEqual(set(self.profile.accessible_pharmacies().values_list('pk', flat=True)), {self.branch_a.pk})
        self.manager.managed_pharmacies.add(self.branch_c)
        self.assertEqual(set(self.profile.accessible_pharmacies().values_list('pk', flat=True)), {self.branch_a.pk, self.branch_c.pk})

    def test_selected_mode_is_intersected_with_manager_scope(self):
        self.profile.branch_access_mode = ManagerAssistant.BranchAccessMode.SELECTED_BRANCHES
        self.profile.save(update_fields=['branch_access_mode'])
        self.profile.branches.set([self.branch_a, self.branch_b, self.branch_c])
        self.assertEqual(set(self.profile.accessible_pharmacies().values_list('pk', flat=True)), {self.branch_a.pk, self.branch_b.pk})

    def test_manager_can_have_multiple_active_assistants_same_branch(self):
        second = User.objects.create_user(
            username='assistant_scope_2', password='x', role=User.Role.ASSISTANT,
            phone_number='+255700000003',
        )
        ManagerAssistant.objects.create(
            manager=self.manager, assistant=second, pharmacy=self.branch_a,
            created_by=self.manager,
        )
        self.assertEqual(ManagerAssistant.objects.filter(manager=self.manager, active=True).count(), 2)
