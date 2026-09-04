# Copyright 2026 NuoBiT Solutions SL - Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, fields, models
from odoo.tools import float_compare, float_is_zero


class SpmsInvoiceLineCheck(models.Model):
    """One row per claim (prestação) reported by the check document.

    The claim element is the only place of the document carrying money
    and quantities, once per prescription, so the amounts live here and
    nowhere else: a sum over the lines is a sum over the prescriptions.
    The errors the document anchors under the claim (claim, line and
    prescription-data levels) hang from the line.
    """

    _name = "spms.invoice.line.check"
    _description = "SPMS Invoice Line Check"
    _order = "result_id, id"
    _rec_name = "prescription"
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
    lot_type = fields.Char(
        string="Lot Type",
        readonly=True,
        help="Type of the lot the claim belongs to (TipoLote).",
    )
    lot_number = fields.Char(
        string="Lot Number",
        readonly=True,
        help="Number of the lot the claim belongs to (Numero).",
    )
    prescription = fields.Char(
        string="Prescription",
        index=True,
        readonly=True,
        help="Prescription number (NumeroPrescricao), the billing-line key "
        "of the claim. Patient-linked: treat as an opaque identifier.",
    )
    amount_billed = fields.Monetary(
        string="Billed Amount",
        readonly=True,
        help="Claim total as read by the check, untaxed (ValorTotalLido); "
        "read from the document, never recomputed.",
    )
    amount_allowed = fields.Monetary(
        string="Allowed Amount",
        readonly=True,
        help="Claim total recomputed by the check, untaxed "
        "(ValorTotalCalculado); read from the document, never recomputed.",
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
        help="Billed quantity in days for the claim (QuantidadeLida).",
    )
    days_paid = fields.Float(
        string="Paid Days",
        readonly=True,
        help="Allowed/paid days for the claim (QuantidadeCalculado); may be "
        "empty in the document.",
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
        help="Credit-note line generated from this claim (audit only).",
    )
    error_ids = fields.One2many(
        comodel_name="spms.invoice.check.line.error",
        inverse_name="line_id",
        string="Errors",
    )
    error_codes = fields.Char(
        string="Error Codes",
        compute="_compute_error_codes",
        store=True,
        help="Distinct error codes reported under this claim.",
    )

    @api.depends("amount_billed", "amount_allowed")
    def _compute_amount_difference(self):
        for rec in self:
            rec.amount_difference = rec.amount_billed - rec.amount_allowed

    @api.depends("error_ids.code")
    def _compute_error_codes(self):
        for rec in self:
            codes = []
            for code in rec.error_ids.mapped("code"):
                if code and code not in codes:
                    codes.append(code)
            rec.error_codes = " / ".join(codes)

    def _is_creditable(self):
        """A claim the credit note must carry: keyed by prescription and
        over-billed (positive difference)."""
        self.ensure_one()
        return bool(self.prescription) and (
            float_compare(
                self.amount_difference,
                0.0,
                precision_rounding=self.currency_id.rounding or 0.01,
            )
            > 0
        )

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
