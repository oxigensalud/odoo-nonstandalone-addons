# Copyright 2026 NuoBiT Solutions SL - Deniz Gallo <dgallo@nuobit.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from psycopg2 import IntegrityError

from odoo.exceptions import AccessError
from odoo.tests.common import SavepointCase
from odoo.tools import mute_logger


class TestSpmsErrorType(SavepointCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ErrorType = cls.env["spms.error.type"]

    def test_get_or_create_existing_returns_same_record(self):
        c010 = self.ErrorType._get_or_create("C010", "Official text")
        count_before = self.ErrorType.search_count([])
        error_type = self.ErrorType._get_or_create("C010", "Official text")
        self.assertEqual(error_type, c010)
        self.assertEqual(self.ErrorType.search_count([]), count_before)

    def test_get_or_create_unknown_creates(self):
        count_before = self.ErrorType.search_count([])
        error_type = self.ErrorType._get_or_create("C999", "New error message")
        self.assertEqual(self.ErrorType.search_count([]), count_before + 1)
        self.assertEqual(error_type.code, "C999")
        self.assertEqual(error_type.description, "New error message")

    def test_get_or_create_backfills_missing_message(self):
        error_type = self.ErrorType._get_or_create("C998")
        self.assertFalse(error_type.description)
        error_type_again = self.ErrorType._get_or_create("C998", "Official text")
        self.assertEqual(error_type_again, error_type)
        self.assertEqual(error_type.description, "Official text")

    def test_get_or_create_updates_changed_message(self):
        # the catalogue mirrors a table the CCF owns: when the official
        # message of a known code changes on the wire, the mirror follows
        error_type = self.ErrorType._get_or_create("C997", "First text")
        self.ErrorType._get_or_create("C997", "Second text")
        self.assertEqual(error_type.description, "Second text")

    def test_get_or_create_empty_code(self):
        count_before = self.ErrorType.search_count([])
        self.assertFalse(self.ErrorType._get_or_create(False))
        self.assertFalse(self.ErrorType._get_or_create("  "))
        self.assertEqual(self.ErrorType.search_count([]), count_before)

    @mute_logger("odoo.sql_db")
    def test_code_unique(self):
        self.ErrorType._get_or_create("C010", "Official text")
        with self.assertRaises(IntegrityError), self.env.cr.savepoint():
            self.ErrorType.create({"code": "C010"})

    def test_name_get_carries_message(self):
        error_type = self.ErrorType._get_or_create("C994", "Mensagem oficial")
        self.assertEqual(error_type.display_name, "C994 - Mensagem oficial")
        bare = self.ErrorType._get_or_create("C993")
        self.assertEqual(bare.display_name, "C993")

    def _create_user(self, login, groups):
        return (
            self.env["res.users"]
            .with_context(no_reset_password=True)
            .create(
                {
                    "name": login,
                    "login": login,
                    "groups_id": [(6, 0, [self.env.ref(g).id for g in groups])],
                }
            )
        )

    def test_acl_billing_manager_full_access(self):
        user = self._create_user(
            "spms_billing_manager",
            ["base.group_user", "account.group_account_manager"],
        )
        error_type = (
            self.ErrorType.with_user(user)
            ._get_or_create("C996", "Some text")
            .with_user(user)
        )
        error_type.write({"description": "Corrected text"})
        self.assertEqual(error_type.description, "Corrected text")
        error_type.unlink()
        self.assertFalse(self.ErrorType.search([("code", "=", "C996")]))

    def test_acl_plain_user_is_read_only(self):
        user = self._create_user("spms_plain_user", ["base.group_user"])
        error_type = self.ErrorType._get_or_create("C995")
        self.assertEqual(error_type.with_user(user).read(["code"])[0]["code"], "C995")
        with self.assertRaises(AccessError):
            error_type.with_user(user).write({"description": "Changed"})
