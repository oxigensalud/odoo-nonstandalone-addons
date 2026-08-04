# Copyright 2026 NuoBiT Solutions SL - Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, fields, models
from odoo.tools import float_compare, float_is_zero


class SpmsInvoiceCheckError(models.Model):
    """One row per error reported by the check document.

    The document's Erro element is identical everywhere — {Codigo,
    Mensagem} — only its anchor changes, so a single table mirrors that:
    the `level` selection says where the error hangs and the anchor
    columns of that level carry its context. Rows anchored to a
    prescription (claim, line and prescription-data levels) all carry the
    claim totals, so a prescription's credit base is readable from any of
    its rows.
    """

    _name = "spms.invoice.check.error"
    _description = "SPMS Invoice Check Error"
    _order = "result_id, level, prescription, id"
    _rec_name = "code"
    _check_company_auto = True

    result_id = fields.Many2one(
        comodel_name="spms.invoice.check",
        string="Invoice Check",
        required=True,
        index=True,
        ondelete="cascade",
    )
    company_id = fields.Many2one(
        related="result_id.company_id",
        store=True,
        readonly=True,
    )
    currency_id = fields.Many2one(
        related="result_id.currency_id",
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
        help="Nesting point of the check document the error is " "anchored to.",
    )
    code = fields.Char(
        string="Error Code",
        index=True,
        readonly=True,
        help="Error code reported by the check (Erro/Codigo), "
        "e.g. C010, C012, C313.",
    )
    description = fields.Char(
        string="Error Description",
        readonly=True,
        help="Error description as reported (Erro/Mensagem).",
    )
    lot_type = fields.Char(
        string="Lot Type",
        readonly=True,
        help="Lot-level anchor: type of the lot the error is anchored to.",
    )
    lot_number = fields.Char(
        string="Lot Number",
        readonly=True,
        help="Lot-level anchor: number of the lot the error is anchored to.",
    )
    prescription = fields.Char(
        string="Prescription",
        index=True,
        readonly=True,
        help="Prescription number (NumeroPrescricao), the billing-line key "
        "of the claim the error is anchored to. Patient-linked: treat as "
        "an opaque identifier.",
    )
    amount_billed = fields.Monetary(
        string="Billed Amount",
        readonly=True,
        help="Claim total as read by the check, untaxed. Identical on "
        "every row of the same prescription; read from the document, "
        "never recomputed.",
    )
    amount_allowed = fields.Monetary(
        string="Allowed Amount",
        readonly=True,
        help="Claim total recomputed by the check, untaxed. Identical "
        "on every row of the same prescription; read from the document, "
        "never recomputed.",
    )
    amount_difference = fields.Monetary(
        string="Difference",
        compute="_compute_amount_difference",
        store=True,
        help="Over-billed base, untaxed: billed minus allowed. This is the "
        "base the credit-note line will carry; VAT is applied by the "
        "line's taxes.",
    )
    days_billed = fields.Float(
        string="Billed Days",
        readonly=True,
        help="Billed quantity in days for the claim.",
    )
    days_paid = fields.Float(
        string="Paid Days",
        readonly=True,
        help="Allowed/paid days for the claim; may be empty in the " "document.",
    )
    provider_system_ref = fields.Char(
        string="Provider System Ref",
        readonly=True,
        help="Line-level anchor: provider-system reference of the service "
        "line the error is anchored to. Kept as audit of which line came "
        "flagged, never used as a dedup key.",
    )
    move_line_id = fields.Many2one(
        comodel_name="account.move.line",
        string="Original Invoice Line",
        readonly=True,
        check_company=True,
        help="Original invoice line matched by prescription number within "
        "the invoice.",
    )
    refund_move_line_id = fields.Many2one(
        comodel_name="account.move.line",
        string="Refund Line",
        readonly=True,
        check_company=True,
        help="Credit-note line generated from this prescription (audit " "only).",
    )

    @api.depends("amount_billed", "amount_allowed")
    def _compute_amount_difference(self):
        for rec in self:
            rec.amount_difference = rec.amount_billed - rec.amount_allowed

    def _get_refund_line_values(self):
        """Values to write on the copied refund line, empty to keep it as-is.

        Money is authoritative: the resulting line must carry exactly the
        difference (billed - allowed). Total rejection keeps the copied line
        untouched (original days, price/day and SPMS dates); a partial
        rejection with reliable days credits the rejected days at the
        original price/day; otherwise the whole difference goes on a single
        unit. Partial cases blank the SPMS dates (which days is unknown).
        """
        self.ensure_one()
        original_line = self.move_line_id
        rounding = self.currency_id.rounding or 0.01
        if (
            float_is_zero(self.amount_allowed, precision_rounding=rounding)
            and float_compare(
                original_line.price_subtotal,
                self.amount_difference,
                precision_rounding=rounding,
            )
            == 0
        ):
            return {}
        days_rejected = self.days_billed - self.days_paid
        if (
            self.days_paid > 0
            and days_rejected > 0
            and float_compare(
                days_rejected * original_line.price_unit,
                self.amount_difference,
                precision_rounding=rounding,
            )
            == 0
        ):
            return {
                "quantity": days_rejected,
                "spms_start_date": False,
                "spms_end_date": False,
            }
        return {
            "quantity": 1,
            "price_unit": self.amount_difference,
            "spms_start_date": False,
            "spms_end_date": False,
        }
