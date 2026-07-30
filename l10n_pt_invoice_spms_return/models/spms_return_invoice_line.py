# Copyright 2026 NuoBiT Solutions SL - Deniz Gallo <dgallo@nuobit.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_compare, float_is_zero

# Portuguese reduced health VAT applied by the SPMS conference (6%). Used by
# the per-line credit contribution and the per-invoice estimate; the actual
# credit-note taxes come from the original line's taxes, never from here.
SPMS_TAX_FACTOR = 1.06


class SpmsReturnInvoiceLine(models.Model):
    _name = "spms.return.invoice.line"
    _description = "SPMS Return Invoice Line"
    _order = "return_invoice_id, prescription"
    _rec_name = "prescription"
    _check_company_auto = True

    return_invoice_id = fields.Many2one(
        comodel_name="spms.return.invoice",
        string="SPMS Return Invoice",
        required=True,
        ondelete="cascade",
    )
    company_id = fields.Many2one(
        related="return_invoice_id.company_id",
        store=True,
        readonly=True,
    )
    currency_id = fields.Many2one(
        related="company_id.currency_id",
        store=True,
        readonly=True,
    )
    prescription = fields.Char(
        string="Prescription",
        index=True,
        readonly=True,
        help="Prescription number (NUMEROPRESCRICAO), the billing-line key. "
        "Patient-linked: treat as an opaque identifier.",
    )
    move_line_id = fields.Many2one(
        comodel_name="account.move.line",
        string="Original Invoice Line",
        readonly=True,
        check_company=True,
        help="Original invoice line matched by prescription number within "
        "the matched invoice.",
    )
    refund_move_line_id = fields.Many2one(
        comodel_name="account.move.line",
        string="Refund Line",
        readonly=True,
        check_company=True,
        help="Credit-note line generated from this line (audit only).",
    )
    amount_billed = fields.Monetary(
        string="Billed Amount",
        readonly=True,
        help="Billed value, untaxed (Excel VALORTOTAL). Read from the "
        "report, never recomputed.",
    )
    amount_allowed = fields.Monetary(
        string="Allowed Amount",
        readonly=True,
        help="Allowed value, untaxed (Excel VALORTOTALAPURADO). Read from "
        "the report, never recomputed.",
    )
    amount_allowed_taxed = fields.Monetary(
        string="Allowed Amount (Taxed)",
        readonly=True,
        help="Allowed value with VAT as rounded by the conference (Excel "
        "VALORTOTALAPURADOIVA). Their per-row rounding drives the "
        "per-invoice estimate.",
    )
    days_billed = fields.Float(
        string="Billed Days",
        readonly=True,
        help="Billed quantity in days (Excel QUANTIDADETOTAL).",
    )
    days_paid = fields.Float(
        string="Paid Days",
        readonly=True,
        help="Allowed/paid days (Excel 'Numero de dias pagos'); may be "
        "empty in the report.",
    )
    amount_difference = fields.Monetary(
        string="Difference",
        compute="_compute_amount_difference",
        store=True,
        help="Over-billed base, untaxed: billed minus allowed. This is the "
        "base the credit-note line will carry; VAT is applied by the "
        "line's taxes.",
    )
    amount_credit_taxed = fields.Float(
        string="Credit (Taxed)",
        compute="_compute_amount_credit_taxed",
        digits=(16, 2),
        help="Informative per-line credit contribution with VAT: "
        "billed x 1.06 - allowed-with-VAT (their rounding). The "
        "per-invoice estimate sums these before rounding once.",
    )
    state = fields.Selection(
        selection=[
            ("matched", "Matched"),
            ("zero_diff", "No Difference"),
            ("not_found", "Not Found"),
            ("ambiguous", "Ambiguous Match"),
            ("data_error", "Data Error"),
        ],
        string="State",
        readonly=True,
        copy=False,
    )
    data_error_reason = fields.Selection(
        selection=[
            ("missing", "Empty amount cells"),
            ("unconvertible", "Unconvertible amount value"),
            ("diverged", "Taxed amount inconsistent with the allowed amount"),
            ("incoherent", "Contradictory duplicate rows"),
        ],
        string="Data Error Reason",
        readonly=True,
        copy=False,
        help="Why this line was marked as a data error when parsing the error file.",
    )
    previous_line_id = fields.Many2one(
        comodel_name="spms.return.invoice.line",
        string="Previous Claim Line",
        readonly=True,
        copy=False,
        help="Line of a previous return claiming the same (invoice, "
        "prescription). While set and unresolved, the invoice cannot be "
        "generated.",
    )
    resolution = fields.Selection(
        selection=[
            ("duplicate", "Duplicate"),
            ("new", "New Claim"),
        ],
        string="Resolution",
        copy=False,
        help="Responsible's decision for a prescription reappearing from a "
        "previous period: 'Duplicate' excludes it from this return's credit "
        "and links it to the previous claim; 'New Claim' includes it.",
    )
    error_ids = fields.One2many(
        comodel_name="spms.return.invoice.line.error",
        inverse_name="line_id",
        string="Errors",
        copy=False,
    )
    error_codes = fields.Char(
        string="Error Codes",
        compute="_compute_error_codes",
        store=True,
        help="Error codes reported for this prescription, concatenated.",
    )

    _sql_constraints = [
        (
            "return_invoice_prescription_uniq",
            "unique(return_invoice_id, prescription)",
            "A prescription can only appear once in the same return invoice.",
        ),
    ]

    @api.depends("amount_billed", "amount_allowed")
    def _compute_amount_difference(self):
        for rec in self:
            rec.amount_difference = rec.amount_billed - rec.amount_allowed

    @api.depends("amount_billed", "amount_allowed_taxed")
    def _compute_amount_credit_taxed(self):
        for rec in self:
            rec.amount_credit_taxed = (
                rec.amount_billed * SPMS_TAX_FACTOR - rec.amount_allowed_taxed
            )

    @api.depends("error_ids.code")
    def _compute_error_codes(self):
        for rec in self:
            codes = []
            for code in rec.error_ids.mapped("code"):
                if code and code not in codes:
                    codes.append(code)
            rec.error_codes = " / ".join(codes)

    def write(self, vals):
        if "resolution" in vals:
            for rec in self:
                invoice = rec.return_invoice_id
                if (
                    invoice.state == "done"
                    and invoice.credit_note_move_id
                    and invoice.credit_note_move_id.state != "cancel"
                ):
                    raise UserError(
                        _(
                            "The credit of %s is already carried by a credit "
                            "note; cancel that credit note first to change a "
                            "resolution."
                        )
                        % invoice.display_name
                    )
        res = super().write(vals)
        if "resolution" in vals:
            self.mapped("return_invoice_id")._update_state()
        return res

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

    def action_show_errors(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Prescription Errors"),
            "res_model": "spms.return.invoice.line",
            "res_id": self.id,
            "view_mode": "form",
            "view_id": self.env.ref(
                "l10n_pt_invoice_spms_return.spms_return_invoice_line_form_view"
            ).id,
            "target": "new",
        }
