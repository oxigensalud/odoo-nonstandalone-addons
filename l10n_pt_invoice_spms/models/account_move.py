# Copyright 2025 Dixmit
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import re

from odoo import _, fields, models
from odoo.exceptions import UserError

# once SPMS holds the invoice it stays posted; corrections go through a credit note
SPMS_ACCEPTED_STATES = ("output_sent", "output_sent_and_processed")


class AccountMove(models.Model):
    _inherit = "account.move"

    spms_send_invoice = fields.Boolean(related="partner_id.spms_information")

    def write(self, vals):
        if "state" in vals and vals["state"] != "posted":
            for move in self.filtered(lambda m: m.state == "posted"):
                if move._spms_accepted():
                    raise UserError(
                        _(
                            "SPMS has accepted %(name)s, so it can no longer be "
                            "cancelled or reset to draft. Issue a credit note instead."
                        )
                        % {"name": move.name}
                    )
        return super().write(vals)

    def _spms_accepted(self):
        """Whether SPMS holds this move: its sending record went through."""
        self.ensure_one()
        return self._has_exchange_record(
            self.env.ref("l10n_pt_invoice_spms.spms_exchange_type"),
            extra_domain=[("edi_exchange_state", "in", SPMS_ACCEPTED_STATES)],
        )

    def _get_spms_invoice_number(self):
        """
        Return the invoice number to send to SPMS,
        which is the invoice name without offending characters and no zeros.
        """
        splitted_name = self.name.rsplit("/", 1)
        if len(splitted_name) == 1:
            return self.name
        serie, number = splitted_name
        if number.isdigit():
            clean_serie = re.sub(r"(?![A-Za-z0-9]).", "", serie)
            return f"{clean_serie}-{int(number)}"
        return self.name
