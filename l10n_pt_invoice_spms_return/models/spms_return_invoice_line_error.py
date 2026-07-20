# Copyright 2026 NuoBiT Solutions SL - Deniz Gallo <dgallo@nuobit.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class SpmsReturnInvoiceLineError(models.Model):
    _name = "spms.return.invoice.line.error"
    _description = "SPMS Return Invoice Line Error"
    _order = "line_id, excel_row, id"
    _rec_name = "code"

    line_id = fields.Many2one(
        comodel_name="spms.return.invoice.line",
        string="SPMS Return Invoice Line",
        required=True,
        ondelete="cascade",
    )
    company_id = fields.Many2one(
        related="line_id.company_id",
        store=True,
        readonly=True,
    )
    code = fields.Char(
        string="Error Code",
        index=True,
        readonly=True,
        help="Error code reported by the conference (Excel COD_ERRO), "
        "e.g. C010, C313, A004.",
    )
    description = fields.Char(
        string="Error Description",
        readonly=True,
        help="Error description as reported (Excel DESC_ERRO).",
    )
    provider_system_ref = fields.Char(
        string="Provider System Ref",
        readonly=True,
        help="Provider-system reference (Excel SISTEMAPRESTADOCRD). "
        "Informed only on C010/C012 rows; kept as audit of which row came "
        "flagged, never used as a dedup key.",
    )
    excel_row = fields.Integer(
        string="Excel Row",
        readonly=True,
        help="Row number in the source Excel attached to the SPMS return, "
        "for forensic cross-checking.",
    )
