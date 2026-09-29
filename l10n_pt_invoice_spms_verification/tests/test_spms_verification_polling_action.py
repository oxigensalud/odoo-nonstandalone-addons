# Copyright 2026 NuoBiT Solutions SL - Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from datetime import datetime

from freezegun import freeze_time

from odoo.modules.migration import load_script
from odoo.modules.module import get_module_resource
from odoo.tests import tagged
from odoo.tests.common import SavepointCase
from odoo.tools import convert_file

MODULE = "l10n_pt_invoice_spms_verification"
ACTION = MODULE + ".cron_spms_verification_update"


@tagged("post_install", "-at_install")
class TestSpmsVerificationPollingAction(SavepointCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cron = cls.env.ref(ACTION)
        cls.migration = load_script(
            get_module_resource(
                MODULE, "migrations", "14.0.1.1.0", "post-migration.py"
            ),
            "post_migration",
        )

    @freeze_time("2026-09-29 10:00:00")
    def test_data_file_creates_the_action_with_the_factory_interval(self):
        self.cron.unlink()
        convert_file(
            self.env.cr, MODULE, "data/cron.xml", {}, mode="init", noupdate=True
        )
        cron = self.env.ref(ACTION)
        self.assertEqual((cron.interval_number, cron.interval_type), (1, "days"))
        self.assertEqual(cron.nextcall, datetime(2026, 9, 30, 5, 0, 0))
        self.assertTrue(cron.active)

    @freeze_time("2026-09-29 10:00:00")
    def test_migration_moves_the_previous_factory_interval(self):
        self.cron.write({"interval_number": 1, "interval_type": "hours"})
        self.migration.migrate(self.env.cr, "14.0.1.0.1")
        self.assertEqual(
            (self.cron.interval_number, self.cron.interval_type), (1, "days")
        )
        self.assertEqual(self.cron.nextcall, datetime(2026, 9, 30, 5, 0, 0))
        self.assertTrue(self.cron.active)

    def test_migration_keeps_an_inactive_action_inactive(self):
        self.cron.write(
            {"interval_number": 1, "interval_type": "hours", "active": False}
        )
        self.migration.migrate(self.env.cr, "14.0.1.0.1")
        self.assertEqual(
            (self.cron.interval_number, self.cron.interval_type), (1, "days")
        )
        self.assertFalse(self.cron.active)

    def test_migration_keeps_an_interval_set_by_hand(self):
        self.cron.write(
            {
                "interval_number": 2,
                "interval_type": "hours",
                "nextcall": datetime(2026, 9, 29, 12, 0, 0),
            }
        )
        self.migration.migrate(self.env.cr, "14.0.1.0.1")
        self.assertEqual(
            (self.cron.interval_number, self.cron.interval_type), (2, "hours")
        )
        self.assertEqual(self.cron.nextcall, datetime(2026, 9, 29, 12, 0, 0))

    @freeze_time("2026-09-29 04:00:00")
    def test_migration_keeps_the_new_interval_set_by_hand(self):
        self.cron.write(
            {
                "interval_number": 1,
                "interval_type": "days",
                "nextcall": datetime(2026, 9, 29, 5, 0, 0),
            }
        )
        self.migration.migrate(self.env.cr, "14.0.1.0.1")
        self.assertEqual(
            (self.cron.interval_number, self.cron.interval_type), (1, "days")
        )
        self.assertEqual(self.cron.nextcall, datetime(2026, 9, 29, 5, 0, 0))
