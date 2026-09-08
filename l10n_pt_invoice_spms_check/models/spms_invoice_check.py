# Copyright 2026 NuoBiT Solutions SL - Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_compare, float_is_zero, float_round, formatLang

_logger = logging.getLogger(__name__)

OFFICIAL_VALUE_FIELDS = (
    "total_billed",
    "total_allowed",
    "total_billed_taxed",
    "total_allowed_taxed",
)


class SpmsInvoiceCheck(models.Model):
    _name = "spms.invoice.check"
    _description = "SPMS Invoice Check"
    _order = "fetch_date desc, id desc"
    _check_company_auto = True

    name = fields.Char(
        string="Number",
        readonly=True,
        copy=False,
        help="Number the CCF gave the check document (its ApplicationResponse ID).",
    )
    document_date = fields.Date(
        string="Document Date",
        readonly=True,
        copy=False,
        help="Date the CCF issued the check document.",
    )
    move_id = fields.Many2one(
        comodel_name="account.move",
        string="Original Invoice",
        required=True,
        readonly=True,
        index=True,
        ondelete="restrict",
        check_company=True,
        help="Invoice this check result belongs to. One result per "
        "invoice: the first definitive answer of the check closes "
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
    check_state = fields.Selection(
        selection=[
            ("with_errors", "Checked With Errors"),
            ("without_errors", "Checked Without Errors"),
        ],
        string="Check State",
        readonly=True,
        help="Definitive state reported by the check document "
        "(Conferida Com Erros / Conferida Sem Erros).",
    )
    fetch_date = fields.Datetime(
        string="Fetch Date",
        readonly=True,
        help="When the definitive check document was fetched from the " "web service.",
    )
    total_billed = fields.Monetary(
        string="Total Billed",
        readonly=True,
        help="Invoice total as read by the check, untaxed " "(TotalFaturaLido).",
    )
    total_allowed = fields.Monetary(
        string="Total Allowed",
        readonly=True,
        help="Invoice total recomputed by the check, untaxed "
        "(TotalFaturaCalculado).",
    )
    total_billed_taxed = fields.Monetary(
        string="Total Billed (Taxed)",
        readonly=True,
        help="Invoice total as read by the check, taxes included "
        "(TotalFaturaIVALido).",
    )
    total_allowed_taxed = fields.Monetary(
        string="Total Allowed (Taxed)",
        readonly=True,
        help="Invoice total recomputed by the check, taxes included "
        "(TotalFaturaIVACalculado).",
    )
    credit_official = fields.Monetary(
        string="Official Credit",
        compute="_compute_credit_official",
        store=True,
        help="Official value for this invoice, taxes included: "
        "TotalFaturaIVALido - TotalFaturaIVACalculado, exactly as the "
        "check document states it. The generated credit note must carry "
        "exactly this value; a negative value means the check computed "
        "more than billed, and the generated debit note carries exactly "
        "its absolute value.",
    )
    oficio = fields.Text(
        string="Official Result Notice",
        readonly=True,
        help="The ofício, the official letter the CCF communicates with the "
        "check result, as received.",
    )
    note_move_id = fields.Many2one(
        comodel_name="account.move",
        string="Credit/Debit Note",
        readonly=True,
        check_company=True,
        ondelete="set null",
        help="Draft credit note, or debit note when the official value is "
        "negative, generated for this invoice by this module. Full pointer "
        "means live note: it is released automatically the moment the note "
        "is cancelled or deleted.",
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
    line_ids = fields.One2many(
        comodel_name="spms.invoice.check.line",
        inverse_name="result_id",
        string="Lines",
        copy=False,
    )
    line_count = fields.Integer(
        string="# Lines",
        compute="_compute_line_count",
    )
    error_ids = fields.One2many(
        comodel_name="spms.invoice.check.error",
        inverse_name="result_id",
        string="Errors",
        copy=False,
        help="Every error the document reports, with and without line.",
    )
    error_count = fields.Integer(
        string="# Errors",
        compute="_compute_error_count",
    )
    document_error_ids = fields.One2many(
        comodel_name="spms.invoice.check.error",
        inverse_name="result_id",
        string="Document Errors",
        domain=[("line_id", "=", False)],
        help="Errors anchored to the invoice itself: they carry no "
        "prescription, so no line.",
    )
    official_locked = fields.Boolean(
        compute="_compute_official_locked",
    )
    amount_lines_untaxed = fields.Monetary(
        string="Claims Amount (Untaxed)",
        compute="_compute_amount_lines_untaxed",
        store=True,
        help="Net sum of the per-prescription differences, without taxes: "
        "over-billed claims minus the claims the check allowed above the "
        "billed amount, what the generated credit-note lines add up to "
        "(negated on a debit note).",
    )
    amount_lines_negative_untaxed = fields.Monetary(
        string="Negative Claims Amount (Untaxed)",
        compute="_compute_amount_lines_untaxed",
        store=True,
        help="Sum of the negative differences, without taxes: the claims "
        "the check allowed above the billed amount. Each one becomes a "
        "negative line of the credit note (a positive line of the debit "
        "note), netting against the over-billed claims exactly as the "
        "official value does.",
    )
    error_message = fields.Char(
        string="Error Message",
        compute="_compute_error_message",
        help="Why the result is held in error: a live credit or debit note "
        "this module did not create or a check outcome this module does not "
        "recognise. Computed live and never stored, so fixing the cause "
        "clears it on its own.",
    )
    completeness_warning = fields.Text(
        string="Completeness Warning",
        readonly=True,
        copy=False,
        help="Completeness warning raised while parsing the check "
        "document: some errors sit at positions the parser does not "
        "know, so the error list may be incomplete. The raw document "
        "attached to this result is the authority. Never blocks the "
        "flow.",
    )
    generation_error = fields.Text(
        string="Generation Error",
        readonly=True,
        copy=False,
        help="Why the automatic note generation of this result failed; the "
        "result is held in Error until the cause is fixed and the document "
        "is reprocessed. Other results of the same batch are never dragged "
        "along.",
    )

    _sql_constraints = [
        (
            "move_uniq",
            "unique(move_id)",
            "An invoice can only carry one check result.",
        ),
    ]

    @api.depends("total_billed_taxed", "total_allowed_taxed")
    def _compute_credit_official(self):
        for rec in self:
            rec.credit_official = float_round(
                rec.total_billed_taxed - rec.total_allowed_taxed,
                precision_rounding=rec.currency_id.rounding or 0.01,
            )

    @api.depends("line_ids")
    def _compute_line_count(self):
        for rec in self:
            rec.line_count = len(rec.line_ids)

    @api.depends("error_ids")
    def _compute_error_count(self):
        for rec in self:
            rec.error_count = len(rec.error_ids)

    @api.depends("state", "note_move_id.state")
    def _compute_official_locked(self):
        for rec in self:
            rec.official_locked = (
                rec.state == "done"
                and bool(rec.note_move_id)
                and rec.note_move_id.state != "cancel"
            )

    @api.depends("line_ids.amount_difference", "line_ids.prescription")
    def _compute_amount_lines_untaxed(self):
        for rec in self:
            rounding = rec.currency_id.rounding or 0.01
            differences = [
                line.amount_difference for line in rec.line_ids if line._is_creditable()
            ]
            rec.amount_lines_untaxed = sum(differences)
            rec.amount_lines_negative_untaxed = sum(
                difference
                for difference in differences
                if float_compare(difference, 0.0, precision_rounding=rounding) < 0
            )

    def name_get(self):
        # a result created out of band has no document, hence no number:
        # name it after its invoice so the user still knows what it is
        return [(rec.id, rec.name or rec.move_id.display_name) for rec in self]

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._update_state()
        return records

    def write(self, vals):
        official_touched = any(field in vals for field in OFFICIAL_VALUE_FIELDS)
        state_touched = official_touched or any(
            field in vals for field in ("check_state", "generation_error")
        )
        if official_touched:
            for rec in self:
                if (
                    rec.state == "done"
                    and rec.note_move_id
                    and rec.note_move_id.state != "cancel"
                ):
                    raise UserError(
                        _(
                            "The official value of %s is already carried by a "
                            "credit or debit note; cancel that note first to "
                            "change it."
                        )
                        % rec.move_id.display_name
                    )
        res = super().write(vals)
        if state_touched:
            self._update_state()
        return res

    def _is_debit(self):
        """A negative official value: the check computed more than billed,
        so the note charges the difference back, as a debit note."""
        self.ensure_one()
        return (
            float_compare(
                self.credit_official,
                0.0,
                precision_rounding=self.currency_id.rounding or 0.01,
            )
            < 0
        )

    def _get_note_kind(self):
        """The kind of note the official value calls for, for messages."""
        self.ensure_one()
        return _("debit note") if self._is_debit() else _("credit note")

    def _get_note_total(self):
        """The total the generated note must carry: the official value
        without its sign, as a debit note charges back a negative one."""
        self.ensure_one()
        return abs(self.credit_official)

    def _get_foreign_notes(self):
        """Live notes of the invoice, of the kind the official value calls
        for, that this module did not create: credit notes for a positive
        value, debit notes (hanging from the invoice as their debit origin)
        for a negative one. The other kind never blocks: a debit note
        billing a prescription late has nothing to do with the credit note
        of the check."""
        self.ensure_one()
        if self._is_debit():
            notes, move_type = self.move_id.debit_note_ids, "out_invoice"
        else:
            notes, move_type = self.move_id.reversal_move_id, "out_refund"
        return (
            notes.filtered(
                lambda move: move.move_type == move_type and move.state != "cancel"
            )
            - self.note_move_id
        )

    @api.depends(
        "move_id.reversal_move_id.state",
        "move_id.debit_note_ids.state",
        "note_move_id",
        "check_state",
        "generation_error",
        "credit_official",
    )
    def _compute_error_message(self):
        for rec in self:
            foreign_notes = rec._get_foreign_notes()
            if foreign_notes:
                rec.error_message = _(
                    "The invoice already has a live %(kind)s this module "
                    "did not create: %(notes)s. Only one %(kind)s may exist "
                    "per invoice; fix or cancel it in accounting first."
                ) % {
                    "kind": rec._get_note_kind(),
                    "notes": ", ".join(foreign_notes.mapped("display_name")),
                }
            elif rec.generation_error:
                rec.error_message = rec.generation_error
            elif rec._is_unrecognized_outcome():
                rec.error_message = _(
                    "The check result of %(invoice)s does not match any "
                    "recognised outcome (official credit: %(value)s); it "
                    "is held for human review. The raw document attached "
                    "to this result is the authority."
                ) % {
                    "invoice": rec.move_id.display_name,
                    "value": formatLang(
                        self.env,
                        rec.credit_official,
                        currency_obj=rec.currency_id,
                    ),
                }
            else:
                rec.error_message = False

    def _is_unrecognized_outcome(self):
        """A verdict/totals combination outside the flows this module knows.

        Known: a with-errors verdict (its generation guards already hold
        odd totals loudly) and a no-errors verdict with zero official
        credit. Anything else must never close or park silently.
        """
        self.ensure_one()
        if self.check_state == "with_errors":
            return False
        return self.check_state != "without_errors" or not float_is_zero(
            self.credit_official,
            precision_rounding=self.currency_id.rounding or 0.01,
        )

    def _update_state(self):
        """Single source of truth for the result state (semaphore).

        Called after creation, after the official totals are written and
        after generation. A result whose own note (credit note, or debit
        note for a negative official value) is alive is 'done'; any other
        live note of that kind — made by hand or by anyone else — is an
        'error' even alongside our own note, because only one note may
        regularize an invoice (a human fixes accounting; the module never
        adopts a foreign note). Once our note is cancelled or deleted, the
        regular evaluation below reopens the result (the official value
        is kept) so generation stays reachable. An official value of zero
        closes the result as 'zero_official': legitimately settled,
        nothing to regularize — the
        'Conferida Sem Erros' results land here by construction.
        """
        for rec in self:
            foreign_notes = rec._get_foreign_notes()
            own_note_alive = rec.note_move_id and rec.note_move_id.state != "cancel"
            if own_note_alive and not foreign_notes:
                rec.state = "done"
                continue
            if rec.note_move_id and not own_note_alive:
                rec.note_move_id = False
            if foreign_notes:
                rec.state = "error"
                continue
            if rec.generation_error:
                rec.state = "error"
                continue
            if rec._is_unrecognized_outcome():
                rec.state = "error"
                continue
            if float_is_zero(
                rec.credit_official,
                precision_rounding=rec.currency_id.rounding or 0.01,
            ):
                rec.state = "zero_official"
            else:
                rec.state = "ready"

    def _generate_note_or_hold(self):
        """One savepoint per result: a failure holds that result alone
        in Error with its reason; reprocessing the document retries."""
        for rec in self:
            if rec.check_state != "with_errors" or rec.state != "ready":
                continue
            try:
                with self.env.cr.savepoint():
                    rec._generate_note()
            except UserError as err:
                rec.generation_error = err.args[0] if err.args else str(err)
            except Exception:
                _logger.exception(
                    "Note generation of %s failed unexpectedly",
                    rec.move_id.display_name,
                )
                rec.generation_error = _(
                    "Unexpected generation failure; see the server log."
                )

    def _generate_note(self):
        """Create the draft note for a ready (green) result: a credit note,
        or a debit note when the official value is negative.

        Goes through the standard reversal or debit-note path (Reis/SAF-T
        PT requirement), then edits the draft down to the affected
        prescriptions, with the sign flipped on a debit note. Raises
        UserError with the blocking reason; the caller runs each result in
        its own savepoint, so a raise leaves this result untouched.
        """
        self.ensure_one()
        self.invalidate_cache(["credit_official"], self.ids)
        self.env["account.move"].invalidate_cache(
            ["state", "reversal_move_id", "debit_note_ids"], self.move_id.ids
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
        # ready means a non-zero official value: its sign picks the note
        debit = self._is_debit()
        if not debit and (
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
        draft = self._create_debit_draft() if debit else self._create_reversal_draft()
        self._edit_note_draft(draft, lines, debit)
        self._check_draft_consistency(draft, debit)
        difference = float_round(
            self._get_note_total() - draft.amount_total, precision_rounding=rounding
        )
        if float_compare(difference, 0.0, precision_rounding=rounding) != 0:
            self._check_adjustment_limit(draft, difference)
            self._impose_official_tax(draft, difference)
        note_line_map = {}
        for draft_line in draft.invoice_line_ids:
            note_line_map.setdefault(draft_line.spms_prescription, draft_line)
        for line in lines:
            line.note_move_line_id = note_line_map.get(
                line.prescription, self.env["account.move.line"]
            )
        self.note_move_id = draft
        self.state = "done"
        return draft

    def _get_creditable_lines(self):
        """The claims with a difference, positive or negative, each matched
        to its original invoice line."""
        self.ensure_one()
        lines = self.line_ids.filtered(lambda line: line._is_creditable())
        if not lines:
            raise UserError(
                _("There is no prescription with a difference to regularize.")
            )
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
                "reason": _("SPMS check %s") % self.move_id.name,
                "company_id": self.company_id.id,
                # the CCF cut is definitive: the sale order must not reopen
                "sale_qty_to_reinvoice": False,
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

    def _create_debit_draft(self):
        """The standard debit-note path, the mirror of the reversal: a full
        copy of the invoice hanging from it as its debit origin, cut down
        afterwards like the credit-note draft."""
        self.ensure_one()
        wizard = (
            self.env["account.debit.note"]
            .with_context(active_model="account.move", active_ids=self.move_id.ids)
            .create(
                {
                    "date": fields.Date.context_today(self),
                    "reason": _("SPMS check %s") % self.move_id.name,
                    "copy_lines": True,
                }
            )
        )
        action = wizard.create_debit()
        draft = self.env["account.move"].browse(action.get("res_id"))
        if (
            len(draft) != 1
            or draft.debit_origin_id != self.move_id
            or draft.move_type != "out_invoice"
        ):
            raise UserError(
                _(
                    "The standard debit-note flow did not create a single "
                    "draft debit note for %s."
                )
                % self.move_id.display_name
            )
        return draft

    def _edit_note_draft(self, draft, lines, debit):
        """Cut the full-copy draft down to the affected prescriptions.

        The single write goes through the standard invoice business layer
        (`invoice_line_ids`), which recomputes subtotals, taxes and payment
        terms with the edited lines. A debit note also drops the sale-order
        link the debit-note flow copies onto its lines: the CCF cut is
        definitive either way, and a debit line linked to the order would
        count its quantity as invoiced twice.
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
                        "of the note draft."
                    )
                    % line.prescription
                )
            kept_draft_line_ids.append(draft_line_ids[0])
            line_values = line._get_note_line_values(debit)
            if debit:
                line_values["sale_line_ids"] = [(5, 0, 0)]
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

    def _check_draft_consistency(self, draft, debit):
        """The single generation-time check (design §5): after cutting the
        draft down to the affected prescriptions and BEFORE the tax
        adjustment, the draft must match the claims total computed from
        the check result, negated on a debit note. A mismatch reveals a
        technical discrepancy (tax config, fiscal position, price drift)
        that the tax adjustment must never absorb.

        The expected tax is computed as one globally rounded sum, matching
        the round_globally tax rounding method the SPMS flow relies on.
        """
        self.ensure_one()
        rounding = self.currency_id.rounding or 0.01
        expected_untaxed = (
            -self.amount_lines_untaxed if debit else self.amount_lines_untaxed
        )
        if (
            float_compare(
                draft.amount_untaxed,
                expected_untaxed,
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
                    "expected": expected_untaxed,
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

    def _check_adjustment_limit(self, draft, difference):
        """The tax line absorbs the rounding cent only.

        A residual beyond the company limit reveals a discrepancy between
        the check lines and the official value (a claim the check priced
        differently, a line the parser could not read, a company rounding
        its taxes per line) that a human must review; plugging it silently
        would hide the discrepancy inside the note.
        """
        self.ensure_one()
        rounding = self.currency_id.rounding or 0.01
        limit = self.company_id.spms_adjustment_limit
        if float_compare(abs(difference), limit, precision_rounding=rounding) > 0:
            raise UserError(
                _(
                    "The %(kind)s cannot be generated: its total %(draft)s "
                    "differs from the official value %(official)s by "
                    "%(difference)s, and the SPMS adjustment limit of company "
                    "%(company)s allows at most %(limit)s.\n\n"
                    "Only the rounding cent of the tax may be written on the "
                    "tax line of the %(kind)s. A larger difference reveals "
                    "a discrepancy to review before retrying: the Claims "
                    "Credit of the result must match its official totals "
                    "before tax (a prescription the check priced differently "
                    "from the invoice makes them diverge), and the company "
                    "must round its taxes globally (Accounting > Settings > "
                    "Taxes > Rounding Method), as the CCF computes the tax "
                    "once on the invoice total. Raise the SPMS adjustment "
                    "limit of the company only when the difference is "
                    "legitimate and strictly necessary."
                )
                % {
                    "draft": formatLang(
                        self.env, draft.amount_total, currency_obj=self.currency_id
                    ),
                    "kind": self._get_note_kind(),
                    "official": formatLang(
                        self.env, self._get_note_total(), currency_obj=self.currency_id
                    ),
                    "difference": formatLang(
                        self.env, difference, currency_obj=self.currency_id
                    ),
                    "limit": formatLang(self.env, limit, currency_obj=self.currency_id),
                    "company": self.company_id.display_name,
                }
            )

    def _impose_official_tax(self, draft, difference):
        """Write the difference with the official value on the tax line.

        The claim bases come from the check document itself, so after the
        consistency check only the rounding of the tax remains: the CCF
        computes it once on the invoice total, Odoo once on the note
        total, and the two can differ by one cent. The official value is
        what the CCF validates the note against, so that cent is written
        on the tax line — its debit on a credit note, its credit on a debit
        note — the same correction an accountant would pencil on the tax
        journal item, compensating the receivable line to keep the entry
        balanced.
        """
        self.ensure_one()
        rounding = self.currency_id.rounding or 0.01
        tax_line = draft.line_ids.filtered("tax_line_id")
        if len(tax_line) != 1:
            raise UserError(
                _(
                    "Expected exactly one tax line on the draft %(kind)s "
                    "%(move)s to carry the difference with the official "
                    "value, found %(count)d."
                )
                % {
                    "kind": self._get_note_kind(),
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
                    "draft %(kind)s %(move)s, found %(count)d."
                )
                % {
                    "kind": self._get_note_kind(),
                    "move": draft.display_name,
                    "count": len(term_line),
                }
            )
        # a credit note carries its tax as a debit and its receivable as a
        # credit; a debit note the other way round
        debit_note = draft.move_type == "out_invoice"
        tax_side = "credit" if debit_note else "debit"
        term_side = "debit" if debit_note else "credit"
        draft.with_context(check_move_validity=False).write(
            {
                "line_ids": [
                    (
                        1,
                        tax_line.id,
                        {
                            tax_side: float_round(
                                tax_line[tax_side] + difference,
                                precision_rounding=rounding,
                            )
                        },
                    ),
                    (
                        1,
                        term_line.id,
                        {
                            term_side: float_round(
                                term_line[term_side] + difference,
                                precision_rounding=rounding,
                            )
                        },
                    ),
                ]
            }
        )
        if (
            float_compare(
                draft.amount_total, self._get_note_total(), precision_rounding=rounding
            )
            != 0
        ):
            raise UserError(
                _(
                    "The %(kind)s total %(total).2f still differs from "
                    "the official value %(official).2f after imposing the "
                    "tax amount."
                )
                % {
                    "kind": self._get_note_kind(),
                    "total": draft.amount_total,
                    "official": self._get_note_total(),
                }
            )

    def action_view_note(self):
        self.ensure_one()
        if not self.note_move_id:
            return False
        return {
            "type": "ir.actions.act_window",
            "name": _("Debit Note")
            if self.note_move_id.move_type == "out_invoice"
            else _("Credit Note"),
            "res_model": "account.move",
            "res_id": self.note_move_id.id,
            "view_mode": "form",
        }

    def action_view_lines(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Line Checks"),
            "res_model": "spms.invoice.check.line",
            "view_mode": "tree,form",
            "domain": [("result_id", "=", self.id)],
        }
