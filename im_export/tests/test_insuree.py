import os
from tablib import Dataset
from im_export.resources import InsureeResource
from core.services import create_or_update_core_user, create_or_update_interactive_user
from django.test import TestCase
from location.test_helpers import create_test_location
from django.conf import settings
from location.models import Location
_TEST_USER_NAME = "test_insuree_import"
_TEST_USER_PWD = "test_insuree_import"
_TEST_DATA_USER = {
    "username": _TEST_USER_NAME,
    "last_name": _TEST_USER_NAME,
    "password": _TEST_USER_PWD,
    "other_names": _TEST_USER_NAME,
    "user_types": "INTERACTIVE",
    "language": "en",
    "roles": [1, 5, 9],
}

# all location used in the test files must use those name
_TEST_LOCATIONS = [
    {
        'region': 'Batha',
        'district': 'Batha',
        'municipality': 'BEGOU',
        'villages': [
            'Baguirmi',
            'Boua',
            'Niellim',
            'Sarakaba',
            'Maroc'
        ]
    }
]


class ImportInsureeTest(TestCase):

    def setUp(self) -> None:

        super(ImportInsureeTest, self).setUp()
        self.i_user, i_user_created = create_or_update_interactive_user(
            user_id=None, data=_TEST_DATA_USER, audit_user_id=999, connected=False)
        self.user, user_created = create_or_update_core_user(
            user_uuid=None, username=_TEST_DATA_USER["username"], i_user=self.i_user)
        test_location = _TEST_LOCATIONS

        for locations in test_location:
            test_region = create_test_location('R', custom_props={"name": locations['region']})
            test_district = create_test_location('D', custom_props={"name": locations['district'],
                                                                    "parent_id": test_region.id})
            test_municipality = create_test_location('M', custom_props={"name": locations['municipality'],
                                                                        "parent_id": test_district.id})
            for villages in locations['villages']:
                create_test_location('V', custom_props={"name": villages, "parent_id": test_municipality.id})

    def test_simple_import(self):
        region = Location.objects.all() \
            .filter(validity_to__isnull=True) \
            .filter(name='Batha Region', type='R').first()
        if not region:  
            region = create_test_location('R', custom_props={'name': 'Batha Region', 'code': 'R99'})
        district = Location.objects.all() \
            .filter(validity_to__isnull=True) \
            .filter(name='Batha', type='D', parent=region).first()
        if not district:
            district = create_test_location('D', custom_props={'name': 'Batha', 'code': 'R99D1', 'parent': region})
        ward = Location.objects.all() \
            .filter(validity_to__isnull=True) \
            .filter(name='Batha', type='M', parent=district).first()
        if not ward:
            ward = create_test_location('M', custom_props={'name': 'BEGOU', 'code': 'R99D1M1', 'parent': district})
        village = Location.objects.all() \
            .filter(validity_to__isnull=True) \
            .filter(name='Batha', type='V', parent=ward).first()
        if not village:
            village = create_test_location('V', custom_props={'name': 'Boua', 'code': 'R99D1M1V', 'parent': ward})
        
        dir_path = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
        resource = InsureeResource(user=self.user)
        #with Patch("ImportExportConfig.im_export_date_format", '%m/%d/%Y')
        with open(os.path.join(dir_path, 'tests/import_example.csv'), 'r') as f:
            imported_data = resource \
                .validate_and_sort_dataset(Dataset(headers=InsureeResource.insuree_headers).load(f.read()))
            result = resource.import_data(
                imported_data, dry_run=True, use_transactions=True,
                collect_failed_rows=False,
            )
            self.assertEqual(result.has_errors(), False)

    def test_simple_export(self):
        result = InsureeResource(self.user).export().dict
        self.assertTrue(result)

