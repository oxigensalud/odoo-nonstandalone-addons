# Copyright 2026 NuoBiT Solutions SL - Deniz Gallo <dgallo@nuobit.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models


class AccountMove(models.Model):
    _inherit = "account.move"

    spms_return_invoice_ids = fields.One2many(
        comodel_name="spms.return.invoice",
        inverse_name="move_id",
        string="SPMS Return Invoices",
    )
    spms_return_invoice_count = fields.Integer(
        string="# SPMS Returns",
        compute="_compute_spms_return_invoice_count",
    )

    @api.depends("spms_return_invoice_ids")
    def _compute_spms_return_invoice_count(self):
        for rec in self:
            rec.spms_return_invoice_count = len(rec.spms_return_invoice_ids)

    def action_view_spms_return_invoices(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("SPMS Returns"),
            "res_model": "spms.return.invoice",
            "view_mode": "tree,form",
            "domain": [("move_id", "=", self.id)],
        }
