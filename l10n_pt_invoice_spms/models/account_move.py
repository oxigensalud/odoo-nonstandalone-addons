# Copyright 2025 Dixmit
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import re

from odoo import fields, models


class AccountMove(models.Model):
    _inherit = "account.move"

    spms_send_invoice = fields.Boolean(related="partner_id.spms_information")

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
