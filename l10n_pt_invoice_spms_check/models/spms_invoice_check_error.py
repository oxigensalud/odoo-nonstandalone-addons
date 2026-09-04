# Copyright 2026 NuoBiT Solutions SL - Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class SpmsInvoiceCheckError(models.Model):
    """One row per error (Erro) reported by the check document.

    The Erro element is identical everywhere — {Codigo, Mensagem} — only
    its anchor changes: `level` says where it hangs and `line_id` is the
    claim it hangs under, empty for the errors anchored to the document
    itself or to a lot, which carry no prescription. Errors carry no
    money: the amounts live on the line.
    """

    _name = "spms.invoice.check.error"
    _description = "SPMS Invoice Check Error"
    _order = "result_id, line_id, id"
    _rec_name = "code"
    _check_company_auto = True

    result_id = fields.Many2one(
        comodel_name="spms.invoice.check",
        string="Invoice Check",
        required=True,
        index=True,
        ondelete="cascade",
    )
    line_id = fields.Many2one(
        comodel_name="spms.invoice.line.check",
        string="Line",
        index=True,
        ondelete="cascade",
        help="Claim the error hangs under; empty for the errors anchored "
        "to the document itself or to a lot.",
    )
    company_id = fields.Many2one(
        related="result_id.company_id",
        store=True,
        readonly=True,
    )
    prescription = fields.Char(
        related="line_id.prescription",
        store=True,
        readonly=True,
    )
    level = fields.Selection(
        selection=[
            ("invoice", "Invoice"),
            ("lote", "Lot"),
            ("prestacao", "Claim"),
            ("linha", "Line"),
            ("prescricao", "Prescription Data"),
        ],
        string="Level",
        required=True,
        readonly=True,
        help="Nesting point of the check document the error is anchored to.",
    )
    error_type_id = fields.Many2one(
        comodel_name="spms.error.type",
        string="Error Type",
        required=True,
        readonly=True,
        index=True,
        ondelete="restrict",
        help="Type of this error in the shared SPMS error-type master; "
        "unknown codes are auto-created there as pending "
        "classification.",
    )
    code = fields.Char(
        related="error_type_id.code",
        store=True,
        index=True,
        string="Error Code",
        help="Error code reported by the check (Erro/Codigo), "
        "e.g. C010, C012, C313.",
    )
    description = fields.Char(
        string="Error Description",
        readonly=True,
        help="Error description as reported (Erro/Mensagem).",
    )
    provider_system_ref = fields.Char(
        string="Provider System Ref",
        readonly=True,
        help="Line-level anchor: provider-system reference of the service "
        "line the error is anchored to. Kept as audit of which line came "
        "flagged, never used as a dedup key.",
    )
