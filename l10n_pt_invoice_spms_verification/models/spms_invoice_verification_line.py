# Copyright 2026 NuoBiT Solutions SL - Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_compare, float_is_zero, float_round


class SpmsInvoiceVerificationLine(models.Model):
    """One row per claim (prestação) reported by the verification document.

    The claim element is the only place of the document carrying money
    and quantities, once per prescription, so the amounts live here and
    nowhere else: a sum over the lines is a sum over the prescriptions.
    The errors the document anchors under the claim (claim, line and
    prescription-data levels) hang from the line.
    """

    _name = "spms.invoice.verification.line"
    _description = "SPMS Invoice Verification Line"
    _order = "result_id, id"
    _rec_name = "prescription"
    _check_company_auto = True

    result_id = fields.Many2one(
        comodel_name="spms.invoice.verification",
        string="Invoice Verification",
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
        help="Claim total as read by the verification, untaxed (ValorTotalLido); "
        "read from the document, never recomputed.",
    )
    amount_allowed = fields.Monetary(
        string="Allowed Amount",
        readonly=True,
        help="Claim total recomputed by the verification, untaxed "
        "(ValorTotalCalculado); read from the document, never recomputed.",
    )
    amount_difference = fields.Monetary(
        string="Difference",
        compute="_compute_amount_difference",
        store=True,
        help="Billed minus allowed, untaxed: positive for an over-billed "
        "claim, negative when the verification allowed more than billed. This is "
        "the base the credit-note line will carry, sign included; VAT is "
        "applied by the line's taxes.",
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
        help="Original invoice line of the claim: the line billing its "
        "prescription; when the prescription is billed on several lines, "
        "the one with the claim's billed quantity and amount, each line "
        "paired with one claim at most.",
    )
    note_move_line_id = fields.Many2one(
        comodel_name="account.move.line",
        string="Note Line",
        readonly=True,
        check_company=True,
        help="Line of the generated credit or debit note carrying this claim "
        "(audit only).",
    )
    error_ids = fields.One2many(
        comodel_name="spms.invoice.verification.error",
        inverse_name="line_id",
        string="Errors",
    )

    _sql_constraints = [
        (
            "result_move_line_uniq",
            "unique(result_id, move_line_id)",
            "An original invoice line carries at most one claim per "
            "verification result.",
        ),
    ]

    @api.depends("amount_billed", "amount_allowed")
    def _compute_amount_difference(self):
        for rec in self:
            rec.amount_difference = rec.amount_billed - rec.amount_allowed

    # -- pairing claims with invoice lines ---------------------------------
    #
    # The prescription number is the billing-line key of a claim, but an
    # invoice can bill one prescription on several lines (two periods, two
    # products) and the document then returns one claim per line with no
    # line reference. The pairing rule: the prescription alone when it is
    # billed once and claimed once; otherwise the billed quantity and
    # untaxed amount tell the lines apart, N identical claims and N
    # identical lines pair one-to-one in order (the note comes out the same
    # either way), and a line never carries two claims. A claim without a
    # distinct compatible line is left unpaired: the generation reports it.

    @api.model
    def _pairing_key(self, prescription, quantity, amount, rounding):
        """The key two sides of the pairing compare: the prescription and,
        rounded to what a document value and an invoice value can share,
        the quantity and the untaxed amount."""
        return (
            prescription,
            float_round(
                quantity,
                precision_digits=self.env["decimal.precision"].precision_get(
                    "Product Unit of Measure"
                ),
            ),
            float_round(amount, precision_rounding=rounding),
        )

    @api.model
    def _invoice_line_key(self, line, rounding):
        return self._pairing_key(
            line.spms_prescription, line.quantity, line.price_subtotal, rounding
        )

    @api.model
    def _pair_one_to_one(self, keys, candidates):
        """Pair each key with a distinct candidate of its prescription.

        `keys`: pairing keys in document order; `candidates`: (pairing key,
        value) pairs in invoice order. Returns {position in `keys`: value}.
        One key and one candidate for a prescription pair on the
        prescription alone; otherwise a key takes the first unused
        candidate with its exact key, in order. A key left without a
        candidate is absent from the result.
        """
        pool_by_prescription = {}
        for key, value in candidates:
            pool_by_prescription.setdefault(key[0], []).append((key, value))
        positions_by_prescription = {}
        for position, key in enumerate(keys):
            positions_by_prescription.setdefault(key[0], []).append((position, key))
        pairs = {}
        for prescription, positioned in positions_by_prescription.items():
            pool = list(pool_by_prescription.get(prescription, []))
            if len(positioned) == 1 and len(pool) == 1:
                pairs[positioned[0][0]] = pool[0][1]
            else:
                for position, key in positioned:
                    match = next(
                        (
                            index
                            for index, (candidate_key, _value) in enumerate(pool)
                            if candidate_key == key
                        ),
                        None,
                    )
                    if match is not None:
                        pairs[position] = pool.pop(match)[1]
        return pairs

    @api.model
    def _match_original_lines(self, move, claims):
        """The original invoice line of each claim, as a list aligned with
        `claims`: an empty recordset where no distinct compatible line
        exists (the generation reports it, never the parser).

        `claims`: (prescription, billed quantity, billed amount) tuples in
        document order.
        """
        rounding = move.currency_id.rounding
        pairs = self._pair_one_to_one(
            [
                self._pairing_key(prescription, quantity, amount, rounding)
                for prescription, quantity, amount in claims
            ],
            [
                (self._invoice_line_key(line, rounding), line)
                for line in move.invoice_line_ids.filtered("spms_prescription")
            ],
        )
        empty = self.env["account.move.line"]
        return [pairs.get(position, empty) for position in range(len(claims))]

    @api.model
    def _match_copied_lines(self, move, draft):
        """The copy each invoice line of `move` has on `draft` — a note
        draft built by copying the invoice — as {invoice line id: draft
        line}, paired like the claims so that a prescription billed on
        several lines keeps one draft line per invoice line."""
        rounding = move.currency_id.rounding
        originals = move.invoice_line_ids.filtered("spms_prescription")
        pairs = self._pair_one_to_one(
            [self._invoice_line_key(line, rounding) for line in originals],
            [
                (self._invoice_line_key(line, rounding), line)
                for line in draft.invoice_line_ids.filtered("spms_prescription")
            ],
        )
        return {originals[position].id: copy for position, copy in pairs.items()}

    def _is_creditable(self):
        """A claim the credit note must carry: keyed by prescription and
        with a difference in either direction. An over-billed claim
        (positive difference) credits the customer; a claim the verification
        allowed above the billed amount (negative difference) becomes a
        negative line, because the official value nets both kinds."""
        self.ensure_one()
        return bool(self.prescription) and (
            float_compare(
                self.amount_difference,
                0.0,
                precision_rounding=self.currency_id.rounding or 0.01,
            )
            != 0
        )

    def _get_refund_line_values(self):
        """Values to write on the copied refund line, empty to keep it as-is.

        Money is authoritative: the resulting line must carry exactly the
        difference (billed - allowed). Total rejection keeps the copied line
        untouched (original days, price/day and SPMS dates); a partial
        rejection with reliable days credits the rejected days at the
        original price/day, dated as the first rejected days of the original
        period (the verification says how many days SPMS cut, never which ones; the
        customer's hand-made notes use the same convention); otherwise the
        whole difference goes on a single unit over the original period, as
        a negative difference always does: the line then carries the
        negative amount on one unit. SPMS requires both dates on every line
        of the note, so no shape leaves them blank.
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
            if not original_line.spms_start_date:
                raise UserError(
                    _(
                        "The rejected days of prescription %(prescription)s "
                        "cannot be dated: invoice line %(line)s has no SPMS "
                        "start date."
                    )
                    % {
                        "prescription": self.prescription,
                        "line": original_line.display_name,
                    }
                )
            return {
                "quantity": days_rejected,
                "spms_end_date": original_line.spms_start_date
                + timedelta(days=days_rejected - 1),
            }
        return {
            "quantity": 1,
            "price_unit": self.amount_difference,
        }

    def _get_note_line_values(self, debit):
        """The refund values as they are on a credit note; on a debit note
        the same line with the sign of its amount flipped (the quantity
        stays positive), so the note charges back what the verification allowed
        above the billed amount. A total rejection keeps the copied line
        as-is on a credit note, so on a debit note its price is negated."""
        self.ensure_one()
        values = self._get_refund_line_values()
        if debit:
            values["price_unit"] = -values.get(
                "price_unit", self.move_line_id.price_unit
            )
        return values
