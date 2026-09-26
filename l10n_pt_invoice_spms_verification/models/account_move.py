# Copyright 2026 NuoBiT Solutions SL - Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, fields, models
from odoo.tools import html_escape


class AccountMove(models.Model):
    _inherit = "account.move"

    spms_invoice_verification_ids = fields.One2many(
        comodel_name="spms.invoice.verification",
        inverse_name="move_id",
        string="SPMS Invoice Verifications",
    )
    spms_note_invoice_verification_ids = fields.One2many(
        comodel_name="spms.invoice.verification",
        inverse_name="note_move_id",
        string="SPMS Invoice Verifications (Credit/Debit Note)",
    )
    spms_invoice_verification_state = fields.Selection(
        related="spms_invoice_verification_ids.verification_state",
        string="SPMS Verification State",
    )

    def action_view_spms_invoice_verification(self):
        """Open the invoice's verification result form directly (1:1)."""
        self.ensure_one()
        result = self.spms_invoice_verification_ids[:1]
        if not result:
            return False
        return {
            "type": "ir.actions.act_window",
            "name": _("Invoice Verification"),
            "res_model": "spms.invoice.verification",
            "res_id": result.id,
            "view_mode": "form",
        }

    def _spms_verification_results(self):
        """Verification results these moves take part in: the ones whose
        generated note they are, and the ones of the invoices they
        reverse or debit (a foreign note in the eyes of the result)."""
        return (
            self.spms_note_invoice_verification_ids
            | self.reversed_entry_id.spms_invoice_verification_ids
            | self.debit_origin_id.spms_invoice_verification_ids
        )

    def button_cancel(self):
        """Release the verification results these notes belong to, in the
        same transaction, and hold their exchange records for Retry.

        The pointer means 'live note': once the note is cancelled the
        result no longer carries one, which the original invoice's
        chatter records. A result that is Ready again — ours, or the one
        of the invoice a foreign note reversed — can only be generated
        again by processing its document again: its exchange record is
        held with Retry for that.
        """
        results = self._spms_verification_results()
        res = super().button_cancel()
        own = self.spms_note_invoice_verification_ids
        for result in own:
            result.move_id.message_post(
                body=_(
                    "The generated note %(note)s was cancelled: the verification "
                    "result of invoice %(invoice)s no longer carries a note."
                )
                % {
                    "note": html_escape(result.note_move_id.display_name),
                    "invoice": html_escape(result.move_id.display_name),
                },
                subtype_xmlid="mail.mt_note",
            )
        own.write({"note_move_id": False})
        results._mark_exchange_record_retryable()
        return res

    def unlink(self):
        results = self._spms_verification_results()
        res = super().unlink()
        # the database dropped the pointers (ondelete='set null') and the
        # semaphore followed; a result Ready again is held for Retry
        results._mark_exchange_record_retryable()
        return res
