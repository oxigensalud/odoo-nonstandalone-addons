# Copyright 2026 NuoBiT Solutions SL - Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from datetime import timedelta

from odoo import SUPERUSER_ID, api, fields

PREVIOUS_FACTORY_INTERVAL = (1, "hours")


def migrate(cr, version):
    """Give the polling action the factory interval and hour of this version.

    The action belongs to the administrator (noupdate): it moves only while
    it still carries the previous factory interval. An interval set by hand
    and the Active switch stay as they are.
    """
    env = api.Environment(cr, SUPERUSER_ID, {})
    # data/cron.xml is loaded before this script and creates the action when
    # it is missing: the action is always there
    cron = env.ref("l10n_pt_invoice_spms_verification.cron_spms_verification_update")
    if (cron.interval_number, cron.interval_type) == PREVIOUS_FACTORY_INTERVAL:
        # the factory values of data/cron.xml
        today_at_five = fields.Datetime.now().replace(hour=5, minute=0, second=0)
        cron.write(
            {
                "interval_number": 1,
                "interval_type": "days",
                "nextcall": today_at_five + timedelta(days=1),
            }
        )
