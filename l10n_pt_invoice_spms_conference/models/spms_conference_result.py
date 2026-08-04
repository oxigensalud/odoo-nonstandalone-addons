# Copyright 2026 NuoBiT Solutions SL - Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_compare, float_is_zero, float_round, formatLang

OFFICIAL_VALUE_FIELDS = (
    "total_billed",
    "total_allowed",
    "total_billed_taxed",
    "total_allowed_taxed",
)


class SpmsConferenceResult(models.Model):
    _name = "spms.conference.result"
    _description = "SPMS Conference Result"
    _order = "fetch_date desc, id desc"
    _rec_name = "move_id"
    _check_company_auto = True

    move_id = fields.Many2one(
        comodel_name="account.move",
        string="Original Invoice",
        required=True,
        readonly=True,
        index=True,
        ondelete="restrict",
        check_company=True,
        help="Invoice this conference result belongs to. One result per "
        "invoice: the first definitive answer of the conference closes "
        "the invoice permanently.",
    )
    company_id = fields.Many2one(
        related="move_id.company_id",
        store=True,
        readonly=True,
    )
    currency_id = fields.Many2one(
        related="company_id.currency_id",
        store=True,
        readonly=True,
    )
    conference_state = fields.Selection(
        selection=[
            ("with_errors", "Conferred With Errors"),
            ("without_errors", "Conferred Without Errors"),
        ],
        string="Conference State",
        readonly=True,
        help="Definitive state reported by the conference document "
        "(Conferida Com Erros / Conferida Sem Erros).",
    )
    fetch_date = fields.Datetime(
        string="Fetch Date",
        readonly=True,
        help="When the definitive conference document was fetched from the "
        "web service.",
    )
    total_billed = fields.Monetary(
        string="Total Billed",
        readonly=True,
        help="Invoice total as read by the conference, untaxed " "(TotalFaturaLido).",
    )
    total_allowed = fields.Monetary(
        string="Total Allowed",
        readonly=True,
        help="Invoice total recomputed by the conference, untaxed "
        "(TotalFaturaCalculado).",
    )
    total_billed_taxed = fields.Monetary(
        string="Total Billed (Taxed)",
        readonly=True,
        help="Invoice total as read by the conference, taxes included "
        "(TotalFaturaIVALido).",
    )
    total_allowed_taxed = fields.Monetary(
        string="Total Allowed (Taxed)",
        readonly=True,
        help="Invoice total recomputed by the conference, taxes included "
        "(TotalFaturaIVACalculado).",
    )
    credit_official = fields.Monetary(
        string="Official Credit",
        compute="_compute_credit_official",
        store=True,
        help="Official credit-note value for this invoice, taxes included: "
        "TotalFaturaIVALido - TotalFaturaIVACalculado, exactly as the "
        "conference document states it. The generated credit note must "
        "carry exactly this value.",
    )
    oficio = fields.Text(
        string="Ofício",
        readonly=True,
        help="Ofício text communicated with the conference result, as " "received.",
    )
    credit_note_move_id = fields.Many2one(
        comodel_name="account.move",
        string="Credit Note",
        readonly=True,
        check_company=True,
        ondelete="set null",
        help="Draft credit note generated for this invoice by this module. "
        "Full pointer means live note: it is released automatically the "
        "moment the note is cancelled or deleted.",
    )
    state = fields.Selection(
        selection=[
            ("ready", "Ready"),
            ("error", "Error"),
            ("done", "Done"),
            ("zero_official", "Zero Official Value"),
        ],
        string="State",
        readonly=True,
        copy=False,
    )
    error_ids = fields.One2many(
        comodel_name="spms.conference.result.error",
        inverse_name="result_id",
        string="Errors",
        copy=False,
    )
    error_count = fields.Integer(
        string="# Errors",
        compute="_compute_error_count",
    )
    official_locked = fields.Boolean(
        compute="_compute_official_locked",
    )
    amount_lines_untaxed = fields.Monetary(
        string="Claims Amount (Untaxed)",
        compute="_compute_amount_lines_untaxed",
        store=True,
        help="Sum of the per-prescription credit bases, without taxes: "
        "what the generated credit-note lines will add up to.",
    )
    error_codes = fields.Char(
        string="Error Codes",
        compute="_compute_error_codes",
        store=True,
        help="Distinct error codes reported for this invoice.",
    )
    error_message = fields.Char(
        string="Error Message",
        compute="_compute_error_message",
        help="Why the result is held in error when the reason lives in "
        "accounting, not in this module: names the live credit note the "
        "module did not create. Computed live and never stored, so fixing "
        "accounting clears it on its own.",
    )

    _sql_constraints = [
        (
            "move_uniq",
            "unique(move_id)",
            "An invoice can only carry one conference result.",
        ),
    ]

    @api.depends("total_billed_taxed", "total_allowed_taxed")
    def _compute_credit_official(self):
        for rec in self:
            rec.credit_official = float_round(
                rec.total_billed_taxed - rec.total_allowed_taxed,
                precision_rounding=rec.currency_id.rounding or 0.01,
            )

    @api.depends("error_ids")
    def _compute_error_count(self):
        for rec in self:
            rec.error_count = len(rec.error_ids)

    @api.depends("state", "credit_note_move_id.state")
    def _compute_official_locked(self):
        for rec in self:
            rec.official_locked = (
                rec.state == "done"
                and bool(rec.credit_note_move_id)
                and rec.credit_note_move_id.state != "cancel"
            )

    @api.depends("error_ids.amount_difference", "error_ids.prescription")
    def _compute_amount_lines_untaxed(self):
        for rec in self:
            seen = set()
            total = 0.0
            for row in rec.error_ids:
                if not row.prescription or row.prescription in seen:
                    continue
                seen.add(row.prescription)
                if row.amount_difference > 0:
                    total += row.amount_difference
            rec.amount_lines_untaxed = total

    @api.depends("error_ids.code")
    def _compute_error_codes(self):
        for rec in self:
            codes = []
            for code in rec.error_ids.mapped("code"):
                if code and code not in codes:
                    codes.append(code)
            rec.error_codes = " / ".join(codes)

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._update_state()
        return records

    def write(self, vals):
        official_touched = any(field in vals for field in OFFICIAL_VALUE_FIELDS)
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
        res = super().write(vals)
        if official_touched:
            self._update_state()
        return res

    def _get_foreign_credit_notes(self):
        """Live credit notes of the invoice that this module did not create."""
        self.ensure_one()
        return (
            self.move_id.reversal_move_id.filtered(
                lambda move: move.move_type == "out_refund" and move.state != "cancel"
            )
            - self.credit_note_move_id
        )

    @api.depends("move_id.reversal_move_id.state", "credit_note_move_id")
    def _compute_error_message(self):
        for rec in self:
            foreign_notes = rec._get_foreign_credit_notes()
            rec.error_message = (
                _(
                    "The invoice already has a live credit note this module "
                    "did not create: %s. Only one credit note may exist per "
                    "invoice; fix or cancel it in accounting first."
                )
                % ", ".join(foreign_notes.mapped("display_name"))
                if foreign_notes
                else False
            )

    def _update_state(self):
        """Single source of truth for the result state (semaphore).

        Called after creation, after the official totals are written and
        after generation. A result whose own credit note is alive is
        'done'; any other live credit note — made by hand or by anyone
        else — is an 'error' even alongside our own note, because only
        one credit note may exist per invoice (a human fixes accounting;
        the module never adopts a foreign note). Once our credit note is
        cancelled or deleted, the regular evaluation below reopens the
        result (the official value is kept) so generation stays
        reachable. An official value of zero closes the result as
        'zero_official': legitimately settled, nothing to credit — the
        'Conferida Sem Erros' results land here by construction.
        """
        for rec in self:
            foreign_notes = rec._get_foreign_credit_notes()
            own_note_alive = (
                rec.credit_note_move_id and rec.credit_note_move_id.state != "cancel"
            )
            if own_note_alive and not foreign_notes:
                rec.state = "done"
                continue
            if rec.credit_note_move_id and not own_note_alive:
                rec.credit_note_move_id = False
            if foreign_notes:
                rec.state = "error"
                continue
            if float_is_zero(
                rec.credit_official,
                precision_rounding=rec.currency_id.rounding or 0.01,
            ):
                rec.state = "zero_official"
            else:
                rec.state = "ready"

    def _generate_credit_note(self):
        """Create the draft credit note for a ready (green) result.

        Goes through the standard reversal path (Reis/SAF-T PT requirement),
        then edits the draft down to the affected prescriptions. Raises
        UserError with the blocking reason; the caller runs each result in
        its own savepoint, so a raise leaves this result untouched.
        """
        self.ensure_one()
        self.invalidate_cache(["credit_official"], self.ids)
        self.env["account.move"].invalidate_cache(
            ["state", "reversal_move_id"], self.move_id.ids
        )
        self._update_state()
        if self.state != "ready":
            raise UserError(
                _("The result is no longer ready (current state: %s).")
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
                    "Nothing to credit on invoice %(invoice)s: the official "
                    "value is %(value)s."
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
                    "The official value %(value)s exceeds the total of the "
                    "original invoice %(invoice)s (%(total)s)."
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
        credited_prescriptions = set(lines.mapped("prescription"))
        for row in self.error_ids:
            if row.prescription in credited_prescriptions:
                row.refund_move_line_id = refund_line_map.get(
                    row.prescription, self.env["account.move.line"]
                )
        self.credit_note_move_id = draft
        self.state = "done"
        return draft

    def _get_creditable_lines(self):
        """One representative error row per prescription with a positive
        difference.

        Every error row of a prescription carries the same claim totals by
        construction (they all mirror the same conference claim), so the
        first row stands for the whole prescription.
        """
        self.ensure_one()
        rounding = self.currency_id.rounding or 0.01
        seen = set()
        lines = self.env["spms.conference.result.error"]
        for row in self.error_ids.sorted("id"):
            if not row.prescription or row.prescription in seen:
                continue
            seen.add(row.prescription)
            if (
                float_compare(row.amount_difference, 0.0, precision_rounding=rounding)
                > 0
            ):
                lines |= row
        if not lines:
            raise UserError(_("There is no prescription with a positive difference."))
        unmatched = lines.filtered(lambda line: not line.move_line_id)
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
                "reason": _("SPMS conference %s") % self.move_id.name,
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
        line, the draft must match the claims total computed from the
        conference result. A mismatch reveals a technical discrepancy (tax
        config, fiscal position, price drift) that an adjustment line must
        never absorb.

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
                    "prescription claims total (%(expected).2f); review the "
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
        self.ensure_one()
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
        if not self.credit_note_move_id:
            return False
        return {
            "type": "ir.actions.act_window",
            "name": _("Credit Note"),
            "res_model": "account.move",
            "res_id": self.credit_note_move_id.id,
            "view_mode": "form",
        }

    def action_view_errors(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Conference Errors"),
            "res_model": "spms.conference.result.error",
            "view_mode": "tree,form",
            "domain": [("result_id", "=", self.id)],
            "context": {"search_default_groupby_level": 1},
        }
