# Copyright 2026 NuoBiT Solutions SL - Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, models
from odoo.exceptions import ValidationError


class EdiExchangeRecord(models.Model):
    _inherit = "edi.exchange.record"

    @api.constrains("type_id", "model", "res_id", "parent_id")
    def _check_spms_company(self):
        for exchange in self.filtered(
            lambda record: record.type_id.code == "l10n_pt_spms"
        ):
            invoice = exchange.record
            if (
                invoice
                and invoice._name == "account.move"
                and not invoice.company_id._is_spms_company()
            ):
                raise ValidationError(
                    _("SPMS invoices must belong to a Portuguese company.")
                )
