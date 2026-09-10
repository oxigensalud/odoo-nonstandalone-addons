# Copyright 2026 NuoBiT Solutions SL - Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class SpmsInvoiceCheckError(models.Model):
    """One row per error (Erro) reported by the check document.

    The Erro element is identical everywhere — {Codigo, Mensagem} — only
    its anchor changes: `level` says where it hangs and `line_id` is the
    claim it hangs under, empty for the errors anchored to the document
    itself or to a lot, which carry no prescription. Errors carry no
    money: the amounts live on the line, and the flat error list reads
    them through it.
    """

    _name = "spms.invoice.check.error"
    _description = "SPMS Invoice Check Error"
    _order = "result_id, line_id, id"
    _rec_name = "code"
    _check_company_auto = True

    # levels anchored to a claim of the document, hence to a line
    _LINE_LEVELS = ("prestacao", "linha", "prescricao")

    result_id = fields.Many2one(
        comodel_name="spms.invoice.check",
        string="Invoice Check",
        required=True,
        readonly=True,
        index=True,
        ondelete="cascade",
    )
    line_id = fields.Many2one(
        comodel_name="spms.invoice.check.line",
        string="Line",
        readonly=True,
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
        comodel_name="spms.invoice.check.error.type",
        string="Error Type",
        required=True,
        readonly=True,
        index=True,
        ondelete="restrict",
        help="Type of this error in the catalogue of check error types; "
        "unknown codes are created there as the documents arrive.",
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
        help="Line-level anchor: provider-system reference of the claim "
        "line or prescription-data line the error is anchored to. Kept as "
        "audit of which line came flagged, never used as a dedup key.",
    )

    # the flat error list (Customers > SPMS > Errors) reads the invoice,
    # the customer and the document date through the result and the
    # money of the claim through the line: stored where the list groups
    # by them, plain related where it only shows them
    move_id = fields.Many2one(
        related="result_id.move_id",
        string="Invoice",
        store=True,
        readonly=True,
        help="Invoice the check reported this error on.",
    )
    partner_id = fields.Many2one(
        related="result_id.move_id.partner_id",
        string="Customer",
        store=True,
        readonly=True,
        help="Customer of the invoice.",
    )
    document_date = fields.Date(
        related="result_id.document_date",
        store=True,
        readonly=True,
    )
    currency_id = fields.Many2one(
        related="result_id.currency_id",
        readonly=True,
    )
    lot_type = fields.Char(
        related="line_id.lot_type",
        readonly=True,
    )
    lot_number = fields.Char(
        related="line_id.lot_number",
        readonly=True,
    )
    amount_billed = fields.Monetary(
        related="line_id.amount_billed",
        readonly=True,
    )
    amount_allowed = fields.Monetary(
        related="line_id.amount_allowed",
        readonly=True,
    )
    amount_difference = fields.Monetary(
        related="line_id.amount_difference",
        readonly=True,
    )
    days_billed = fields.Float(
        related="line_id.days_billed",
        readonly=True,
    )
    days_paid = fields.Float(
        related="line_id.days_paid",
        readonly=True,
    )

    @api.constrains("result_id", "line_id", "level")
    def _check_anchor(self):
        """An error hangs under a line of its own check, and only the
        levels below the claim have a line."""
        for rec in self:
            if rec.line_id and rec.line_id.result_id != rec.result_id:
                raise ValidationError(
                    _("Error %(code)s hangs under a line of another invoice check.")
                    % {"code": rec.error_type_id.code}
                )
            if bool(rec.line_id) != (rec.level in self._LINE_LEVELS):
                raise ValidationError(
                    _(
                        "Error %(code)s at level %(level)s must hang under a line "
                        "if and only if the level is below the claim."
                    )
                    % {"code": rec.error_type_id.code, "level": rec.level}
                )

    def write(self, vals):
        # Reprocessing replaces errors from the source document; it never
        # reassigns an existing error to a different check or claim.
        for rec in self:
            if any(
                name in vals and vals[name] != rec[name].id
                for name in ("result_id", "line_id")
            ):
                raise ValidationError(
                    _(
                        "An imported error cannot be moved to another invoice "
                        "check or claim."
                    )
                )
        return super().write(vals)
