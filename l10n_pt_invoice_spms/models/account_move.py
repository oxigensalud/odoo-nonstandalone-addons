# Copyright 2025 Dixmit
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import re

from odoo import api, fields, models


class AccountMove(models.Model):
    _inherit = "account.move"

    spms_send_invoice = fields.Boolean(related="partner_id.spms_information")
    spms_period_start = fields.Date(compute="_compute_spms_period_start", store=True)
    spms_reference_invoice_id = fields.Many2one(
        "account.move",
        string="SPMS Reference Invoice",
        help="Reference invoice for SPMS credit notes.",
        compute="_compute_spms_reference_invoice_id",
        store=True,
    )

    @api.depends("invoice_line_ids.spms_start_date")
    def _compute_spms_period_start(self):
        for move in self:
            if move.invoice_line_ids.filtered(lambda l: l.spms_start_date):
                move.spms_period_start = min(
                    move.invoice_line_ids.mapped("spms_start_date")
                ).replace(day=1)
            else:
                move.spms_period_start = False

    @api.depends("partner_id", "date", "state")
    def _compute_spms_reference_invoice_id(self):
        for move in self:
            if (
                move.spms_reference_invoice_id
                or move.state != "posted"
                or not move.partner_id.spms_information
                or move.move_type not in ["out_invoice", "out_refund"]
            ):
                continue
            move.spms_reference_invoice_id = self.env["account.move"].search(
                [
                    ("partner_id", "=", move.partner_id.id),
                    ("move_type", "=", "out_invoice"),
                    ("state", "=", "posted"),
                    ("spms_period_start", "=", move.spms_period_start),
                    ("spms_reference_invoice_id", "=", False),
                    ("id", "!=", move.id),
                ],
                limit=1,
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
