# Copyright 2026 NuoBiT Solutions SL - Deniz Gallo <dgallo@nuobit.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_compare, float_round, formatLang

from .spms_return_invoice_line import SPMS_TAX_FACTOR


class SpmsReturnInvoice(models.Model):
    _name = "spms.return.invoice"
    _description = "SPMS Return Invoice"
    _order = "return_id desc, name"

    return_id = fields.Many2one(
        comodel_name="spms.return",
        string="SPMS Return",
        required=True,
        ondelete="cascade",
    )
    company_id = fields.Many2one(
        related="return_id.company_id",
        store=True,
        readonly=True,
    )
    currency_id = fields.Many2one(
        related="company_id.currency_id",
        readonly=True,
    )
    name = fields.Char(
        string="Invoice Number",
        required=True,
        index=True,
        help="Invoice number as reported by SPMS (NUMFACTURA), e.g. FT2026-416.",
    )
    move_id = fields.Many2one(
        comodel_name="account.move",
        string="Original Invoice",
        readonly=True,
        help="Original customer invoice matched by the SPMS invoice number.",
    )
    credit_note_move_id = fields.Many2one(
        comodel_name="account.move",
        string="Credit Note",
        readonly=True,
        help="Draft credit note generated for this invoice, or the "
        "pre-existing one detected by the duplicate check.",
    )
    state = fields.Selection(
        selection=[
            ("awaiting_official", "Awaiting Official Value"),
            ("ready", "Ready"),
            ("not_found", "Not Found"),
            ("already_done", "Already Done"),
            ("mismatch", "Mismatch"),
            ("done", "Done"),
        ],
        string="State",
        readonly=True,
        copy=False,
    )
    credit_official = fields.Monetary(
        string="Official Credit",
        copy=False,
        help="Official credit-note value for this invoice, taxes included, "
        "as communicated by the CCMSNS conference result. The generated "
        "credit note must carry exactly this value.",
    )
    official_source = fields.Selection(
        selection=[
            ("manual", "Manual"),
            ("portal", "Portal Results"),
        ],
        string="Official Value Source",
        readonly=True,
        copy=False,
    )
    official_date = fields.Date(
        string="Official Value Date",
        readonly=True,
        copy=False,
    )
    official_confirmed = fields.Boolean(
        string="Official Confirmed",
        copy=False,
        help="Set when the official credit-note value has been confirmed. "
        "Credit-note generation is gated on this flag.",
    )
    line_ids = fields.One2many(
        comodel_name="spms.return.invoice.line",
        inverse_name="return_invoice_id",
        string="Prescriptions",
        copy=False,
    )
    line_count = fields.Integer(
        string="# Prescriptions",
        compute="_compute_line_count",
    )
    has_data_error = fields.Boolean(
        compute="_compute_has_data_error",
    )
    credit_estimated = fields.Monetary(
        string="Estimated Credit",
        compute="_compute_credit_estimated",
        store=True,
        help="Formula estimate of the credit, taxes included: "
        "round(sum(billed x 1.06 - allowed-with-VAT)) over unique "
        "prescriptions, excluding lines resolved as duplicates. "
        "Informational preview and cross-check; the official value is "
        "authoritative.",
    )
    amount_lines_untaxed = fields.Monetary(
        string="Lines Amount (Untaxed)",
        compute="_compute_amount_lines_untaxed",
        store=True,
        help="Sum of the per-prescription credit bases, without taxes: "
        "what the generated credit-note lines will add up to. Excludes "
        "lines resolved as duplicates.",
    )
    error_codes = fields.Char(
        string="Error Codes",
        compute="_compute_error_codes",
        store=True,
        help="Distinct error codes reported for this invoice.",
    )

    _sql_constraints = [
        (
            "return_move_uniq",
            "unique(return_id, move_id)",
            "An invoice can only appear once in the same SPMS return.",
        ),
    ]

    @api.depends("line_ids")
    def _compute_line_count(self):
        for rec in self:
            rec.line_count = len(rec.line_ids)

    @api.depends("line_ids.state")
    def _compute_has_data_error(self):
        for rec in self:
            rec.has_data_error = any(
                line.state == "data_error" for line in rec.line_ids
            )

    @api.depends(
        "line_ids.amount_billed",
        "line_ids.amount_allowed_taxed",
        "line_ids.resolution",
    )
    def _compute_credit_estimated(self):
        for rec in self:
            lines = rec.line_ids.filtered(lambda line: line.resolution != "duplicate")
            rec.credit_estimated = float_round(
                sum(
                    line.amount_billed * SPMS_TAX_FACTOR - line.amount_allowed_taxed
                    for line in lines
                ),
                precision_rounding=rec.currency_id.rounding or 0.01,
            )

    @api.depends("line_ids.amount_difference", "line_ids.resolution")
    def _compute_amount_lines_untaxed(self):
        for rec in self:
            rec.amount_lines_untaxed = sum(
                line.amount_difference
                for line in rec.line_ids
                if line.amount_difference > 0 and line.resolution != "duplicate"
            )

    @api.depends("line_ids.error_ids.code")
    def _compute_error_codes(self):
        for rec in self:
            codes = []
            for code in rec.line_ids.mapped("error_ids.code"):
                if code and code not in codes:
                    codes.append(code)
            rec.error_codes = " / ".join(codes)

    def write(self, vals):
        official_touched = "credit_official" in vals or "official_confirmed" in vals
        if official_touched:
            for rec in self:
                if (
                    rec.state == "done"
                    and rec.credit_note_move_id
                    and rec.credit_note_move_id.state != "cancel"
                ):
                    raise UserError(
                        _(
                            "The official value of %s is already carried by a "
                            "credit note; cancel that credit note first to "
                            "change it."
                        )
                        % rec.display_name
                    )
        if "credit_official" in vals and not any(
            field in vals
            for field in (
                "official_confirmed",
                "official_source",
                "official_date",
            )
        ):
            vals = dict(
                vals,
                official_confirmed=True,
                official_source="manual",
                official_date=fields.Date.context_today(self),
            )
        res = super().write(vals)
        if official_touched:
            self._update_state()
        return res

    def unlink(self):
        if not self.env.context.get("spms_return_reprocess"):
            for rec in self:
                if rec.return_id.state not in ("draft", "cancel"):
                    raise UserError(
                        _(
                            "Invoices of %s can only be deleted while it is "
                            "draft or cancelled."
                        )
                        % rec.return_id.display_name
                    )
        return super().unlink()

    def _update_state(self):
        """Single source of truth for the invoice state (semaphore).

        Called after processing, after an official value is written and
        after a line resolution changes. Never downgrades a generated
        invoice (state 'done') while its credit note is alive; once that
        credit note is cancelled or deleted, the regular evaluation below
        reopens the invoice (the official value is kept) and drops its
        'done' return back to 'processed' so generation stays reachable.
        """
        for rec in self:
            if (
                rec.state == "done"
                and rec.credit_note_move_id
                and rec.credit_note_move_id.state != "cancel"
            ):
                continue
            if not rec.move_id:
                rec.state = "not_found"
                continue
            reversals = rec.move_id.reversal_move_id.filtered(
                lambda move: move.state != "cancel"
            )
            if reversals:
                rec.credit_note_move_id = reversals[0]
                total = sum(reversals.mapped("amount_total"))
                if (
                    float_compare(
                        total,
                        rec.credit_estimated,
                        precision_rounding=rec.currency_id.rounding or 0.01,
                    )
                    == 0
                ):
                    rec.state = "already_done"
                else:
                    rec.state = "mismatch"
                continue
            rec.credit_note_move_id = False
            pending_lines = rec.line_ids.filtered(
                lambda line: line.previous_line_id and not line.resolution
            )
            if pending_lines:
                rec.state = "mismatch"
                continue
            rec.state = "ready" if rec.official_confirmed else "awaiting_official"
        stale_returns = self.mapped("return_id").filtered(
            lambda ret: ret.state == "done"
            and ret.invoice_ids.filtered(
                lambda inv: inv.state not in ("done", "already_done")
            )
        )
        stale_returns.write({"state": "processed"})

    def _generate_credit_note(self):
        """Create the draft credit note for a ready (green) invoice.

        Goes through the standard reversal path (Reis/SAF-T PT requirement),
        then edits the draft down to the affected prescriptions. Raises
        UserError with the blocking reason; the caller runs each invoice in
        its own savepoint, so a raise leaves this invoice untouched.
        """
        self.ensure_one()
        self.invalidate_cache(["credit_official", "official_confirmed"], self.ids)
        self.env["account.move"].invalidate_cache(
            ["state", "reversal_move_id"], self.move_id.ids
        )
        self._update_state()
        if self.state != "ready":
            raise UserError(
                _("The invoice is no longer ready (current state: %s).")
                % dict(self._fields["state"].selection).get(self.state)
            )
        if self.move_id.state != "posted":
            raise UserError(
                _("The original invoice %s is not posted.") % self.move_id.display_name
            )
        rounding = self.currency_id.rounding or 0.01
        if float_compare(self.credit_official, 0, precision_rounding=rounding) <= 0:
            raise UserError(
                _(
                    "Nothing to credit on invoice %(invoice)s: the confirmed "
                    "official value is %(value)s."
                )
                % {
                    "invoice": self.move_id.display_name,
                    "value": formatLang(
                        self.env, self.credit_official, currency_obj=self.currency_id
                    ),
                }
            )
        if (
            float_compare(
                self.credit_official,
                self.move_id.amount_total,
                precision_rounding=rounding,
            )
            > 0
        ):
            raise UserError(
                _(
                    "The confirmed official value %(value)s exceeds the "
                    "total of the original invoice %(invoice)s (%(total)s)."
                )
                % {
                    "value": formatLang(
                        self.env, self.credit_official, currency_obj=self.currency_id
                    ),
                    "invoice": self.move_id.display_name,
                    "total": formatLang(
                        self.env,
                        self.move_id.amount_total,
                        currency_obj=self.currency_id,
                    ),
                }
            )
        lines = self._get_creditable_lines()
        draft = self._create_reversal_draft()
        self._edit_reversal_draft(draft, lines)
        self._check_draft_consistency(draft)
        difference = float_round(
            self.credit_official - draft.amount_total, precision_rounding=rounding
        )
        if float_compare(difference, 0.0, precision_rounding=rounding) != 0:
            self._append_adjustment_line(draft, difference)
        refund_line_map = {}
        for draft_line in draft.invoice_line_ids:
            refund_line_map.setdefault(draft_line.spms_prescription, draft_line)
        for line in lines:
            line.refund_move_line_id = refund_line_map.get(
                line.prescription, self.env["account.move.line"]
            )
        self.credit_note_move_id = draft
        self.state = "done"
        return draft

    def _get_creditable_lines(self):
        self.ensure_one()
        rounding = self.currency_id.rounding or 0.01
        lines = self.line_ids.filtered(
            lambda line: line.resolution != "duplicate"
            and float_compare(line.amount_difference, 0.0, precision_rounding=rounding)
            > 0
        )
        if not lines:
            raise UserError(_("There is no prescription with a positive difference."))
        unmatched = lines.filtered(lambda line: line.state != "matched")
        if unmatched:
            raise UserError(
                _("Prescriptions not matched to an original invoice line: %s.")
                % ", ".join(line.prescription or "?" for line in unmatched)
            )
        return lines

    def _create_reversal_draft(self):
        self.ensure_one()
        wizard = self.env["account.move.reversal"].create(
            {
                "move_ids": [(6, 0, self.move_id.ids)],
                "refund_method": "refund",
                "date_mode": "custom",
                "date": fields.Date.context_today(self),
                "reason": _("SPMS conference %s") % self.return_id.period,
                "company_id": self.company_id.id,
            }
        )
        wizard.reverse_moves()
        draft = wizard.new_move_ids
        if len(draft) != 1:
            raise UserError(
                _(
                    "The standard reversal did not create a single draft "
                    "credit note for %s."
                )
                % self.move_id.display_name
            )
        return draft

    def _edit_reversal_draft(self, draft, lines):
        """Cut the full-copy draft down to the affected prescriptions.

        The single write goes through the standard invoice business layer
        (`invoice_line_ids`), which recomputes subtotals, taxes and payment
        terms with the edited lines.
        """
        self.ensure_one()
        draft_line_map = {}
        for draft_line in draft.invoice_line_ids:
            draft_line_map.setdefault(draft_line.spms_prescription, []).append(
                draft_line.id
            )
        commands = []
        kept_draft_line_ids = []
        for line in lines:
            draft_line_ids = draft_line_map.get(line.prescription, [])
            if len(draft_line_ids) != 1:
                raise UserError(
                    _(
                        "Prescription %s cannot be mapped to a single line "
                        "of the credit-note draft."
                    )
                    % line.prescription
                )
            kept_draft_line_ids.append(draft_line_ids[0])
            line_values = line._get_refund_line_values()
            if line_values:
                commands.append((1, draft_line_ids[0], line_values))
        for draft_line in draft.invoice_line_ids:
            if draft_line.id not in kept_draft_line_ids:
                commands.append((2, draft_line.id))
        draft.write(
            {
                "invoice_origin": self.move_id.name,
                "invoice_line_ids": commands,
            }
        )

    def _check_draft_consistency(self, draft):
        """The single generation-time check (design §5): after cutting the
        draft down to the affected prescriptions and BEFORE any adjustment
        line, the draft must match the lines total the app computed from
        the Excel. A mismatch reveals a technical discrepancy (tax config,
        fiscal position, price drift) that a adjustment line must never
        absorb.

        The expected tax is computed as one globally rounded sum, matching
        the round_globally tax rounding method the SPMS flow relies on.
        """
        self.ensure_one()
        rounding = self.currency_id.rounding or 0.01
        if (
            float_compare(
                draft.amount_untaxed,
                self.amount_lines_untaxed,
                precision_rounding=rounding,
            )
            != 0
        ):
            raise UserError(
                _(
                    "The draft untaxed total (%(draft).2f) differs from the "
                    "prescription lines total (%(expected).2f); review the "
                    "draft lines before retrying."
                )
                % {
                    "draft": draft.amount_untaxed,
                    "expected": self.amount_lines_untaxed,
                }
            )
        expected_tax = float_round(
            sum(
                line.price_subtotal * tax.amount / 100.0
                for line in draft.invoice_line_ids
                for tax in line.tax_ids
            ),
            precision_rounding=rounding,
        )
        if (
            float_compare(draft.amount_tax, expected_tax, precision_rounding=rounding)
            != 0
        ):
            raise UserError(
                _(
                    "The draft tax amount (%(draft).2f) differs from the "
                    "expected tax (%(expected).2f) for the taxes on its "
                    "lines; review the tax configuration before retrying."
                )
                % {
                    "draft": draft.amount_tax,
                    "expected": expected_tax,
                }
            )

    def _append_adjustment_line(self, draft, difference):
        self.ensure_one()
        product = self.company_id.spms_adjustment_product_id
        if not product:
            raise UserError(
                _(
                    "Configure the SPMS adjustment line product on company "
                    "%s before generating credit notes that require an "
                    "adjustment line."
                )
                % self.company_id.display_name
            )
        taxes = product.taxes_id.filtered(lambda tax: tax.company_id == self.company_id)
        taxes = draft.fiscal_position_id.map_tax(taxes, product, draft.partner_id)
        if len(taxes) != 1 or taxes.amount_type != "percent":
            raise UserError(
                _(
                    "The adjustment line product %(product)s must carry "
                    "exactly one percentage customer tax for company "
                    "%(company)s."
                )
                % {
                    "product": product.display_name,
                    "company": self.company_id.display_name,
                }
            )
        rounding = self.currency_id.rounding or 0.01
        base = float_round(
            difference / (1 + taxes.amount / 100.0), precision_rounding=rounding
        )
        adjustment_vals = {
            "product_id": product.id,
            "quantity": 1.0,
            "price_unit": base,
            "tax_ids": [(6, 0, taxes.ids)],
        }
        draft.write({"invoice_line_ids": [(0, 0, adjustment_vals)]})
        if self._is_official_total_reached(draft):
            return
        for candidate in (base + rounding, base - rounding):
            # the invoice business layer silently drops (1, id) / (2, id)
            # commands aimed at the line it just recreated, so replace the
            # adjustment line instead: drop it through the line model and
            # create it again with the candidate base
            draft.invoice_line_ids.filtered(
                lambda line: line.product_id == product
            ).with_context(check_move_validity=False).unlink()
            draft.write(
                {
                    "invoice_line_ids": [
                        (0, 0, dict(adjustment_vals, price_unit=candidate))
                    ]
                }
            )
            if self._is_official_total_reached(draft):
                return
        self._force_group_tax_amount(draft, product, taxes, adjustment_vals)

    def _force_group_tax_amount(self, draft, product, taxes, adjustment_vals):
        """Write the group tax-line amount so the total matches the official
        value.

        With global tax rounding some official totals cannot be reached by
        moving the adjustment base alone (the rounded tax jumps a full cent):
        restore the theoretical base and force the remaining cent on the
        group tax line, compensating the payment term line to keep the
        entry balanced.
        """
        self.ensure_one()
        draft.invoice_line_ids.filtered(
            lambda line: line.product_id == product
        ).with_context(check_move_validity=False).unlink()
        draft.write({"invoice_line_ids": [(0, 0, dict(adjustment_vals))]})
        rounding = self.currency_id.rounding or 0.01
        difference = float_round(
            self.credit_official - draft.amount_total, precision_rounding=rounding
        )
        if float_compare(abs(difference), rounding, precision_rounding=rounding) > 0:
            raise UserError(
                _(
                    "The remaining difference %(difference).2f between the "
                    "official value %(official).2f and the credit note "
                    "total %(total).2f exceeds one rounding step "
                    "(%(step).2f); it cannot be absorbed by the imposed "
                    "tax amount."
                )
                % {
                    "difference": difference,
                    "official": self.credit_official,
                    "total": draft.amount_total,
                    "step": rounding,
                }
            )
        tax_line = draft.line_ids.filtered(lambda line: line.tax_line_id == taxes)
        if len(tax_line) != 1:
            raise UserError(
                _(
                    "Expected exactly one tax line for tax %(tax)s on the "
                    "draft credit note %(move)s, found %(count)d."
                )
                % {
                    "tax": taxes.display_name,
                    "move": draft.display_name,
                    "count": len(tax_line),
                }
            )
        term_line = draft.line_ids.filtered(
            lambda line: line.account_id.user_type_id.type in ("receivable", "payable")
        )
        if len(term_line) != 1:
            raise UserError(
                _(
                    "Expected exactly one receivable/payable line on the "
                    "draft credit note %(move)s, found %(count)d."
                )
                % {
                    "move": draft.display_name,
                    "count": len(term_line),
                }
            )
        draft.with_context(check_move_validity=False).write(
            {
                "line_ids": [
                    (
                        1,
                        tax_line.id,
                        {
                            "debit": float_round(
                                tax_line.debit + difference,
                                precision_rounding=rounding,
                            )
                        },
                    ),
                    (
                        1,
                        term_line.id,
                        {
                            "credit": float_round(
                                term_line.credit + difference,
                                precision_rounding=rounding,
                            )
                        },
                    ),
                ]
            }
        )
        if not self._is_official_total_reached(draft):
            raise UserError(
                _(
                    "The credit note total %(total).2f still differs from "
                    "the official value %(official).2f after imposing the "
                    "tax amount."
                )
                % {
                    "total": draft.amount_total,
                    "official": self.credit_official,
                }
            )

    def _is_official_total_reached(self, draft):
        return (
            float_compare(
                draft.amount_total,
                self.credit_official,
                precision_rounding=self.currency_id.rounding or 0.01,
            )
            == 0
        )

    def action_view_credit_note(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Credit Note"),
            "res_model": "account.move",
            "res_id": self.credit_note_move_id.id,
            "view_mode": "form",
        }
