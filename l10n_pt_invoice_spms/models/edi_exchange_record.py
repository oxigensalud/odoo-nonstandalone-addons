# Copyright 2026 NuoBiT Solutions SL - Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, models
from odoo.exceptions import ValidationError


class EdiExchangeRecord(models.Model):
    _inherit = "edi.exchange.record"

    @api.constrains("type_id", "model", "res_id", "parent_id")
    def _check_spms_company(self):
        # Descendants can inherit the changed document even when hidden by rules.
        # Elevation is limited to checking consistency; no record is modified.
        exchanges = self.sudo().search(
            [("id", "child_of", self.ids), ("type_id.code", "=", "l10n_pt_spms")]
        )
        for exchange in exchanges:
            invoice = exchange.record
            if (
                invoice
                and invoice._name == "account.move"
                and not invoice.company_id._is_spms_company()
            ):
                raise ValidationError(
                    _("SPMS invoices must belong to a Portuguese company.")
                )
