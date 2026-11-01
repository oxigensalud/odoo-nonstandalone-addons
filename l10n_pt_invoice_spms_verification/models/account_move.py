# Copyright 2026 NuoBiT Solutions SL - Eric Antones <eantones@nuobit.com>
# Copyright 2026 NuoBiT Solutions SL - Deniz Gallo <dgallo@nuobit.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class AccountMove(models.Model):
    _inherit = "account.move"

    spms_invoice_verification_ids = fields.One2many(
        comodel_name="spms.invoice.verification",
        inverse_name="move_id",
        string="SPMS Invoice Verifications",
        groups="l10n_pt_invoice_spms_verification.spms_invoice_verification_group_consultation",
    )
    spms_note_invoice_verification_ids = fields.One2many(
        comodel_name="spms.invoice.verification",
        inverse_name="note_move_id",
        string="SPMS Invoice Verifications (Credit/Debit Note)",
        groups="l10n_pt_invoice_spms_verification.spms_invoice_verification_group_consultation",
    )

    def action_view_spms_invoice_verification(self):
        """Open the verification result of the move: the invoice's own, or
        the one that generated the note. An invoice has one at most; a note
        should have one, and several open as a list."""
        self.ensure_one()
        results = (
            self.spms_invoice_verification_ids | self.spms_note_invoice_verification_ids
        )
        if not results:
            action = False
        elif len(results) == 1:
            action = {
                "type": "ir.actions.act_window",
                "name": self.env._("Invoice Verification"),
                "res_model": "spms.invoice.verification",
                "res_id": results.id,
                "view_mode": "form",
            }
        else:
            action = {
                "type": "ir.actions.act_window",
                "name": self.env._("Invoice Verifications"),
                "res_model": "spms.invoice.verification",
                "domain": [("id", "in", results.ids)],
                "view_mode": "list,form",
            }
        return action

    def _spms_verification_results(self):
        """Verification results these moves take part in: the ones whose
        generated note they are, and the ones of the invoices they
        reverse or debit (a foreign note in the eyes of the result)."""
        # Accounting users need to distinguish ordinary moves from SPMS notes.
        # Only resolve the links as sudo; reading/updating results keeps their ACLs.
        moves = self.sudo()
        results = (
            moves.spms_note_invoice_verification_ids
            | moves.reversed_entry_id.spms_invoice_verification_ids
            | moves.debit_origin_id.spms_invoice_verification_ids
        ).with_env(self.env)
        if results:
            results.check_access("read")
        return results

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
        own = results.filtered(lambda result: result.note_move_id in self)
        for result in own:
            # a plain-text body: message_post escapes it itself since 16.0
            result.move_id.message_post(
                body=self.env._(
                    "The generated note %(note)s was cancelled: the verification "
                    "result of invoice %(invoice)s no longer carries a note."
                )
                % {
                    "note": result.note_move_id.display_name,
                    "invoice": result.move_id.display_name,
                },
                subtype_xmlid="mail.mt_note",
            )
        if own:
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
