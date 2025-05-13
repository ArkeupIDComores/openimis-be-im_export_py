import tempfile
import os
from django.test import TestCase
from django.contrib.auth import get_user_model 
from core.services import create_or_update_interactive_user, create_or_update_core_user
from insuree.test_helpers import create_test_insuree
from policy.test_helpers import create_test_policy_with_IPs
from product.test_helpers import create_test_product
from insuree.models import FamilyType

class ImportEximBankTest(TestCase):
    
    def setUp(self):
        self.username = "test_import_user"
        self.password = "securepwd123"
        
        # Créer un utilisateur Django de base
        UserModel = get_user_model()
        self.django_user = UserModel.objects.create_user(
            username=self.username, password=self.password
        )

        # Créer un InteractiveUser et CoreUser associés
        self.i_user, _ = create_or_update_interactive_user(
            user_id=None,
            data={
                "username": self.username,
                "last_name": "Tester",
                "password": self.password,
                "other_names": "Import",
                "user_types": "INTERACTIVE",
                "language": "en",
                "roles": [1], 
            },
            audit_user_id=999,
            connected=False
        )

        self.core_user, _ = create_or_update_core_user(
            user_uuid=None,
            username=self.username,
            i_user=self.i_user
        )

        # Authentifie le client
        self.client.force_login(self.core_user.user)
        
        # Création des assurés
        insuree_1 = create_test_insuree(with_family=True, custom_props={"chf_id": "123456789"}, family_custom_props={'family_type': FamilyType.objects.get(code='H')})
        insuree_2 = create_test_insuree(with_family=True, custom_props={"chf_id": "987654321"}, family_custom_props={'family_type': FamilyType.objects.get(code='H')})

        product = create_test_product("VISIT", custom_props={"max_no_visits": 1})
        
        create_test_policy_with_IPs(product=product, insuree=insuree_1)
        create_test_policy_with_IPs(product=product, insuree=insuree_2)

    def test_import_exim_file(self):
        dir_path = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
        with open(os.path.join(dir_path, 'tests/EXIM_TEST.xlsx'), 'rb') as f:
            response = self.client.post(
                "/api/im_export/imports/exim_bank",
                {"file": f},
                format="multipart"
            )

        data = response.json()
        self.assertEqual(data["count"], 2)
        self.assertEqual(data["total_kmf"], "15000.00")
        
    def test_import_bdc_file(self):
        dir_path = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
        with open(os.path.join(dir_path, 'tests/BDC_MODELE_MVTC TEST.xlsx'), 'rb') as f:
            response = self.client.post(
                "/api/im_export/imports/bdc_bank",
                {"file": f},
                format="multipart"
            )

        data = response.json()
        self.assertEqual(data["count"], 2)
        self.assertEqual(data["total_kmf"], "80000.00")
