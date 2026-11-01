# Copyright 2026 NuoBiT Solutions SL - Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from datetime import datetime

from freezegun import freeze_time

from odoo.tests import tagged
from odoo.tests.common import TransactionCase
from odoo.tools import convert_file

MODULE = "l10n_pt_invoice_spms_verification"
ACTION = MODULE + ".cron_spms_verification_update"


@tagged("post_install", "-at_install")
class TestSpmsVerificationPollingAction(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cron = cls.env.ref(ACTION)

    @freeze_time("2026-09-29 10:00:00")
    def test_data_file_creates_the_action_with_the_factory_interval(self):
        self.cron.unlink()
        convert_file(self.env, MODULE, "data/cron.xml", {}, mode="init", noupdate=True)
        cron = self.env.ref(ACTION)
        self.assertEqual((cron.interval_number, cron.interval_type), (1, "days"))
        self.assertEqual(cron.nextcall, datetime(2026, 9, 30, 5, 0, 0))
        self.assertTrue(cron.active)
